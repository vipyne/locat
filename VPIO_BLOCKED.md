# VPIO spike — HUMAN TEST NEEDED (Phase 2 A/B echo measurement)

The standalone AEC prototype (`spike-vpio/aec_prototype.py`) is written and its
hardware-free parts are verified (PCM buffer read/write self-test passed; speech clip
generation/loading works). What remains needs a human with speakers in a quiet room —
this cannot be faked.

## Setup (2 minutes)

1. **Built-in mic + built-in speakers only.** Disconnect/turn off AirPods and any other
   audio devices (System Settings → Sound: input = "MacBook Pro Microphone", output =
   "MacBook Pro Speakers"). Mismatched devices make VPIO fail with aggregate-device
   errors — this is a known constraint, not a bug.
2. **Quiet room** — no music, fans low, don't talk during the runs.
3. **System output volume ~60–75%.**

## Run (from the repo root)

```bash
cd spike-vpio

# Run 1 — voice processing ON:
uv run python aec_prototype.py

# Run 2 — voice processing OFF (the control):
uv run python aec_prototype.py --no-vp
```

Each run: ~2 s of quiet baseline capture, then ~8 s of a spoken test clip looping
through the speakers while the mic records. Stay quiet for the whole ~12 s.

- **First run will pop the macOS microphone permission dialog** for your terminal —
  click Allow, then re-run that command (the first run's numbers are invalid if the
  prompt appeared mid-capture).
- The script exits hard (`os._exit`) after printing results to dodge a known
  HAL-teardown hang. If it ever wedges anyway: Ctrl-C, then `kill -9` the python
  process; the printed results above the hang are still valid.

## What to report back (paste into SPIKE_PROGRESS.md or just save it here)

Paste the full `=== RESULTS (vp-on) ===` and `=== RESULTS (vp-off) ===` blocks from
both runs. The numbers that matter:

| number | vp-on | vp-off |
|---|---|---|
| baseline (room noise) RMS dBFS | | |
| playback-window mic RMS dBFS | | |
| **echo-over-noise dB** (the key A/B number) | | |
| tap cadence: mean / max gap ms | | |

**How to read it:** "echo-over-noise" is how much louder the mic got when the speakers
started playing. Expect vp-off to be strongly positive (mic clearly hears the speakers,
e.g. +15…+30 dB) and vp-on to be much smaller (AEC removing the playback). The
difference between the two echo-over-noise values ≈ the echo reduction VPIO delivers.
Absolute RMS values are NOT comparable across runs (VPIO applies its own gain), which
is why the within-run ratio is the measure.

Also worth noting:
- Listen to `spike-vpio/capture_vp-on.wav` vs `capture_vp-off.wav` — can you hear the
  clip in the off one and (mostly) not in the on one?
- Any errors printed, especially `[tap] extraction error` or `engine start failed`.
- If tap cadence max gap is huge (>500 ms), report that too — it affects Phase 3.

## Then

Delete `VPIO_BLOCKED` (the empty marker file) and this file, paste the numbers into
`SPIKE_PROGRESS.md` under a "human-reported results" heading (or just leave them in
the commit message), commit, and restart the loop — the next iteration will pick up
from the numbers.
