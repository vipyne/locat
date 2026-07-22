# SPIKE_PROGRESS — VPIO echo cancellation (see SPIKE_VPIO.md for full plan)

Shared state for the amnesiac spike loop. Each iteration: pick the FIRST unchecked task
(phase order 0 → 4), do only that, record findings below, commit.

## Checklist

### Phase 0 — Scaffold
- [x] Create `spike-vpio/` directory for all prototype code (spike-local venv via `uv`;
      keep spike deps out of the main project)

### Phase 1 — Research & choose approach
- [ ] Assess approach 1: AVAudioEngine + `setVoiceProcessingEnabled(true)` via PyObjC
      (feasibility of pulling processed mic buffers into Python AND rendering TTS output
      through the same engine)
- [ ] Assess approach 2: `AUVoiceProcessingIO` audio unit via AudioToolbox/ctypes or PyObjC
- [ ] Assess approach 4 (fallback): software WebRTC APM binding (`webrtc-audio-processing`)
      as `audio_in_filter` with playback reference
- [ ] Read installed Pipecat transport source
      (`.venv/lib/python3.12/site-packages/pipecat/transports/local/audio.py`,
      `base_input.py`, `base_output.py`) and record the interface a VPIO transport must
      implement (approach 3 groundwork)
- [ ] Record chosen approach + required packages in Findings, with cited sources

### Phase 2 — Standalone AEC prototype
- [ ] Smallest script in `spike-vpio/` that opens VPIO capture+render, plays a known clip
      through speakers while capturing mic, and computes a numeric echo measure
      (e.g. captured RMS, VPIO on vs off)
- [ ] HUMAN-GATED: write exact test steps + numbers-to-report into `VPIO_BLOCKED.md`,
      create empty `VPIO_BLOCKED`, stop; then record human-reported numbers here
- [ ] Conclude: does VPIO measurably reduce echo? (If not after ~2 rounds → record
      infeasibility finding and jump to Phase 4 with a DROP recommendation)

### Phase 3 — Integrate into Pipecat (only if Phase 2 proved AEC works)
- [ ] Wrap VPIO path as a Pipecat transport / input+output path selectable via
      `AUDIO_BACKEND=vpio` (default stays current PyAudio path; `bot.py` keeps working)
- [ ] HUMAN-GATED: bot on speakers, confirm no self-interruption (via `VPIO_BLOCKED`
      handoff; never fake this)

### Phase 4 — Findings + decision
- [ ] Write `SPIKE_FINDINGS.md`: KEEP (with wiring notes) or DROP (with recommended
      alternative), backed by the evidence above
- [ ] Create empty `VPIO_DONE`, commit, stop

## Findings log

(append per-iteration: what was tried, what happened — numbers/errors, conclusion, next step)

### 2026-07-22 — Phase 0: scaffold `spike-vpio/`
- Created `spike-vpio/` with `pyproject.toml` (uv project, `requires-python >=3.12`,
  **zero deps** — deps get added in Phase 1 once an approach is chosen), `README.md`,
  and `.gitignore` (ignores `spike-vpio/.venv/`, `__pycache__/`, `*.wav`).
- Verified isolation: `uv venv && uv sync` inside `spike-vpio/` succeeded;
  `uv run python -c "print(sys.prefix)"` → `spike-vpio/.venv` (CPython 3.12.0),
  i.e. NOT the main bot's root `.venv`. Main project untouched.
- No system/brew deps needed yet.
- **Next step:** Phase 1, first unchecked task — assess approach 1
  (AVAudioEngine + `setVoiceProcessingEnabled(true)` via PyObjC): can we pull
  processed mic buffers into Python (installTap / render callback) AND play TTS
  output through the same engine so AEC has its reference signal? Use
  WebSearch/WebFetch on Apple docs + PyObjC and cite sources here.
