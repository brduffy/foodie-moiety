"""Text-to-speech service using Piper TTS.

Piper is a local ONNX neural voice engine — good quality, fully offline.
Audio is played via QAudioSink (Qt multimedia) on the main thread with
silence prepended to absorb Bluetooth audio startup latency.
"""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

log = logging.getLogger(__name__)

try:
    from piper import PiperVoice
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False

from utils.paths import PIPER_ONNX

# Piper voice model — ships with the app in models/tts/
_PIPER_ONNX = PIPER_ONNX

# Silence prepended to the audio numpy array before playback.
# Bluetooth A2DP codecs need ~1-1.5s to wake the audio stream.  The silence
# is part of the same continuous stream so the codec receives it and finishes
# negotiating before the real speech begins.
# Tested with Braven BT speaker: 800ms too short, 1500ms mostly reliable,
# 2000ms adds margin for inconsistent BT codec wake times.
_SILENCE_MS = 2000


class TTSService(QObject):
    """Speaks text using Piper TTS on a background thread.

    Audio generation happens on a background thread via Piper.
    Playback uses QAudioSink on the main thread, which automatically
    routes to the current default audio output device.
    Silence is prepended to each utterance so Bluetooth speakers
    have time to wake their A2DP codec before speech begins.
    """

    speech_finished = Signal()  # Emitted when an utterance finishes (not when cancelled)
    _play_signal = Signal(bytes, int)  # (audio_bytes, sample_rate) — cross-thread

    _SENTINEL = object()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._piper_voice: PiperVoice | None = None
        self._tmp_wav = os.path.join(tempfile.gettempdir(), "foodie_tts.wav")
        self._interrupted = False
        self.is_playing = False

        # QAudioSink playback state (main thread only)
        self._audio_sink: QAudioSink | None = None
        self._audio_buffer: QBuffer | None = None
        self._playback_done = threading.Event()
        self._playback_done.set()  # Initially "done"

        self._play_signal.connect(self._start_playback)

        if PIPER_AVAILABLE:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        """Queue *text* to be spoken, replacing any pending speech."""
        if self._thread is None:
            return
        # Clear pending items so only the latest text is spoken
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        # Stop any in-progress playback
        self._interrupted = True
        self._stop_playback()
        self._queue.put(self._format_for_speech(text))

    @staticmethod
    def _format_for_speech(text: str) -> str:
        """Convert display-formatted text into TTS-friendly sentences.

        Workflow responses are formatted for visual display (newlines,
        indentation, labels).  Piper reads periods as sentence breaks
        with natural pauses, so we convert each line into a sentence.
        """
        lines = text.splitlines()
        sentences = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Drop visual-only labels that add nothing when spoken
            if line.lower() in ("breakdown by step:", "breakdown:"):
                continue
            # Ensure line ends with sentence-ending punctuation
            if not line.endswith((".", "!", "?")):
                line += "."
            sentences.append(line)
        return " ".join(sentences)

    def cancel(self) -> None:
        """Cancel current and pending speech without shutting down."""
        self.is_playing = False
        self._interrupted = True
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._stop_playback()

    def stop(self) -> None:
        """Stop speech and shut down the worker thread."""
        self._stop_playback()
        if self._thread is None:
            return
        self._queue.put(self._SENTINEL)
        self._thread.join(timeout=5.0)
        self._thread = None
        try:
            os.remove(self._tmp_wav)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Playback — main thread via QAudioSink
    # ------------------------------------------------------------------

    def _start_playback(self, audio_bytes: bytes, sample_rate: int) -> None:
        """Slot called on main thread when audio is ready to play."""
        if self._interrupted:
            self.is_playing = False
            self._playback_done.set()
            return

        fmt = QAudioFormat()
        fmt.setSampleRate(sample_rate)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        device = QMediaDevices.defaultAudioOutput()
        self._audio_sink = QAudioSink(device, fmt)
        self._audio_sink.stateChanged.connect(self._on_sink_state_changed)

        self._audio_buffer = QBuffer()
        self._audio_buffer.setData(QByteArray(audio_bytes))
        self._audio_buffer.open(QIODevice.OpenModeFlag.ReadOnly)

        self._audio_sink.start(self._audio_buffer)

    def _on_sink_state_changed(self, state: QAudio.State) -> None:
        """Handle QAudioSink state transitions."""
        if state == QAudio.State.IdleState:
            # All data consumed — playback finished
            self._cleanup_sink()
            self.is_playing = False
            if not self._interrupted:
                self.speech_finished.emit()
            self._playback_done.set()
        elif state == QAudio.State.StoppedState:
            if self._audio_sink:
                err = self._audio_sink.error()
                if err != QAudio.Error.NoError:
                    log.warning("QAudioSink error: %s", err)
            self._cleanup_sink()
            self.is_playing = False
            self._playback_done.set()

    def _stop_playback(self) -> None:
        """Stop current playback and unblock the background thread."""
        if self._audio_sink is not None:
            self._audio_sink.stop()
        self._playback_done.set()

    def _cleanup_sink(self) -> None:
        """Release QAudioSink and QBuffer resources."""
        if self._audio_sink is not None:
            try:
                self._audio_sink.stateChanged.disconnect(self._on_sink_state_changed)
            except RuntimeError:
                pass  # Already disconnected
            self._audio_sink = None
        if self._audio_buffer is not None:
            self._audio_buffer.close()
            self._audio_buffer = None

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        self._load_piper_model()
        if self._piper_voice is None:
            log.warning("No TTS engine available — speech disabled")
            return

        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                break
            try:
                self._generate_and_play(item)
            except Exception:
                log.debug("Piper TTS failed for: %s", item[:40])

    def _load_piper_model(self) -> None:
        """Load the Piper ONNX voice model from models/tts/."""
        if not _PIPER_ONNX.exists():
            log.warning("Piper model not found: %s", _PIPER_ONNX)
            return
        try:
            self._piper_voice = PiperVoice.load(str(_PIPER_ONNX))
            log.info("Piper TTS model loaded")
        except Exception as exc:
            log.warning("Failed to load Piper model: %s", exc)

    def _generate_and_play(self, text: str) -> None:
        """Generate WAV with Piper, prepend silence, signal main thread to play."""
        self._interrupted = False
        self.is_playing = True
        self._playback_done.clear()

        with wave.open(self._tmp_wav, "wb") as wf:
            self._piper_voice.synthesize_wav(text, wf)

        with wave.open(self._tmp_wav, "rb") as rf:
            sample_rate = rf.getframerate()
            n_channels = rf.getnchannels()
            raw_data = rf.readframes(rf.getnframes())

        audio = np.frombuffer(raw_data, dtype=np.int16)
        if n_channels > 1:
            audio = audio.reshape(-1, n_channels)[:, 0]  # Take first channel

        # Pad with silence on both sides:
        # - Leading: lets BT codec finish A2DP negotiation before speech
        # - Trailing: ensures last word flushes through BT buffer before
        #   the stream closes
        silence_samples = int(sample_rate * _SILENCE_MS / 1000)
        tail_samples = int(sample_rate * 0.5)  # 500ms trailing silence
        silence = np.zeros(silence_samples, dtype=np.int16)
        tail = np.zeros(tail_samples, dtype=np.int16)
        audio = np.concatenate([silence, audio, tail])

        if self._interrupted:
            self.is_playing = False
            self._playback_done.set()
            return

        # Signal main thread to play via QAudioSink
        self._play_signal.emit(audio.tobytes(), sample_rate)

        # Block until playback completes or is cancelled
        self._playback_done.wait()
