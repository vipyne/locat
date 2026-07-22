"""scripts/check_vad.py — measure what Pipecat's Silero VAD actually sees.

Feeds live mic audio into the SAME `SileroVADAnalyzer` bot.py uses (with default
`VADParams`) and prints, per frame, the two numbers the VAD gates on:

    speaking = (confidence >= VADParams.confidence)  AND  (volume >= VADParams.min_volume)
    defaults:              conf >= 0.7                            vol  >= 0.6

Run it, then alternate ~2s SILENCE / ~2s TALKING:

    uv run python scripts/check_vad.py

Columns:
- conf : Silero neural speech probability (0..1). Should jump toward ~1.0 when you
         talk. LEVEL-ROBUST — this is the signal we want to trust.
- vol  : EBU-R128 loudness normalized to 0..1. LEVEL-SENSITIVE — the portability trap.
- gate : which thresholds you pass. Seeing `conf=PASS vol=FAIL` while speaking proves
         the volume gate (min_volume) is what's blocking you — fixable in software,
         no per-machine calibration.
"""

import sys
import time

import numpy as np

try:
    import pyaudio
except ImportError:
    sys.exit("pyaudio not installed. Run: uv sync")

from pipecat.audio.utils import calculate_audio_volume
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

RATE = 16000
SECONDS = 12
CONF_T = VADParams().confidence  # 0.7 default
VOL_T = VADParams().min_volume   # 0.6 default


def main() -> None:
    analyzer = SileroVADAnalyzer(sample_rate=RATE)
    analyzer.set_sample_rate(RATE)
    n = analyzer.num_frames_required()

    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                     input=True, frames_per_buffer=n)

    print(f"Gate thresholds: confidence >= {CONF_T},  min_volume >= {VOL_T}")
    print(f"Frame = {n} samples. Alternate SILENCE / TALKING for ~{SECONDS}s...\n")
    print(f"{'conf':>6} {'vol':>6}  gate")

    max_conf = max_vol = 0.0
    ever_spoke = False
    end = time.monotonic() + SECONDS
    try:
        while time.monotonic() < end:
            data = stream.read(n, exception_on_overflow=False)
            # voice_confidence() returns a size-1 numpy array; flatten to a scalar.
            conf = float(np.ravel(analyzer.voice_confidence(data))[0])
            vol = float(np.ravel(calculate_audio_volume(data, RATE))[0])
            max_conf, max_vol = max(max_conf, conf), max(max_vol, vol)
            cpass, vpass = conf >= CONF_T, vol >= VOL_T
            ever_spoke = ever_spoke or (cpass and vpass)
            gate = f"conf={'PASS' if cpass else 'fail'} vol={'PASS' if vpass else 'FAIL'}"
            print(f"\r{conf:6.2f} {vol:6.2f}  {gate}   {'#' * int(conf * 20):<20}",
                  end="", flush=True)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    print(f"\n\nMax confidence: {max_conf:.2f}  (threshold {CONF_T})")
    print(f"Max volume:     {max_vol:.2f}  (threshold {VOL_T})")
    if ever_spoke:
        print("VERDICT: VAD registered speech (both gates passed). If the bot still")
        print("  doesn't respond, the issue is turn-taking/STT, not the VAD gate.")
    elif max_conf >= CONF_T and max_vol < VOL_T:
        print("VERDICT: Silero HEARD you (confidence passed) but the VOLUME GATE blocked")
        print("  every frame. Fix = lower min_volume so neural confidence decides. This is")
        print("  the portability fix — it removes the per-machine loudness dependency.")
    elif max_conf < CONF_T:
        print(f"VERDICT: confidence peaked at {max_conf:.2f}, under {CONF_T}. If it got")
        print("  reasonably high we can also lower the confidence threshold; if it stayed")
        print("  near 0, revisit the mic/device (see check_audio.py).")


if __name__ == "__main__":
    main()
