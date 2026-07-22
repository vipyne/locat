"""Phase 2 standalone AEC prototype: measure macOS VPIO echo cancellation from Python.

Builds ONE AVAudioEngine, enables voice processing (unless --no-vp), plays a known
speech clip (generated offline via macOS `say`) through the speakers via an
AVAudioPlayerNode on that same engine, taps the input node, and reports:

  - baseline mic RMS (room noise, before playback starts)
  - playback-window mic RMS (what the mic hears while the clip plays)
  - echo-over-noise ratio in dB  (the A/B number: compare VP on vs VP off runs)
  - tap cadence stats (callback count, mean/max gap) — Phase 2 unknown (a)

Captured channel-0 audio is saved to a WAV for listening/inspection.

Run (HUMAN-GATED — needs built-in mic + built-in speakers, quiet room, mic TCC grant):
    uv run python aec_prototype.py                 # VP ON  -> capture_vp-on.wav
    uv run python aec_prototype.py --no-vp         # VP OFF -> capture_vp-off.wav

Hardware-free self-test (no engine start, no mic, safe anywhere):
    uv run python aec_prototype.py --selftest

Known constraint (see SPIKE_PROGRESS.md): the process may hang in HAL teardown on
interpreter exit, so we print results, stop the engine explicitly, then os._exit(0).
"""

import argparse
import math
import os
import struct
import subprocess
import sys
import threading
import time
import wave

import AVFoundation
import Foundation

HERE = os.path.dirname(os.path.abspath(__file__))
CLIP_PATH = os.path.join(HERE, "clip.aiff")
CLIP_TEXT = (
    "This is the voice processing echo test. "
    "The quick brown fox jumps over the lazy dog. "
    "Counting one, two, three, four, five."
)

# AVAudioPlayerNodeBufferLoops == 1 (loop the clip until stopped)
BUFFER_LOOPS = getattr(AVFoundation, "AVAudioPlayerNodeBufferLoops", 1)


# ---------------------------------------------------------------- PCM buffer I/O

def channel_floats(buf, ch=0):
    """Read channel `ch` of an AVAudioPCMBuffer as a tuple of Python floats."""
    n = int(buf.frameLength())
    if n == 0:
        return ()
    data = buf.floatChannelData()
    if data is None:
        raise RuntimeError("floatChannelData is NULL (non-float buffer?)")
    p = data[ch]
    if isinstance(p, int):  # raw address — go through ctypes
        import ctypes
        return tuple((ctypes.c_float * n).from_address(p))
    try:
        mem = p.as_buffer(n * 4)  # objc.varlist -> memoryview
        return struct.unpack(f"<{n}f", mem)
    except Exception:
        return tuple(p[i] for i in range(n))  # slow indexed fallback


def fill_channel(buf, values, ch=0):
    """Write Python floats into channel `ch` of an AVAudioPCMBuffer."""
    n = len(values)
    buf.setFrameLength_(n)
    data = buf.floatChannelData()
    p = data[ch]
    try:
        mem = p.as_buffer(n * 4)
        struct.pack_into(f"<{n}f", mem, 0, *values)
        return "as_buffer"
    except Exception:
        for i, v in enumerate(values):
            p[i] = v
        return "indexed"


def rms(samples):
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def dbfs(x):
    return 20.0 * math.log10(x + 1e-12)


def write_wav(path, samples, sample_rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        ints = (max(-32767, min(32767, int(s * 32767.0))) for s in samples)
        w.writeframes(struct.pack(f"<{len(samples)}h", *ints))


# ---------------------------------------------------------------- self-test

def selftest():
    """Round-trip floats through an AVAudioPCMBuffer — no audio hardware touched."""
    sr, n = 48000.0, 4800
    fmt = AVFoundation.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(sr, 1)
    buf = AVFoundation.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, n)
    tone = [0.5 * math.sin(2 * math.pi * 440.0 * i / sr) for i in range(n)]
    how = fill_channel(buf, tone)
    got = channel_floats(buf)
    assert len(got) == n, f"frame count mismatch: {len(got)} != {n}"
    err = max(abs(a - b) for a, b in zip(tone, got))
    expected = rms(tone)
    measured = rms(got)
    print(f"[selftest] fill strategy: {how}")
    print(f"[selftest] round-trip max abs error: {err:.3e}")
    print(f"[selftest] RMS expected {expected:.6f} measured {measured:.6f}")
    assert err < 1e-6, "round-trip mismatch"
    print("[selftest] PASS — PyObjC float buffer read/write works")


# ---------------------------------------------------------------- main capture

def ensure_clip():
    if not os.path.exists(CLIP_PATH):
        subprocess.run(["say", "-o", CLIP_PATH, CLIP_TEXT], check=True)
    return CLIP_PATH


def load_clip(path):
    url = Foundation.NSURL.fileURLWithPath_(path)
    f, err = AVFoundation.AVAudioFile.alloc().initForReading_error_(url, None)
    if f is None:
        raise RuntimeError(f"cannot read clip: {err}")
    fmt = f.processingFormat()
    buf = AVFoundation.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(
        fmt, int(f.length()))
    ok, err = f.readIntoBuffer_error_(buf, None)
    if not ok:
        raise RuntimeError(f"cannot load clip into buffer: {err}")
    return buf, fmt


def run_capture(vp_enabled, seconds, baseline_secs):
    clip_buf, clip_fmt = load_clip(ensure_clip())

    engine = AVFoundation.AVAudioEngine.alloc().init()
    inp = engine.inputNode()
    out = engine.outputNode()

    if vp_enabled:
        ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
        if not ok:
            raise RuntimeError(f"setVoiceProcessingEnabled failed: {err}")
    print(f"voice processing: input={bool(inp.isVoiceProcessingEnabled())} "
          f"output={bool(out.isVoiceProcessingEnabled())}")

    player = AVFoundation.AVAudioPlayerNode.alloc().init()
    engine.attachNode_(player)
    engine.connect_to_format_(player, engine.mainMixerNode(), clip_fmt)
    player.setVolume_(1.0)

    tap_fmt = inp.outputFormatForBus_(0)
    sample_rate = float(tap_fmt.sampleRate())
    print(f"tap format: {tap_fmt.channelCount()} ch @ {sample_rate:.0f} Hz "
          f"(interleaved={bool(tap_fmt.isInterleaved())})")

    lock = threading.Lock()
    chunks = []  # (wall_time, floats) per tap callback

    def tap_block(buf, when):
        t = time.monotonic()
        try:
            floats = channel_floats(buf, 0)
        except Exception as e:  # report, don't crash inside the block
            print(f"[tap] extraction error: {e!r}", flush=True)
            return
        with lock:
            chunks.append((t, floats))

    # format=None -> use the node's own output format for the bus
    inp.installTapOnBus_bufferSize_format_block_(0, 4800, None, tap_block)

    engine.prepare()
    ok, err = engine.startAndReturnError_(None)
    if not ok:
        raise RuntimeError(f"engine start failed: {err} "
                           "(mic permission? mismatched input/output devices?)")
    print(f"engine running; capturing {baseline_secs:.0f}s baseline (stay quiet)...")
    time.sleep(baseline_secs)

    play_start = time.monotonic()
    player.scheduleBuffer_atTime_options_completionHandler_(
        clip_buf, None, BUFFER_LOOPS, None)
    player.play()
    print(f"playing clip (looped) for {seconds:.0f}s — do not speak...")
    time.sleep(seconds)

    player.stop()
    inp.removeTapOnBus_(0)
    engine.stop()

    # ---- analysis
    with lock:
        snap = list(chunks)
    if not snap:
        print("FAIL: tap delivered zero buffers (mic permission denied?)")
        return None

    times = [t for t, _ in snap]
    gaps = [b - a for a, b in zip(times, times[1:])]
    total_frames = sum(len(f) for _, f in snap)
    baseline = [s for t, f in snap if t < play_start for s in f]
    playback = [s for t, f in snap if t > play_start + 0.5 for s in f]

    b_rms, p_rms = rms(baseline), rms(playback)
    tag = "vp-on" if vp_enabled else "vp-off"
    wav_path = os.path.join(HERE, f"capture_{tag}.wav")
    write_wav(wav_path, [s for _, f in snap for s in f], sample_rate)

    print()
    print(f"=== RESULTS ({tag}) ===")
    print(f"tap callbacks: {len(snap)}  total frames: {total_frames} "
          f"({total_frames / sample_rate:.2f}s @ {sample_rate:.0f} Hz)")
    if gaps:
        print(f"tap cadence: mean gap {1000 * sum(gaps) / len(gaps):.1f} ms, "
              f"max gap {1000 * max(gaps):.1f} ms")
    print(f"baseline (room noise) RMS: {b_rms:.6f} ({dbfs(b_rms):.1f} dBFS) "
          f"over {len(baseline) / sample_rate:.2f}s")
    print(f"playback-window mic RMS:   {p_rms:.6f} ({dbfs(p_rms):.1f} dBFS) "
          f"over {len(playback) / sample_rate:.2f}s")
    print(f"echo-over-noise: {dbfs(p_rms) - dbfs(b_rms):+.1f} dB "
          f"(how much louder the mic got when the speakers played)")
    print(f"captured audio written to {wav_path}")
    return dbfs(p_rms) - dbfs(b_rms)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-vp", action="store_true",
                    help="disable voice processing (the control run)")
    ap.add_argument("--seconds", type=float, default=8.0,
                    help="playback capture window (default 8)")
    ap.add_argument("--baseline", type=float, default=2.0,
                    help="quiet baseline capture before playback (default 2)")
    ap.add_argument("--selftest", action="store_true",
                    help="hardware-free PCM buffer round-trip test, then exit")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        sys.stdout.flush()
        os._exit(0)

    try:
        run_capture(not args.no_vp, args.seconds, args.baseline)
    finally:
        # Known issue: HAL teardown can hang the interpreter at exit (see
        # SPIKE_PROGRESS.md). Results are already printed; exit hard.
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
