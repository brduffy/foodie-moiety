"""macOS audio capture via AVAudioEngine with Voice Processing AGC.

Replaces QAudioSource on macOS to fix Bluetooth HFP mic gain degradation
after device switches.  Voice Processing AudioUnit provides hardware-level
AGC that compensates immediately, matching browser behavior.

This module is macOS-only.  Import is guarded by platform check in
voice_service.py.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import warnings

import numpy as np
import objc

log = logging.getLogger(__name__)

# Suppress PyObjCPointer warnings — we handle raw pointers via ctypes
warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)

# ---------------------------------------------------------------------------
# Load AVFAudio framework and register block metadata
# ---------------------------------------------------------------------------

objc.loadBundle(
    "AVFAudio",
    {},
    bundle_path="/System/Library/Frameworks/AVFAudio.framework",
)

# The tap install method takes an ObjC block.  PyObjC needs the block's
# signature registered so it can bridge Python callables correctly.
#   void (^)(AVAudioPCMBuffer *, AVAudioTime *)
objc.registerMetaDataForSelector(
    b"AVAudioNode",
    b"installTapOnBus:bufferSize:format:block:",
    {
        "arguments": {
            5: {  # 0=self, 1=_cmd, 2=bus, 3=size, 4=format, 5=block
                "callable": {
                    "retval": {"type": b"v"},
                    "arguments": {
                        0: {"type": b"^v"},  # block literal
                        1: {"type": b"@"},   # AVAudioPCMBuffer *
                        2: {"type": b"@"},   # AVAudioTime *
                    },
                }
            }
        }
    },
)

AVAudioEngine = objc.lookUpClass("AVAudioEngine")
AVAudioFormat = objc.lookUpClass("AVAudioFormat")


# ---------------------------------------------------------------------------
# MacAudioCapture
# ---------------------------------------------------------------------------

class MacAudioCapture:
    """Captures audio from the default input device using AVAudioEngine.

    Enables Voice Processing (AGC + echo cancellation + noise suppression)
    on the input node.  Delivers 16 kHz mono int16 audio chunks to a
    ``queue.Queue``.

    Parameters
    ----------
    audio_q : queue.Queue
        Queue to push ``numpy.ndarray`` (N, 1) int16 chunks into.
    chunk_size : int
        Target chunk size in samples (default 1280 = 80 ms at 16 kHz).
    """

    SAMPLE_RATE = 16_000

    def __init__(self, audio_q: queue.Queue, chunk_size: int = 1280):
        self._audio_q = audio_q
        self._chunk_size = chunk_size
        self._engine = None
        self._running = False
        self._vp_enabled = False
        self._buffer = bytearray()

    # ----- public API -----

    def start(self) -> bool:
        """Create engine, enable VP + AGC, install tap, start.

        Returns ``True`` on success, ``False`` if voice processing is
        unavailable (caller should fall back to QAudioSource).
        """
        try:
            return self._start_impl()
        except Exception:
            log.exception("AVAudioEngine start failed")
            self._cleanup()
            return False

    def stop(self) -> None:
        """Remove tap, stop engine, release resources."""
        if self._engine is not None:
            try:
                self._engine.inputNode().removeTapOnBus_(0)
            except Exception:
                pass
            try:
                self._engine.stop()
            except Exception:
                pass
        self._cleanup()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def voice_processing_enabled(self) -> bool:
        return self._vp_enabled

    # ----- internals -----

    def _start_impl(self) -> bool:
        engine = AVAudioEngine.alloc().init()
        input_node = engine.inputNode()

        # Enable Voice Processing (AGC + AEC + noise suppression).
        # PyObjC without framework metadata may return bool or (bool, err).
        result = input_node.setVoiceProcessingEnabled_error_(True, None)
        ok = result[0] if isinstance(result, tuple) else result
        if not ok:
            err = result[1] if isinstance(result, tuple) else None
            log.warning("Voice Processing enable failed: %s", err)
            return False
        self._vp_enabled = True

        # Read the post-VP native format — varies by device:
        #   BT headset HFP (ready):   16 kHz / 2 ch / Float32
        #   BT headset HFP (pending): 16 kHz / 3+ ch / Float32 — audio never flows
        #   Built-in mic:             44100 Hz / 4-9 ch / Float32
        native_fmt = input_node.outputFormatForBus_(0)
        self._source_rate = int(native_fmt.sampleRate())
        self._source_channels = int(native_fmt.channelCount())
        self._needs_resample = (self._source_rate != self.SAMPLE_RATE)
        log.info(
            "AVAudioEngine VP native format: %d Hz, %d ch (resample=%s)",
            self._source_rate, self._source_channels, self._needs_resample,
        )

        # 16 kHz with >2 channels means BT HFP profile is still negotiating.
        # The tap will never fire in this state.  Return False so the caller
        # falls back to QAudioSource (which handles HFP transitions gracefully).
        if self._source_rate == self.SAMPLE_RATE and self._source_channels > 2:
            log.warning(
                "VP transitional format (%d ch at %d Hz) — BT HFP not ready, "
                "falling back to QAudioSource",
                self._source_channels, self._source_rate,
            )
            return False

        # Non-16 kHz means a non-BT device (e.g. built-in mic at 44100 Hz).
        # VP adds no value here and causes transitional format delays (4ch/5ch
        # dead ends before settling on 9ch).  Let QAudioSource handle it.
        if self._needs_resample:
            log.info(
                "Non-BT device (%d Hz) — skipping VP, using QAudioSource",
                self._source_rate,
            )
            return False

        # Install tap at native format (None) — Apple requires the tap
        # format to match the node's output sample rate.  We resample
        # to 16 kHz mono in the callback when needed.
        input_node.installTapOnBus_bufferSize_format_block_(
            0,                  # bus
            self._chunk_size,   # hint (engine may deliver larger buffers)
            None,               # native format
            self._tap_callback,
        )

        # Start the engine
        result = engine.startAndReturnError_(None)
        ok = result[0] if isinstance(result, tuple) else result
        if not ok:
            err = result[1] if isinstance(result, tuple) else None
            log.error("AVAudioEngine startAndReturnError failed: %s", err)
            input_node.removeTapOnBus_(0)
            return False

        # AGC must be enabled after engine start — before start it may
        # report False even though the setter was called.
        input_node.setVoiceProcessingAGCEnabled_(True)

        self._engine = engine
        self._running = True
        log.info(
            "AVAudioEngine started: VP=%s, AGC=%s, %d Hz / %d ch",
            self._vp_enabled,
            input_node.isVoiceProcessingAGCEnabled(),
            self._source_rate,
            self._source_channels,
        )
        return True

    def _tap_callback(self, buffer, when):
        """Called on CoreAudio's real-time audio thread.

        Copies float32 channel-0 data, resamples to 16 kHz if needed,
        converts to int16, rechunks to ``_chunk_size`` samples, and
        pushes to the queue.
        """
        try:
            frame_count = buffer.frameLength()
            if frame_count == 0:
                return

            # --- extract float32 channel 0 via ctypes ---
            float_channel_data = buffer.floatChannelData()
            addr = float_channel_data.pointerAsInteger
            float_pp = ctypes.cast(
                addr, ctypes.POINTER(ctypes.POINTER(ctypes.c_float))
            )
            ch0_ptr = float_pp[0]

            # Copy off the RT buffer (CoreAudio reuses it)
            samples = np.ctypeslib.as_array(ch0_ptr, shape=(frame_count,)).copy()

            # Resample to 16 kHz if source rate differs (e.g. built-in 44.1 kHz)
            if self._needs_resample:
                target_len = int(frame_count * self.SAMPLE_RATE / self._source_rate)
                if target_len == 0:
                    return
                x_old = np.linspace(0, 1, frame_count)
                x_new = np.linspace(0, 1, target_len)
                samples = np.interp(x_new, x_old, samples)

            # float32 [-1.0, 1.0] → int16
            int16_arr = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
            raw = int16_arr.tobytes()

            # Accumulate and rechunk to _chunk_size samples
            self._buffer.extend(raw)
            chunk_bytes = self._chunk_size * 2  # int16 = 2 bytes per sample
            while len(self._buffer) >= chunk_bytes:
                chunk = bytes(self._buffer[:chunk_bytes])
                del self._buffer[:chunk_bytes]
                chunk_arr = np.frombuffer(chunk, dtype=np.int16).reshape(-1, 1)
                self._audio_q.put(chunk_arr)

        except Exception:
            # Never let an exception escape the RT callback
            pass

    def _cleanup(self) -> None:
        self._engine = None
        self._running = False
        self._buffer.clear()
