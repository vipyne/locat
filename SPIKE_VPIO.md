# SPIKE: macOS Voice Processing (VPIO) echo cancellation for local audio

## Context
The offline bot uses raw PyAudio (`LocalAudioTransport`), which has **no acoustic echo
cancellation**. On speakers the mic hears the bot's own voice → VAD fires → the bot interrupts
and cancels its own reply. WebRTC gets AEC/AGC/noise-suppression "for free" because the browser
audio stack has the playback reference signal + a built-in processing module; raw local audio has
neither. macOS itself ships excellent **Voice Processing I/O (VPIO)** — system AEC/AGC/NS (the same
tech behind "Voice Isolation"). This spike determines whether we can route the bot's audio through
macOS VPIO **from Python** and integrate it into Pipecat, to enable clean speaker-mode
conversation while staying fully offline.

## Definition of done (this is a SPIKE — the deliverable is a DECISION)
`SPIKE_FINDINGS.md` that answers: **is VPIO usable from Python for this bot, and is it worth it?**
Backed by EITHER
- (a) a working prototype that **measurably** reduces echo, integrated as a Pipecat-compatible
  input/output path behind a config flag (`AUDIO_BACKEND=vpio`, non-default); OR
- (b) a well-evidenced "not feasible / not worth the complexity" conclusion + the recommended
  alternative (headphones / interruption-tuning).

A spike SUCCEEDS by reaching a clear keep/drop call with evidence — not by shipping a polished
feature.

## Approaches to investigate (leads — evaluate, then pick the most tractable)
1. **AVAudioEngine + `setVoiceProcessingEnabled(true)`** via PyObjC (AVFoundation, macOS 10.15+).
   Enabling voice processing on the input/output nodes turns on the system VPIO (AEC/AGC/NS).
   Most promising. Key question: can we (i) pull processed mic buffers into Python and (ii) render
   the bot's TTS output through the SAME engine so the echo canceller has the playback reference?
2. **`AUVoiceProcessingIO`** audio unit (`kAudioUnitSubType_VoiceProcessingIO`) via
   AudioToolbox/CoreAudio through PyObjC or ctypes. The canonical AEC unit — lower level, more
   control, more work.
3. **Custom Pipecat transport** replacing `LocalAudioTransport`: input = VPIO-processed capture,
   output = render through VPIO (required — the canceller must know what's being played). Study the
   installed `pipecat/transports/local/audio.py`, `base_input.py`, `base_output.py` for the
   interface to implement (`push_audio_frame`, `write_audio_frame`, sample-rate handling).
4. **Fallback:** a software WebRTC APM binding (e.g. `webrtc-audio-processing`) as an
   `audio_in_filter`, fed the playback as reference. Only if native VPIO proves infeasible.

## Phases (each independently checkable; audio-quality checks are HUMAN-GATED)
0. **Scaffold** — create `spike-vpio/` for all prototype code; seed `SPIKE_PROGRESS.md` from these
   phases. Keep spike deps OUT of the main bot: use a spike-local venv or clearly-marked optional
   extras; do NOT touch `bot.py`/main deps until Phase 3.
1. **Research & choose** — assess approaches 1–4 via Apple docs + PyObjC (use WebSearch/WebFetch).
   Record feasibility, required packages (e.g. `pyobjc-framework-AVFoundation`), and the chosen
   approach in `SPIKE_PROGRESS.md`.
2. **Standalone AEC prototype** — smallest script that opens a VPIO capture+render, plays a known
   clip out the speakers while capturing the mic, and shows the played audio is attenuated in the
   captured signal. Include a numeric measure (e.g. captured RMS with VPIO on vs off).
   HUMAN-GATED: needs speakers + a quiet room — write exact test steps to `VPIO_BLOCKED.md`,
   create `VPIO_BLOCKED`, and stop for the human to run and report the numbers.
3. **Integrate into Pipecat** — wrap the VPIO path as a transport (or input/output filter) the bot
   can select via `AUDIO_BACKEND=vpio` (default stays the current PyAudio path). HUMAN-GATED: run
   the bot on speakers and confirm no self-interruption.
4. **Findings + recommendation** — `SPIKE_FINDINGS.md`: does it work, how well (echo reduction,
   added latency), complexity, and a clear KEEP (with wiring notes) or DROP (with the alternative).

## Guardrails
- **Spike discipline:** smallest experiment that answers feasibility first; no polish before AEC is
  proven to work.
- **Do not disturb the working bot:** no changes to `bot.py` or main deps until Phase 3, and then
  only behind a non-default flag. All prototype code lives under `spike-vpio/`.
- **Never fake an audio verification** — echo reduction and "no self-interruption" require a human
  on speakers. Hand off via `VPIO_BLOCKED` for those.
- **Time-box:** if after ~2 rounds of a chosen approach AEC isn't demonstrably working, record a
  "not feasible from Python without excessive effort" finding and recommend the alternative — that
  is a valid, successful spike outcome.

## Verification (spike-complete)
- A prototype that **measurably** reduces captured echo (human-reported numbers), OR an evidenced
  infeasibility finding.
- If integrated: bot runs on speakers with no self-interruption (human-confirmed).
- `SPIKE_FINDINGS.md` states a clear keep/drop decision with the evidence behind it.
