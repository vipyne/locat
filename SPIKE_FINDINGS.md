# SPIKE FINDINGS: macOS VPIO echo cancellation from Python — DROP

**Decision: DROP.** Do not pursue macOS Voice Processing (VPIO) echo cancellation from the bot's
Python process. Use **headphones** for speaker-free operation (free, complete, offline). Revisit
only under the narrow conditions listed at the bottom.

## What we were testing
Whether the bot could route local mic/speaker audio through macOS's system Voice Processing I/O
(AEC/AGC/NS) — the same tech WebRTC gets "for free" — from a plain Python/PyObjC process, and
integrate it into Pipecat so the bot works on **speakers** without hearing (and interrupting)
itself. All experiments used `AVAudioEngine` + `setVoiceProcessingEnabled(true)` via PyObjC
(`pyobjc-framework-avfoundation 12.2.1`), on the built-in MacBook Pro mic + speakers (matched
devices, 48 kHz), mic permission granted.

## Evidence (what we actually found)

**The echo problem is real and quantified.** Control run (VPIO off), built-in mic + speakers:
`echo-over-noise = +13.0 dB` — the mic gets 13 dB louder when the speakers play. This is exactly
the self-hearing that makes the bot interrupt its own replies on speakers. (Confirms the reason
the spike existed.)

**VPIO can *initialize* from Python — but nothing usable comes out.** Three independent walls, each
bisected with a standalone probe (`probe_vpio_start.py`, `probe_vpio_tap.py`):

1. **`mainMixerNode` is incompatible with VPIO.** Touching `engine.mainMixerNode()` with voice
   processing enabled fails engine start with `-10875` (`kAudioUnitErr_FailedInitialization`) and
   zeroes the output format. Workaround found: connect the player **straight to `outputNode`** (a
   *bare* VPIO engine starts fine: `ok=True, 2ch@48kHz`). So this one is surmountable.

2. **AVAudioConverter's block API is unusable via PyObjC.** `convertToBuffer:error:withInputFromBlock:`
   returns a generic `OSStatus -1` — the input block's status out-pointer doesn't bridge. Worked
   around with a pure-Python linear resampler (verified, round-trip RMS preserved). Surmountable,
   but a sign of how thin the PyObjC bridge is here.

3. **The input tap delivers ZERO processed-mic buffers under VPIO — the blocker.** With voice
   processing on, `installTapOnBus` never fires its callback. Tested 6 configurations, all 0
   callbacks over 2.5 s (VPIO-off taps fire ~10×/s on the same machine, same code):
   | config | tap fmt | input wired to sink | wait method | callbacks |
   |---|---|---|---|---|
   | A | native (3ch) | no | sleep | **0** |
   | B | explicit mono | no | sleep | **0** |
   | C | native | input→mixer(vol 0)→out | sleep | **0** |
   | D | mono | input→mixer(vol 0)→out | sleep | **0** |
   | E | native | no | **main run loop** | **0** |
   | F | native | input→mixer(vol 0)→out | **main run loop** | **0** |

   Format, graph wiring, and run-loop delivery are all ruled out.

## Why (most likely)
VPIO **initializes** but the processed audio never reaches an `AVAudioEngine` tap in a plain
command-line/PyObjC process. This is consistent with VPIO's known dependence on running inside a
**signed `.app` bundle** with a proper audio session / microphone entitlement — a bare `python`
launched by `uv` has none of that. The unit reports success on start but produces no input stream.

## Recommendation
- **Ship with headphones** as the speaker-free story. The bot already works end-to-end on
  headphones; with no speaker→mic bleed there is nothing to cancel. Zero code, zero deps, fully
  offline. Document it in the README.
- **Cheap partial mitigation (optional), if speaker use is ever wanted:** raise `VAD_MIN_VOLUME`
  and/or add a `MinWordsInterruptionStrategy` so brief self-audio doesn't cancel a reply. Imperfect
  (won't stop a loud full-sentence echo) but low-effort — no VPIO needed.

## Cost/benefit
Getting *usable* echo cancellation this way would require abandoning AVAudioEngine for raw
`AUVoiceProcessingIO` render callbacks in C (via ctypes/PyObjC), and/or repackaging the bot as a
signed app bundle. That is a substantial, fragile subsystem — disproportionate for a personal,
single-user, quiet-room financial bot where headphones are a complete free fix.

## Revisit only if
- The bot is repackaged as a **signed `.app` bundle** (then AVAudioEngine VPIO taps may deliver), OR
- Someone implements the **raw `AUVoiceProcessingIO` AudioUnit** with a manual render callback
  (C-level, bypassing AVAudioEngine taps entirely), OR
- Speaker-mode becomes a hard product requirement — at which point budget it as its own project.

## Artifacts (under `spike-vpio/`, kept for reference)
`aec_prototype.py` (A/B echo measure; VPIO start fixed, blocked on tap delivery),
`probe_vpio_start.py` (bisected the `-10875` mixer conflict),
`probe_vpio_tap.py` (bisected the zero-buffers tap failure), `capture_vp-off.wav` (control).
The main bot was never modified — it remains on the working PyAudio path.
