"""Voice service — handles wake word detection, recording, and speech-to-text.

Supports two modes:
1. Push-to-talk: Manual start/stop via keyboard
2. Always-on: Wake word detection + auto-stop on silence
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import queue
import sys
import threading
import time
import types
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject, QThread, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource, QMediaDevices

log = logging.getLogger(__name__)


def _install_av_stub():
    """Prevent faster_whisper from loading PyAV's FFmpeg dylibs.

    Qt multimedia bundles its own FFmpeg; PyAV bundles a second copy.
    Two FFmpeg instances in one process segfault on macOS.  We pass numpy
    arrays directly to model.transcribe(), so decode_audio (the only av
    consumer) is never called.  This stub satisfies the top-level
    ``import av`` in faster_whisper.audio without loading native libs.
    """
    if "av" in sys.modules:
        return
    av_stub = types.ModuleType("av")
    audio_mod = types.ModuleType("av.audio")
    frame_mod = types.ModuleType("av.audio.frame")
    frame_mod.AudioFrame = type("AudioFrame", (), {})
    audio_mod.frame = frame_mod
    av_stub.audio = audio_mod
    for name, mod in [("av", av_stub), ("av.audio", audio_mod),
                      ("av.audio.frame", frame_mod)]:
        sys.modules[name] = mod


_install_av_stub()

# On macOS, use AVAudioEngine with Voice Processing AGC instead of QAudioSource.
# VP provides hardware-level AGC that compensates for Bluetooth HFP gain
# degradation after device switches (35-40s ramp with QAudioSource, instant with VP).
_USE_AVENGINE = platform.system() == "Darwin"
if _USE_AVENGINE:
    try:
        from services.audio_engine_mac import MacAudioCapture
    except Exception:
        _USE_AVENGINE = False

# Audio recording
try:
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError as _e:
    AUDIO_AVAILABLE = False
    np = None
    log.warning("numpy unavailable: %s", _e)

# Speech-to-text (av stub above makes this safe on macOS)
STT_AVAILABLE = importlib.util.find_spec("faster_whisper") is not None
WhisperModel = None  # Resolved on first use via _ensure_whisper_imported()

# Wake word detection
try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    WAKEWORD_AVAILABLE = True
except Exception as _e:
    WAKEWORD_AVAILABLE = False
    log.warning("openwakeword unavailable: %s", _e)

from utils.paths import (
    WHISPER_MODEL as _WHISPER_MODEL_PATH,
    WAKEWORD_MODEL as _WAKEWORD_MODEL_PATH,
    VOSK_MODEL as _VOSK_MODEL_PATH,
)

# Whisper model — "small.en" gives much better accuracy for food names
# (bruschetta, guanciale, etc.) and fewer hallucinations vs "base".
# ~2 GB RAM, ~4x realtime on CPU. Bundled in project models/ directory.
WHISPER_MODEL_PATH = str(_WHISPER_MODEL_PATH)
WAKEWORD_MODEL = _WAKEWORD_MODEL_PATH
VOSK_MODEL_PATH = str(_VOSK_MODEL_PATH)

# Vosk speech recognition (grammar-based, streaming, lightweight)
try:
    from vosk import Model as VoskModel, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)  # Suppress Vosk's verbose Kaldi logging
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False
    log.warning("vosk unavailable")

log.info("Voice capabilities: audio=%s, stt=%s, vosk=%s, wakeword=%s",
         AUDIO_AVAILABLE, STT_AVAILABLE, VOSK_AVAILABLE, WAKEWORD_AVAILABLE)


def _ensure_whisper_imported():
    """Lazy-import faster_whisper on first use."""
    global WhisperModel
    if WhisperModel is None and STT_AVAILABLE:
        try:
            from faster_whisper import WhisperModel as _WM
            WhisperModel = _WM
        except Exception as e:
            log.error("Failed to import faster_whisper: %s", e, exc_info=True)


def _detect_whisper_device() -> tuple[str, str]:
    """Auto-detect the best device/compute_type for faster-whisper.

    Returns (device, compute_type):
      - ("cuda", "float16") if an NVIDIA GPU with CUDA is available
      - ("cpu", "int8") otherwise
    """
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            log.info("CUDA detected — using GPU for Whisper")
            return "cuda", "float16"
    except Exception:
        pass
    log.info("No CUDA — using CPU int8 for Whisper")
    return "cpu", "int8"


class VoiceUnavailableError(Exception):
    """Raised when voice functionality is not available."""
    pass


class _TranscribeWorker(QObject):
    """Worker that transcribes audio off the main thread."""

    finished = Signal(str)  # Transcribed text
    error = Signal(str)

    def __init__(self, model, audio_data: np.ndarray, initial_prompt: str = "",
                 hotwords: str | None = None):
        super().__init__()
        self._model = model
        self._audio_data = audio_data
        self._initial_prompt = initial_prompt
        self._hotwords = hotwords

    def run(self):
        try:
            segments, _ = self._model.transcribe(
                self._audio_data,
                language="en",
                beam_size=5,
                vad_filter=False,
                initial_prompt=self._initial_prompt,
                hotwords=self._hotwords,
                repetition_penalty=1.3,
                no_repeat_ngram_size=3,
            )
            # Filter out segments with high no-speech probability (hallucinations)
            parts = []
            for segment in segments:
                if segment.no_speech_prob < 0.7:
                    parts.append(segment.text.strip())
            text = " ".join(parts)
            self.finished.emit(text)
        except Exception as e:
            self.error.emit(str(e))


class VoiceService(QObject):
    """Handles wake word detection, voice recording, and transcription.

    Signals:
        transcription_ready: Emitted with transcribed text.
        recording_started: Emitted when command recording begins.
        recording_stopped: Emitted when recording ends.
        wake_word_detected: Emitted when wake word is heard.
        listening_started: Emitted when always-on listening begins.
        listening_stopped: Emitted when always-on listening ends.
        error: Emitted on any error.
    """

    transcription_ready = Signal(str)
    recording_started = Signal()
    recording_stopped = Signal()
    wake_word_detected = Signal()
    listening_started = Signal()
    listening_stopped = Signal()
    error = Signal(str)
    followup_started = Signal()  # Emitted when follow-up listening begins
    followup_expired = Signal()  # Emitted when follow-up times out with no speech
    hands_free_changed = Signal(bool)  # Emitted when hands-free mode toggled
    _transcribe_requested = Signal(object)  # Internal: numpy array to transcribe on main thread
    _request_restart = Signal()  # Listen thread requests audio source restart on main thread

    # Audio parameters
    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = "int16"
    CHUNK_SIZE = 1280  # 80ms chunks for wake word (16000 * 0.08)

    # Silence detection parameters
    SILENCE_THRESHOLD = 40  # RMS below this = silence (BT HFP noise floor ~4-5)
    SPEECH_ONSET_THRESHOLD = 30  # RMS above this starts hands-free recording (BT HFP floor ~4-5, degraded speech ~50+)
    MIN_SPEECH_ENERGY = 40  # Minimum peak RMS for a valid recording (BT HFP floor ~4-5, degraded speech ~48+)
    SILENCE_DURATION = 0.8  # Seconds of continuous silence to stop recording
    MIN_RECORDING_TIME = 0.6  # Minimum seconds before silence can stop recording
    MAX_RECORDING_TIME = 15.0  # Maximum recording time in seconds
    MAX_SPEECH_DURATION = 5.0  # Max seconds after speech detected (commands are short)
    SPEECH_GRACE_PERIOD = 3.0  # Seconds to wait for speech before enabling silence detection
    FOLLOWUP_TIMEOUT = 5.0  # Seconds to wait for follow-up command after agent response
    FOLLOWUP_ACTIVITY_THRESHOLD = 20  # RMS above this resets follow-up timer (above ambient noise)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._whisper_model = None
        self._wakeword_model: OWWModel | None = None
        self._model_loaded = False
        self._wakeword_loaded = False

        # Vosk recognizer (streaming, grammar-constrained)
        self._vosk_model = None
        self._vosk_recognizer = None
        self._use_vosk = self._should_use_vosk()

        # Recording state
        self._is_recording = False
        self._is_listening = False
        self._audio_chunks: list = []
        self._onset_streak = 0  # consecutive chunks above SPEECH_ONSET_THRESHOLD

        # QAudioSource (replaces sounddevice/PortAudio)
        self._audio_source: QAudioSource | None = None
        self._audio_io: QIODevice | None = None
        self._audio_buffer = bytearray()
        self._audio_q: queue.Queue | None = None

        # AVAudioEngine capture (macOS only — Voice Processing AGC)
        self._mac_capture = None  # MacAudioCapture | None
        self._ptt_mac_capture = None  # MacAudioCapture | None (PTT mode)
        self._ptt_q: queue.Queue | None = None

        # When True, skip AVAudioEngine VP and use QAudioSource directly.
        # Set during video playback — VP claims BT headset audio output for
        # echo cancellation, blocking QMediaPlayer from sending video audio.
        self._force_qaudio = False

        # Audio flow tracking — patient startup for BT HFP profile negotiation.
        # When a new audio source starts, BT headsets may take 10-40s to settle
        # the HFP profile before audio flows.  Transitional formats (5ch, 7ch)
        # may deliver a brief burst of chunks then go silent.  We require 3s
        # of continuous audio before considering the stream "confirmed live",
        # and wait patiently (no restarts) until then.
        self._audio_flowing = False
        self._source_start_time = 0.0
        self._first_chunk_time = 0.0  # when first chunk arrived (reset on gap)

        # Push-to-talk source (separate from always-on, Windows/fallback)
        self._ptt_source: QAudioSource | None = None
        self._ptt_io: QIODevice | None = None

        # Resampling state — set when device doesn't support 16kHz/mono/Int16
        self._needs_resample = False
        self._source_rate = self.SAMPLE_RATE
        self._source_channels = self.CHANNELS
        self._source_bytes_per_sample = 2
        self._source_np_dtype = np.int16 if np else None

        # Silence detection
        self._silence_start = None
        self._recording_start = None
        self._speech_detected = False
        self._is_followup = False  # True when waiting for follow-up command
        self._hands_free = False  # True when wake word is bypassed
        self._tts = None  # Set by main.py — used to suppress onset during TTS playback
        self._active_view = "recipe_list"  # Current UI view for whisper prompt

        # Background threads
        self._worker_thread: QThread | None = None
        self._worker: _TranscribeWorker | None = None
        self._preload_thread: QThread | None = None
        self._listen_thread: threading.Thread | None = None
        self._stop_listening_flag = False

        # Transcription sequence tracking — detects stale results when a new
        # recording starts before whisper finishes the previous one.
        self._transcription_seq = 0
        self._pending_seq = 0  # Seq of the currently-running whisper worker

        # Queued audio — serialises whisper workers so only one runs at a
        # time.  CTranslate2 (the engine behind faster-whisper) is NOT
        # thread-safe; concurrent transcribe() calls on the same model crash.
        self._queued_audio: np.ndarray | None = None

        # Connect internal signals for cross-thread operations
        self._transcribe_requested.connect(self._transcribe_audio)
        self._request_restart.connect(self.restart_stream)

    @staticmethod
    def _should_use_vosk() -> bool:
        """Check settings for recognizer preference.  Default to Vosk."""
        if not VOSK_AVAILABLE:
            return False
        from PySide6.QtCore import QSettings
        from utils.paths import SETTINGS_PATH
        settings = QSettings(str(SETTINGS_PATH), QSettings.IniFormat)
        return settings.value("Voice/recognizer", "vosk") == "vosk"

    def is_available(self) -> bool:
        """Check if voice functionality is available."""
        if self._use_vosk:
            return AUDIO_AVAILABLE and VOSK_AVAILABLE
        return AUDIO_AVAILABLE and STT_AVAILABLE

    def is_wakeword_available(self) -> bool:
        """Check if wake word detection is available."""
        # For now, use a built-in wake word model from openwakeword
        return AUDIO_AVAILABLE and WAKEWORD_AVAILABLE

    def is_model_loaded(self) -> bool:
        """Check if the Whisper model is loaded."""
        return self._model_loaded

    def is_listening(self) -> bool:
        """Check if always-on listening is active."""
        return self._is_listening

    def preload_model(self) -> None:
        """Load models in the background."""
        if not self.is_available():
            return
        if self._model_loaded:
            return

        # Vosk model loads fast (~100ms) — do it synchronously.
        if self._use_vosk:
            try:
                self._load_vosk_model()
                self._model_loaded = True
                log.info("Vosk model loaded OK")
            except Exception as e:
                log.error("Failed to load Vosk model: %s", e, exc_info=True)
                self.error.emit(f"Failed to load Vosk model: {e}")
            return

        if self._preload_thread is not None and self._preload_thread.isRunning():
            return

        self._preload_thread = QThread()
        worker = _ModelLoader(self)
        worker.moveToThread(self._preload_thread)

        self._preload_thread.started.connect(worker.run)
        worker.finished.connect(self._on_model_loaded)
        worker.finished.connect(self._cleanup_preload_thread)
        worker.error.connect(lambda e: self.error.emit(e))
        worker.error.connect(self._cleanup_preload_thread)

        self._preload_thread.start()

    def _on_model_loaded(self, model) -> None:
        self._whisper_model = model
        self._model_loaded = True

    def _cleanup_preload_thread(self) -> None:
        if self._preload_thread is not None:
            self._preload_thread.quit()
            self._preload_thread.wait(2000)
            self._preload_thread.deleteLater()
            self._preload_thread = None

    def _load_vosk_model(self) -> None:
        """Load Vosk model and create the KaldiRecognizer."""
        from services.vosk_grammars import VIEW_GRAMMARS, RECIPE_DETAIL_GRAMMAR
        log.info("Loading Vosk model from %s", VOSK_MODEL_PATH)
        self._vosk_model = VoskModel(VOSK_MODEL_PATH)
        grammar = VIEW_GRAMMARS.get(self._active_view, RECIPE_DETAIL_GRAMMAR)
        self._vosk_recognizer = KaldiRecognizer(self._vosk_model, self.SAMPLE_RATE, grammar)

    # -------------------------------------------------------------------------
    # Always-on listening mode (wake word + auto-stop)
    # -------------------------------------------------------------------------

    def start_listening(self) -> None:
        """Start always-on listening for wake word."""
        if not self.is_wakeword_available():
            self.error.emit("Wake word detection not available.")
            return

        if self._is_listening:
            return

        self._is_listening = True
        self._stop_listening_flag = False

        # Load wake word model (reuse if already loaded)
        if not self._wakeword_loaded:
            log.info("Loading wake word model from %s (exists=%s)",
                     WAKEWORD_MODEL, WAKEWORD_MODEL.exists())
            try:
                self._wakeword_model = OWWModel(
                    wakeword_models=[str(WAKEWORD_MODEL)],
                    inference_framework="onnx",
                )
                self._wakeword_loaded = True
                log.info("Wake word model loaded OK")
            except Exception as e:
                log.warning("Failed to load wake word model: %s", e)
                self._is_listening = False
                self.error.emit(f"Failed to load wake word model: {e}")
                return

        # Signal UI immediately so the mic button updates without waiting
        # for audio hardware setup.
        self._audio_q = queue.Queue()
        self.listening_started.emit()

        # Defer audio source creation so the event loop can flush the UI
        # repaint before the blocking audio hardware negotiation runs.
        QTimer.singleShot(20, self._finish_start_listening)

    def _finish_start_listening(self) -> None:
        """Complete start_listening after the UI has had a chance to repaint."""
        if not self._is_listening:
            return  # stop_listening() was called before we got here
        self._create_audio_source()
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

    def stop_listening(self) -> None:
        """Stop always-on listening."""
        if not self._is_listening:
            return

        self._stop_listening_flag = True
        self._is_listening = False

        # Also stop any active recording
        if self._is_recording:
            self._is_recording = False

        # Stop audio capture
        self._stop_audio_source()

        # Unblock listen thread with sentinel
        if self._audio_q is not None:
            self._audio_q.put(None)

        # Discard any queued transcription
        self._queued_audio = None

        # Let listen thread exit on its own — it checks _stop_listening_flag
        # and will see the sentinel in the queue.  Avoid blocking the main
        # thread with join() so the UI stays responsive.
        self._listen_thread = None

        self._audio_q = None

        # Clean up preload thread if still running
        if self._preload_thread is not None and self._preload_thread.isRunning():
            self._preload_thread.quit()
            self._preload_thread.wait(2000)

        self.listening_stopped.emit()

    def stop_listening_nonblocking(self) -> None:
        """Signal all threads to stop and wait briefly. For app shutdown."""
        self._stop_listening_flag = True
        self._is_listening = False
        self._is_recording = False
        self._stop_audio_source()
        if self._audio_q is not None:
            self._audio_q.put(None)
        self._queued_audio = None
        if self._preload_thread is not None and self._preload_thread.isRunning():
            self._preload_thread.quit()
            self._preload_thread.wait(2000)
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(2000)

    # -------------------------------------------------------------------------
    # QAudioSource management (main thread)
    # -------------------------------------------------------------------------

    @staticmethod
    def _make_audio_format() -> QAudioFormat:
        fmt = QAudioFormat()
        fmt.setSampleRate(VoiceService.SAMPLE_RATE)
        fmt.setChannelCount(VoiceService.CHANNELS)
        fmt.setSampleFormat(QAudioFormat.Int16)
        return fmt

    def _configure_resample(self, device) -> QAudioFormat:
        """Always use the device's preferred format and resample if needed.

        We do NOT trust ``isFormatSupported()`` — on macOS it claims 16 kHz
        is supported for the built-in mic but then QAudioSource stays in
        IdleState and readyRead never fires.  Using the preferred format
        guarantees CoreAudio delivers audio.
        """
        fmt = device.preferredFormat()
        self._source_rate = fmt.sampleRate()
        self._source_channels = fmt.channelCount()
        sf = fmt.sampleFormat()
        if sf == QAudioFormat.Float:
            self._source_bytes_per_sample = 4
            self._source_np_dtype = np.float32
        elif sf == QAudioFormat.Int32:
            self._source_bytes_per_sample = 4
            self._source_np_dtype = np.int32
        elif sf == QAudioFormat.UInt8:
            self._source_bytes_per_sample = 1
            self._source_np_dtype = np.uint8
        else:  # Int16
            self._source_bytes_per_sample = 2
            self._source_np_dtype = np.int16

        # Only resample if the preferred format differs from our target
        self._needs_resample = (
            self._source_rate != self.SAMPLE_RATE
            or self._source_channels != self.CHANNELS
            or sf != QAudioFormat.Int16
        )
        log.info("Device %s: preferred %dHz/%dch/%s (resample=%s)",
                 device.description(), self._source_rate, self._source_channels,
                 sf, self._needs_resample)
        return fmt

    def _source_chunk_bytes(self) -> int:
        """Bytes per 80ms chunk at the current source format."""
        if not self._needs_resample:
            return self.CHUNK_SIZE * 2  # 1280 samples × 2 bytes (Int16)
        samples = int(self._source_rate * 0.08)  # 80ms at source rate
        return samples * self._source_channels * self._source_bytes_per_sample

    @staticmethod
    def _ensure_max_input_volume() -> None:
        """Set macOS system input volume to 100%.

        Bluetooth HFP mic gain can degrade after device switches.  Maxing
        the system input volume provides a ~33% signal boost that
        compensates for mild degradation.  No-op on non-macOS platforms.
        """
        import platform
        if platform.system() != "Darwin":
            return
        try:
            import subprocess
            subprocess.run(
                ["osascript", "-e", "set volume input volume 100"],
                timeout=2, capture_output=True,
            )
        except Exception:
            pass

    def _create_audio_source(self) -> None:
        """Create and start an audio source for the current default input device.

        On macOS, uses AVAudioEngine with Voice Processing AGC.
        Falls back to QAudioSource on Windows or if VP is unavailable.
        """
        self._stop_audio_source()
        self._ensure_max_input_volume()

        # Reset flow tracking — listen loop will wait patiently for first audio
        # instead of requesting restarts during BT HFP profile negotiation.
        self._audio_flowing = False
        self._source_start_time = time.monotonic()
        self._first_chunk_time = 0.0
        # Drain stale chunks from previous source so they don't falsely
        # set _audio_flowing before the new source delivers anything.
        if self._audio_q is not None:
            while not self._audio_q.empty():
                try:
                    self._audio_q.get_nowait()
                except queue.Empty:
                    break

        if _USE_AVENGINE and not self._force_qaudio:
            self._mac_capture = MacAudioCapture(self._audio_q)
            if self._mac_capture.start():
                return
            log.warning("AVAudioEngine VP failed, falling back to QAudioSource")
            self._mac_capture = None

        # QAudioSource path (Windows, or macOS fallback)
        device = QMediaDevices.defaultAudioInput()
        fmt = self._configure_resample(device)
        self._audio_source = QAudioSource(device, fmt)
        self._audio_source.setBufferSize(self._source_chunk_bytes() * 4)
        self._audio_source.stateChanged.connect(self._on_audio_state_changed)
        self._audio_io = self._audio_source.start()
        if self._audio_io is not None:
            self._audio_io.readyRead.connect(self._on_audio_ready)
            log.info("QAudioSource started: %s (resample=%s)", device.description(),
                     self._needs_resample)
        else:
            log.error("QAudioSource failed to start for: %s (error: %s)",
                      device.description(), self._audio_source.error())

    def _on_audio_ready(self) -> None:
        """Read available audio from QAudioSource and enqueue fixed-size chunks."""
        if self._audio_io is None or self._audio_q is None:
            return
        data = self._audio_io.readAll()
        if data.isEmpty():
            return
        raw = data.data()
        # Align to frame boundary
        frame_size = self._source_channels * self._source_bytes_per_sample
        remainder = len(raw) % frame_size
        if remainder:
            raw = raw[:-remainder]
        if not raw:
            return
        self._audio_buffer.extend(raw)
        chunk_bytes = self._source_chunk_bytes()
        while len(self._audio_buffer) >= chunk_bytes:
            chunk_raw = bytes(self._audio_buffer[:chunk_bytes])
            del self._audio_buffer[:chunk_bytes]
            if self._needs_resample:
                arr = self._resample_chunk(chunk_raw)
            else:
                arr = np.frombuffer(chunk_raw, dtype=np.int16).reshape(-1, 1)
            self._audio_q.put(arr)

    def _on_audio_state_changed(self, state) -> None:
        """Log QAudioSource state transitions for diagnostics."""
        try:
            name = state.name
        except AttributeError:
            name = str(state)
        error_code = self._audio_source.error() if self._audio_source else None
        log.info("QAudioSource state: %s (error: %s)", name, error_code)

    def _resample_chunk(self, raw: bytes) -> np.ndarray:
        """Convert a source-format audio chunk to 16kHz mono Int16 (1280 samples)."""
        arr = np.frombuffer(raw, dtype=self._source_np_dtype)

        # Stereo → mono
        if self._source_channels > 1:
            arr = arr.reshape(-1, self._source_channels).mean(axis=1)

        # Normalize to float64 [-1.0, 1.0]
        if self._source_np_dtype == np.float32:
            samples = arr.astype(np.float64)
        elif self._source_np_dtype == np.int32:
            samples = arr.astype(np.float64) / 2147483648.0
        elif self._source_np_dtype == np.uint8:
            samples = (arr.astype(np.float64) - 128.0) / 128.0
        else:  # int16
            samples = arr.astype(np.float64) / 32768.0

        # Resample to 16kHz if source rate differs
        if self._source_rate != self.SAMPLE_RATE:
            target_len = self.CHUNK_SIZE  # 1280 samples = 80ms at 16kHz
            x_old = np.linspace(0, 1, len(samples))
            x_new = np.linspace(0, 1, target_len)
            samples = np.interp(x_new, x_old, samples)

        # Convert back to Int16
        int16_arr = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
        return int16_arr.reshape(-1, 1)

    def _stop_audio_source(self) -> None:
        """Stop and discard the current audio source."""
        if self._mac_capture is not None:
            self._mac_capture.stop()
            self._mac_capture = None
        if self._audio_source is not None:
            self._audio_source.stop()
            self._audio_source.deleteLater()
            self._audio_source = None
            self._audio_io = None
        self._audio_buffer.clear()

    def set_force_qaudio(self, force: bool) -> None:
        """Force QAudioSource instead of AVAudioEngine VP.

        Used during video playback — VP claims the BT headset's audio
        output for echo cancellation, which blocks QMediaPlayer from
        sending video audio through the headset.
        """
        if force == self._force_qaudio:
            return
        if force:
            # Only restart if AVAudioEngine VP is active — if already on
            # QAudioSource (built-in mic), switching is a no-op.
            need_restart = self._mac_capture is not None
            self._vp_was_active = need_restart
        else:
            # Only restart if VP was active before we forced QAudioSource,
            # so we can restore it.
            need_restart = getattr(self, "_vp_was_active", False)
            self._vp_was_active = False
        self._force_qaudio = force
        log.info("Force QAudioSource: %s (restart=%s)", force, need_restart)
        if self._is_listening and need_restart:
            self.restart_stream()

    def restart_stream(self) -> None:
        """Recreate the QAudioSource for the current default input device.

        Called from main.py when the OS audio device changes, on manual
        refresh, or when the listen thread detects a dead mic (queue timeout).
        """
        if not self._is_listening:
            return
        log.info("Restarting audio source")
        self._create_audio_source()

    _QUEUE_TIMEOUT = 5.0  # seconds to wait for a chunk before declaring dead mic
    _STARTUP_RETRY_INTERVAL = 10.0  # restart every 10s during startup (gives CoreAudio fresh chances)
    _FLOW_CONFIRM_SECS = 3.0  # continuous audio needed before stream is "confirmed live"

    def _listen_loop(self) -> None:
        """Background loop for wake word detection.

        Reads fixed-size audio chunks from ``self._audio_q`` (fed by
        QAudioSource on the main thread) and routes them to the wake-word
        model, speech-onset detector, or recording pipeline.

        A 5-second queue timeout detects dead-mic situations and signals
        the main thread to recreate the QAudioSource.  During startup,
        we retry every 10s (not 5s) to give CoreAudio a fresh chance to
        negotiate the right BT HFP format without excessive churn.
        Transitional formats may deliver a brief burst of chunks then go
        silent — we require 3s of continuous audio before considering the
        stream "confirmed live".
        """
        while not self._stop_listening_flag:
            try:
                audio_chunk = self._audio_q.get(timeout=self._QUEUE_TIMEOUT)
            except queue.Empty:
                if self._stop_listening_flag:
                    return
                if not self._audio_flowing:
                    # Stream not yet confirmed — could be a dead-end format
                    # (e.g. 3ch) or transitional burst that already stopped.
                    # Reset burst tracking and retry after 10s to give
                    # CoreAudio a fresh chance at a working format.
                    self._first_chunk_time = 0.0
                    elapsed = time.monotonic() - self._source_start_time
                    if elapsed < self._STARTUP_RETRY_INTERVAL:
                        continue
                    log.info("No audio for %.0fs — retrying (BT format negotiation)",
                             elapsed)
                else:
                    # Audio was confirmed flowing and stopped — device disconnected.
                    log.warning("No audio for %.0fs — requesting source restart",
                                self._QUEUE_TIMEOUT)
                self._request_restart.emit()
                continue

            # Track flow confirmation — require continuous audio for 3s.
            if not self._audio_flowing:
                now = time.monotonic()
                if self._first_chunk_time == 0.0:
                    self._first_chunk_time = now
                if now - self._first_chunk_time >= self._FLOW_CONFIRM_SECS:
                    self._audio_flowing = True
                    elapsed = now - self._source_start_time
                    log.info("Audio stream confirmed (%.1fs after source start)", elapsed)

            # Sentinel from stop_listening()
            if audio_chunk is None:
                return

            if self._is_recording:
                if self._use_vosk:
                    self._process_vosk_chunk(audio_chunk)
                else:
                    self._process_recording_chunk(audio_chunk)
            elif self._hands_free:
                self._check_speech_onset(audio_chunk)
            else:
                self._check_wake_word(audio_chunk)

    _HEALTH_LOG_INTERVAL = 300  # Log audio health every N chunks (~24 s at 80 ms/chunk)

    def _check_wake_word(self, audio_chunk: np.ndarray) -> None:
        """Check if audio contains wake word."""
        if self._wakeword_model is None:
            return

        # Periodic health check — log RMS so we can verify mic is capturing
        self._ww_chunk_count = getattr(self, "_ww_chunk_count", 0) + 1
        if self._ww_chunk_count % self._HEALTH_LOG_INTERVAL == 0:
            rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2)))
            log.debug("Wake word health: chunk #%d, RMS=%.1f", self._ww_chunk_count, rms)

        try:
            prediction = self._wakeword_model.predict(audio_chunk.flatten())

            for name, score in prediction.items():
                if score > 0.5 and not self._is_recording:
                    log.info("Wake word detected: %s (score=%.3f)", name, score)
                    self._start_command_recording()
                    self.wake_word_detected.emit()
                    break
        except Exception:
            log.exception("Wake word prediction failed")

    def _check_speech_onset(self, audio_chunk: np.ndarray) -> None:
        """Hands-free mode: start recording when speech is detected (no wake word).

        Hands-free requires a Bluetooth headset (enforced by bluetooth.py).
        HFP noise gating keeps the floor at ~4-5 RMS, so a threshold of 30
        is safe (6x above floor) while catching degraded-gain speech at 50+.

        Requires 2 consecutive chunks (160ms) above threshold to filter out
        transient pops/breaths that cause false triggers.
        """
        # Suppress onset while TTS is playing to avoid feedback loop
        if self._tts and self._tts.is_playing:
            self._onset_streak = 0
            return
        if self._is_recording:
            return
        rms = np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2))
        if rms >= self.SPEECH_ONSET_THRESHOLD:
            self._onset_streak += 1
            if self._onset_streak == 1:
                # Save first chunk and its RMS — contains the very start of the word
                self._onset_first_chunk = audio_chunk.copy()
                self._onset_first_rms = rms
            if self._onset_streak >= 2:
                log.info("Hands-free: speech onset detected (rms=%.1f, streak=%d)",
                         rms, self._onset_streak)
                self._onset_streak = 0
                self._start_command_recording()
                if self._use_vosk and self._vosk_recognizer is not None:
                    # Feed onset chunks to Vosk so short commands aren't lost
                    self._vosk_recognizer.AcceptWaveform(
                        self._onset_first_chunk.flatten().tobytes())
                    self._vosk_recognizer.AcceptWaveform(
                        audio_chunk.flatten().tobytes())
                else:
                    # Include both triggering chunks so short commands like
                    # "more" don't lose their first 80-160ms.
                    self._audio_chunks.append(self._onset_first_chunk)
                    self._audio_chunks.append(audio_chunk.copy())
                # Skip Phase 1 — onset already confirmed sustained audio.
                # Phase 2 silence detection handles the rest.  False triggers
                # hit immediate silence → stop in ~1.4s (vs 3s grace period)
                # and the quality gate (peak_speech_rms < 50) discards them.
                self._speech_detected = True
                self._peak_speech_rms = max(self._onset_first_rms, rms)
        else:
            self._onset_streak = 0

    def set_hands_free(self, enabled: bool) -> None:
        """Toggle hands-free mode (no wake word required)."""
        self._hands_free = enabled
        self.hands_free_changed.emit(enabled)

    def set_active_view(self, view: str) -> None:
        """Set the current UI view for context-aware recognition."""
        self._active_view = view
        if self._use_vosk and self._vosk_model is not None:
            from services.vosk_grammars import VIEW_GRAMMARS
            grammar = VIEW_GRAMMARS.get(view)
            if grammar:
                self._vosk_recognizer = KaldiRecognizer(
                    self._vosk_model, self.SAMPLE_RATE, grammar)

    # View-specific whisper config — biases transcription toward commands.
    # initial_prompt: soft bias via decoder conditioning (vocabulary hint)
    # hotwords: beam search biasing (individual word probabilities)
    # Trailing silence is trimmed before sending to Whisper so the prompt
    # won't cause hallucination on long silent tails.
    # Recipe list has no voice commands — touch is natural there.
    _GLOBAL_PROMPT = (
        "pause listening, resume listening, "
        "disable voice responses, enable voice responses"
    )

    _VIEW_CONFIG = {
        "recipe_detail": {
            "prompt": (
                # Navigation
                "next, previous, intro, step, "
                # Scroll
                "more, less, "
                "more ingredients, more directions, less ingredients, less directions, "
                # View switching
                "show, ingredients, directions, image, details, "
                "ingredients and directions, "
                # Scaling
                "scale by, half, quarter, "
                # Font
                "bigger font, smaller font, max font, min font, "
                # Open video
                "play video, "
                # Global
                "commands, close, dismiss, "
                + _GLOBAL_PROMPT
            ),
            "hotwords": (
                # Navigation + step numbers
                "next previous intro step "
                "one two three four five six seven eight nine ten "
                # Scroll
                "more less "
                # View switching
                "show ingredients directions image details "
                # Scaling
                "scale half quarter double "
                # Font
                "bigger smaller max min font "
                # Open video
                "play video "
                # Global
                "commands close dismiss "
                "pause resume listening "
                "enable disable voice responses"
            ),
        },
        "video_player": {
            "prompt": (
                # Playback
                "stop, pause, play, "
                # Seek
                "skip back, skip forward, "
                # Volume
                "mute, unmute, "
                # Navigation
                "next, previous, step, "
                # Global
                "commands, close, dismiss, "
                + _GLOBAL_PROMPT
            ),
            "hotwords": (
                # Playback
                "stop pause play "
                # Seek
                "skip back forward "
                # Volume
                "mute unmute "
                # Navigation + step numbers
                "next previous step "
                "one two three four five six seven eight nine ten "
                # Global
                "commands close dismiss "
                "pause resume listening "
                "enable disable voice responses"
            ),
        },
    }

    def _start_command_recording(self) -> None:
        """Start recording after wake word detected."""
        import time
        self._audio_chunks = []
        self._is_recording = True
        self._is_followup = False
        self._silence_start = None
        self._recording_start = time.time()
        self._speech_detected = False
        self._recording_chunk_count = 0
        self._peak_speech_rms = 0.0
        self._onset_continuation_seen = False  # hands-free: any Phase 2 chunk above noise?
        self._transcription_seq += 1

        log.info("Recording started (seq=%d, view=%s, hands_free=%s, vosk=%s)",
                 self._transcription_seq, self._active_view, self._hands_free,
                 self._use_vosk)

        # Reset Vosk recognizer state for fresh utterance
        if self._use_vosk and self._vosk_recognizer is not None:
            self._vosk_recognizer.FinalResult()  # Drains and resets

        # Reset wake word model to prevent repeated detections
        if self._wakeword_model is not None:
            self._wakeword_model.reset()

        self.recording_started.emit()

    def start_followup_listening(self) -> None:
        """Start listening for a follow-up command without requiring wake word.

        Called after the agent finishes processing a voice command.
        Waits up to FOLLOWUP_TIMEOUT seconds for the user to start speaking.
        If no speech detected, silently returns to wake word mode.
        """
        if not self._is_listening or self._is_recording:
            return

        import time
        self._audio_chunks = []
        self._is_recording = True
        self._is_followup = True
        self._silence_start = None
        self._recording_start = time.time()
        self._speech_detected = False

        # Reset wake word model to avoid stale state
        if self._wakeword_model is not None:
            self._wakeword_model.reset()

        self.followup_started.emit()

    def _process_vosk_chunk(self, audio_chunk: np.ndarray) -> None:
        """Feed audio to Vosk recognizer in streaming mode.

        Vosk handles endpointing internally — AcceptWaveform() returns True
        when the user stops speaking.  No onset detection, silence tracking,
        or background transcription thread is needed.
        """
        import json
        import time

        # Load on-demand if preload_model wasn't called
        if self._vosk_recognizer is None:
            try:
                self._load_vosk_model()
            except Exception as e:
                log.error("Failed to load Vosk model: %s", e)
                self._is_recording = False
                self.error.emit(f"Failed to load Vosk model: {e}")
                return

        elapsed = time.time() - self._recording_start

        # Follow-up timeout — no speech detected within window
        if self._is_followup and elapsed >= self.FOLLOWUP_TIMEOUT:
            self._vosk_recognizer.FinalResult()  # Reset state
            self._is_recording = False
            self._is_followup = False
            self.followup_expired.emit()
            return

        # Hard cap on recording time
        if elapsed >= self.MAX_RECORDING_TIME:
            result = json.loads(self._vosk_recognizer.FinalResult())
            text = result.get("text", "").strip()
            self._is_recording = False
            self._is_followup = False
            self.recording_stopped.emit()
            if text and text != "[unk]":
                log.info("Vosk result (max time, %.2fs): %r", elapsed, text)
                self.transcription_ready.emit(text)
            else:
                self.error.emit("")
            return

        # Feed raw int16 bytes to Vosk
        audio_bytes = audio_chunk.flatten().tobytes()
        if self._vosk_recognizer.AcceptWaveform(audio_bytes):
            # Utterance complete (Vosk's endpointer detected silence)
            result = json.loads(self._vosk_recognizer.Result())
            text = result.get("text", "").strip()
            self._is_recording = False
            self._is_followup = False
            self.recording_stopped.emit()

            if text and text != "[unk]":
                log.info("Vosk result (%.2fs): %r", elapsed, text)
                self.transcription_ready.emit(text)
            else:
                log.info("Vosk: no valid speech (text=%r, %.2fs)", text, elapsed)
                self.error.emit("")

    def _process_recording_chunk(self, audio_chunk: np.ndarray) -> None:
        """Process audio chunk during command recording."""
        import time

        self._recording_chunk_count = getattr(self, "_recording_chunk_count", 0) + 1
        elapsed = time.time() - self._recording_start
        if elapsed >= self.MAX_RECORDING_TIME:
            log.warning("Recording hit MAX_RECORDING_TIME (%.1fs, %d chunks)",
                        elapsed, self._recording_chunk_count)
            self._audio_chunks.append(audio_chunk.copy())
            self._stop_recording_internal()
            return

        rms = np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2))

        # Log every ~0.5s (6 chunks at 80ms) during recording for diagnostics
        if self._recording_chunk_count % 6 == 0:
            log.debug("Recording chunk #%d: elapsed=%.2fs rms=%.1f speech=%s silence_start=%s",
                      self._recording_chunk_count, elapsed, rms,
                      self._speech_detected, self._silence_start)

        # --- Phase 1: Wait for speech to begin ---
        # Don't append silent chunks — they bloat the recording with seconds
        # of dead air that whisper has to scan through, adding latency.
        if not self._speech_detected:
            if rms >= self.SILENCE_THRESHOLD:
                self._speech_detected = True
                log.info("Phase 1→2: speech detected at %.2fs (rms=%.1f, chunk #%d)",
                         elapsed, rms, self._recording_chunk_count)
                self._audio_chunks.append(audio_chunk.copy())
                # Fall through to phase 2 for this chunk
            elif self._is_followup:
                # In follow-up mode: extend timeout if any audio activity detected
                if rms >= self.FOLLOWUP_ACTIVITY_THRESHOLD:
                    self._recording_start = time.time() - (self.FOLLOWUP_TIMEOUT - 3.0)
                # Check hard timeout
                if elapsed >= self.FOLLOWUP_TIMEOUT:
                    self._is_recording = False
                    self._is_followup = False
                    self._audio_chunks = []
                    self.followup_expired.emit()
                return
            elif elapsed >= self.SPEECH_GRACE_PERIOD:
                # Grace period expired with no speech — discard phantom recording.
                # Emit error("") instead of recording_stopped so main.py dismisses
                # the voice indicator immediately rather than showing PROCESSING
                # for the 10-second safety timeout.
                log.info("Phase 1 grace period expired with no speech — discarding")
                self._is_recording = False
                self._audio_chunks = []
                self.error.emit("")
                return
            else:
                return
        else:
            self._audio_chunks.append(audio_chunk.copy())

        # --- Phase 2: Record until sustained silence ---
        # Track peak speech energy across ALL phase 2 chunks — must be before
        # the MIN_RECORDING_TIME gate because in hands-free mode the command
        # word is spoken immediately and may finish before 0.6s.
        self._peak_speech_rms = max(self._peak_speech_rms, rms)

        # Hands-free continuation check: onset confirmed 160ms of energy,
        # but we need speech to CONTINUE beyond the onset.  If 0.3s passes
        # after onset with no chunk above 15 RMS (just above HFP noise
        # floor), the onset was a transient — abort immediately.
        if self._hands_free and not self._onset_continuation_seen:
            if rms >= 15:
                self._onset_continuation_seen = True
            elif elapsed >= 0.3:
                log.info("Hands-free: no speech continuation after onset — aborting (rms=%.1f)", rms)
                self._is_recording = False
                self._audio_chunks = []
                self.error.emit("")
                return

        # Don't stop before minimum recording time
        if elapsed < self.MIN_RECORDING_TIME:
            self._silence_start = None
            return

        # Hard cap — voice commands are at most a few words long
        if elapsed >= self.MAX_SPEECH_DURATION:
            log.info("Max speech duration (%.1fs) — auto-stopping", elapsed)
            self._stop_recording_internal()
            return

        # Adaptive silence: in noisy kitchens ambient RMS can stay above
        # SILENCE_THRESHOLD (40) indefinitely.  Also detect silence as a
        # significant drop from peak speech energy (< 10% of peak).
        adaptive = self._peak_speech_rms * 0.1
        effective_threshold = max(self.SILENCE_THRESHOLD, adaptive)
        if rms < effective_threshold:
            if self._silence_start is None:
                self._silence_start = time.time()
            elif time.time() - self._silence_start >= self.SILENCE_DURATION:
                self._stop_recording_internal()
        else:
            self._silence_start = None

    def _stop_recording_internal(self) -> None:
        """Stop recording and transcribe (called from listen thread)."""
        import time
        self._is_recording = False
        self._is_followup = False
        duration = time.time() - self._recording_start if self._recording_start else 0
        chunk_count = getattr(self, "_recording_chunk_count", 0)
        audio_chunk_count = len(self._audio_chunks)
        log.info("Recording stopped: total=%.2fs, %d chunks processed, %d audio chunks saved",
                 duration, chunk_count, audio_chunk_count)
        self.recording_stopped.emit()

        if not self._audio_chunks:
            log.warning("No audio chunks to transcribe — empty recording")
            self.error.emit("")  # Dismiss indicator — no transcription will follow
            return

        audio_data = np.concatenate(self._audio_chunks, axis=0)
        self._audio_chunks = []

        # Trim trailing silence — recordings accumulate 0.8-1.4s of silence
        # waiting for silence detection.  Sending mostly-silent audio to
        # Whisper causes hallucination (YouTube captions, random words).
        # Keep speech + 200ms buffer.  Use a LOW threshold (10) — just above
        # the HFP noise floor (~4-5 RMS) — to preserve quiet speech tails
        # that are well below SILENCE_THRESHOLD with degraded mic gain.
        _TRIM_THRESHOLD = 10
        _TRIM_BUFFER = int(self.SAMPLE_RATE * 0.2)  # 200ms
        chunk_sz = self.CHUNK_SIZE  # 1280 samples = 80ms
        flat = audio_data.flatten()
        last_speech_idx = 0
        for i in range(0, len(flat), chunk_sz):
            chunk_slice = flat[i:i + chunk_sz]
            rms_val = float(np.sqrt(np.mean(chunk_slice.astype(np.float32) ** 2)))
            if rms_val >= _TRIM_THRESHOLD:
                last_speech_idx = i + len(chunk_slice)
        trim_end = min(last_speech_idx + _TRIM_BUFFER, len(flat))
        if trim_end < len(flat):
            trimmed = flat[:trim_end].reshape(-1, 1)
            log.info("Trimmed trailing silence: %.2fs → %.2fs",
                     len(flat) / self.SAMPLE_RATE, len(trimmed) / self.SAMPLE_RATE)
            audio_data = trimmed

        audio_duration = len(audio_data) / self.SAMPLE_RATE

        # Quality gate — reject pure noise but allow quiet-but-real speech.
        # Use peak speech RMS (tracked during recording) rather than overall
        # RMS, which is diluted by silence padding and misleadingly low when
        # the Bluetooth HFP mic gain is degraded after a device switch.
        overall_rms = float(np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)))
        peak_rms = getattr(self, "_peak_speech_rms", 0.0)
        log.info("Audio for whisper: %.2fs (%d samples, rms=%.1f, peak=%.1f)",
                 audio_duration, len(audio_data), overall_rms, peak_rms)
        if peak_rms < self.MIN_SPEECH_ENERGY:
            log.info("Recording too quiet (peak_rms=%.1f < %d) — discarding",
                     peak_rms, self.MIN_SPEECH_ENERGY)
            self.error.emit("")  # Dismiss indicator — no transcription will follow
            return

        # Gain normalization — compensate for degraded Bluetooth HFP mic gain
        # after device switches.  CoreAudio corrupts the gain when switching
        # away from a BT headset and back; the audio is real but very quiet.
        _TARGET_RMS = 300.0
        if overall_rms > 5 and overall_rms < _TARGET_RMS * 0.5:
            gain = min(_TARGET_RMS / overall_rms, 10.0)
            audio_data = np.clip(
                audio_data.astype(np.float32) * gain, -32768, 32767
            ).astype(np.int16)
            log.info("Gain normalization: %.1fx (rms %.1f → ~%.1f)",
                     gain, overall_rms, overall_rms * gain)

        self._transcribe_start = time.time()
        log.info("Transcription request seq=%d", self._transcription_seq)
        self._transcribe_requested.emit(audio_data)

    # -------------------------------------------------------------------------
    # Push-to-talk mode (manual start/stop)
    # -------------------------------------------------------------------------

    def start_recording(self) -> None:
        """Start recording audio (push-to-talk mode)."""
        if not self.is_available():
            self.error.emit("Voice recording not available.")
            return

        if self._is_recording:
            return

        self._audio_chunks = []
        self._audio_buffer.clear()
        self._is_recording = True

        try:
            if _USE_AVENGINE:
                self._ptt_q = queue.Queue()
                self._ptt_mac_capture = MacAudioCapture(self._ptt_q)
                if self._ptt_mac_capture.start():
                    self.recording_started.emit()
                    return
                # VP failed — fall through to QAudioSource
                self._ptt_mac_capture = None
                self._ptt_q = None

            device = QMediaDevices.defaultAudioInput()
            fmt = self._configure_resample(device)
            self._ptt_source = QAudioSource(device, fmt)
            self._ptt_source.setBufferSize(self._source_chunk_bytes() * 4)
            self._ptt_io = self._ptt_source.start()
            if self._ptt_io is not None:
                self._ptt_io.readyRead.connect(self._on_ptt_audio_ready)
            self.recording_started.emit()
        except Exception as e:
            self._is_recording = False
            self.error.emit(f"Failed to start recording: {e}")

    def _on_ptt_audio_ready(self) -> None:
        """Read audio from push-to-talk QAudioSource into _audio_chunks."""
        if not self._is_recording or self._ptt_io is None:
            return
        data = self._ptt_io.readAll()
        if data.isEmpty():
            return
        raw = data.data()
        # Align to frame boundary
        frame_size = self._source_channels * self._source_bytes_per_sample
        remainder = len(raw) % frame_size
        if remainder:
            raw = raw[:-remainder]
        if not raw:
            return
        if self._needs_resample:
            self._audio_buffer.extend(raw)
            chunk_bytes = self._source_chunk_bytes()
            while len(self._audio_buffer) >= chunk_bytes:
                chunk_raw = bytes(self._audio_buffer[:chunk_bytes])
                del self._audio_buffer[:chunk_bytes]
                arr = self._resample_chunk(chunk_raw)
                self._audio_chunks.append(arr)
        else:
            arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, 1)
            self._audio_chunks.append(arr)

    def stop_recording(self) -> None:
        """Stop recording and transcribe (push-to-talk mode)."""
        if not self._is_recording:
            return

        self._is_recording = False

        # Stop AVAudioEngine PTT capture and drain queue
        if self._ptt_mac_capture is not None:
            self._ptt_mac_capture.stop()
            self._ptt_mac_capture = None
            if self._ptt_q is not None:
                while not self._ptt_q.empty():
                    try:
                        chunk = self._ptt_q.get_nowait()
                        self._audio_chunks.append(chunk)
                    except queue.Empty:
                        break
                self._ptt_q = None

        if self._ptt_source is not None:
            self._ptt_source.stop()
            self._ptt_source.deleteLater()
            self._ptt_source = None
            self._ptt_io = None

        self.recording_stopped.emit()

        if not self._audio_chunks:
            self.error.emit("No audio recorded.")
            return

        audio_data = np.concatenate(self._audio_chunks, axis=0)
        self._audio_chunks = []

        if self._use_vosk and self._vosk_recognizer is not None:
            import json
            audio_bytes = audio_data.flatten().tobytes()
            self._vosk_recognizer.AcceptWaveform(audio_bytes)
            result = json.loads(self._vosk_recognizer.FinalResult())
            text = result.get("text", "").strip()
            if text and text != "[unk]":
                log.info("Vosk PTT result: %r", text)
                self.transcription_ready.emit(text)
            else:
                self.error.emit("")
        else:
            self._transcribe_audio(audio_data)

    # -------------------------------------------------------------------------
    # Transcription
    # -------------------------------------------------------------------------

    def _transcribe_audio(self, audio_data: np.ndarray) -> None:
        """Transcribe audio using Whisper (in-process, all platforms).

        *audio_data* is int16 mono 16 kHz — converted to float32 here.
        """
        if not STT_AVAILABLE:
            self.error.emit("faster-whisper not installed.")
            return

        # Guard: if whisper is already busy, queue this request.
        # CTranslate2 is NOT thread-safe — two concurrent transcribe() calls
        # on the same WhisperModel cause a native crash (segfault).
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._queued_audio = audio_data
            log.info("Whisper busy (worker seq=%d) — queued new audio",
                     self._pending_seq)
            return

        # Load model on-demand if not preloaded
        _ensure_whisper_imported()
        if self._whisper_model is None:
            try:
                device, compute_type = _detect_whisper_device()
                log.info("Loading Whisper model: path=%s, device=%s, compute=%s",
                         WHISPER_MODEL_PATH, device, compute_type)
                self._whisper_model = WhisperModel(
                    WHISPER_MODEL_PATH,
                    device=device,
                    compute_type=compute_type,
                )
                self._model_loaded = True
                log.info("Whisper model loaded OK")
            except Exception as e:
                log.error("Failed to load Whisper model: %s", e, exc_info=True)
                self.error.emit(f"Failed to load Whisper model: {e}")
                return

        # Convert int16 → float32 normalised to [-1, 1] for Whisper
        audio_float = audio_data.flatten().astype(np.float32) / 32768.0

        # Run transcription in background thread
        self._cleanup_worker_thread()
        self._pending_seq = self._transcription_seq
        cfg = self._VIEW_CONFIG.get(self._active_view, {})
        prompt = cfg.get("prompt", "")
        hotwords = cfg.get("hotwords")
        self._worker_thread = QThread()
        self._worker = _TranscribeWorker(
            self._whisper_model, audio_float, prompt, hotwords=hotwords
        )
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_transcription_done)
        self._worker.error.connect(lambda e: self.error.emit(e))
        self._worker.finished.connect(self._cleanup_worker_thread)
        self._worker.error.connect(self._cleanup_worker_thread)

        self._worker_thread.start()

    def _on_transcription_done(self, text: str) -> None:
        """Handle completed transcription."""
        import time
        whisper_time = time.time() - getattr(self, "_transcribe_start", time.time())
        log.info("Whisper result (seq=%d, %.2fs): %r",
                 self._pending_seq, whisper_time, text.strip()[:80])

        # Discard stale results — a new recording started before whisper finished
        if self._pending_seq != self._transcription_seq:
            log.warning("Discarding stale transcription (seq=%d, current=%d): %r",
                        self._pending_seq, self._transcription_seq, text.strip()[:40])
            self.error.emit("")
            return

        cleaned = text.strip()
        if not cleaned:
            log.info("Whisper returned empty text — no speech detected")
            self.error.emit("")
            return

        # Detect whisper hallucination — repetitive output from prompt seeding
        if self._is_hallucination(cleaned):
            log.warning("Whisper hallucination detected, discarding: %r", cleaned[:60])
            self.error.emit("")
            return

        self.transcription_ready.emit(cleaned)

    # Known Whisper hallucination phrases — prompt regurgitation and
    # YouTube training data leaks.  These never appear in real commands.
    _HALLUCINATION_PHRASES = [
        "pause resume listening",
        "disable voice responses next",
        "enable voice responses next",
        "like and subscribe",
        "link in the description",
        "thanks for watching",
    ]

    @classmethod
    def _is_hallucination(cls, text: str) -> bool:
        """Detect whisper hallucinations: repetition loops and prompt regurgitation.

        When whisper gets ambiguous audio and an initial_prompt with command
        words, it can loop on a single token (e.g. "mute mute mute..." x200)
        or regurgitate the prompt text.  Voice commands are at most ~10 words.
        """
        lower = text.lower()
        words = lower.split()
        if len(words) > 20:
            return True
        if len(words) >= 5:
            from collections import Counter
            counts = Counter(words)
            most_common_count = counts.most_common(1)[0][1]
            if most_common_count > len(words) * 0.6:
                return True
        # Prompt regurgitation — Whisper outputs the initial_prompt text
        for phrase in cls._HALLUCINATION_PHRASES:
            if phrase in lower:
                return True
        return False

    def _cleanup_worker_thread(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(2000)
            self._worker_thread.deleteLater()
            self._worker_thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        # Process queued transcription now that the worker is free
        if self._queued_audio is not None:
            data = self._queued_audio
            self._queued_audio = None
            log.info("Processing queued transcription")
            self._transcribe_audio(data)


class _ModelLoader(QObject):
    """Worker that loads the Whisper model off the main thread."""

    finished = Signal(object)  # WhisperModel
    error = Signal(str)

    def __init__(self, service: VoiceService):
        super().__init__()
        self._service = service

    def run(self):
        try:
            _ensure_whisper_imported()
            device, compute_type = _detect_whisper_device()
            model = WhisperModel(
                WHISPER_MODEL_PATH,
                device=device,
                compute_type=compute_type,
            )
            self.finished.emit(model)
        except Exception as e:
            self.error.emit(f"Failed to load Whisper model: {e}")
