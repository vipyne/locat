# SPIKE_PROGRESS — VPIO echo cancellation (see SPIKE_VPIO.md for full plan)

Shared state for the amnesiac spike loop. Each iteration: pick the FIRST unchecked task
(phase order 0 → 4), do only that, record findings below, commit.

## Checklist

### Phase 0 — Scaffold
- [x] Create `spike-vpio/` directory for all prototype code (spike-local venv via `uv`;
      keep spike deps out of the main project)

### Phase 1 — Research & choose approach
- [x] Assess approach 1: AVAudioEngine + `setVoiceProcessingEnabled(true)` via PyObjC
      (feasibility of pulling processed mic buffers into Python AND rendering TTS output
      through the same engine) — FEASIBLE, verified by local probe; see findings 2026-07-22
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

### 2026-07-22 — Phase 1: approach 1 assessed (AVAudioEngine VP via PyObjC) → FEASIBLE
**What was tried:** web research (Apple dev forums + docs) plus a live local probe,
`spike-vpio/probe_avaudioengine.py`, run in the spike venv after
`uv add pyobjc-framework-AVFoundation` (installed 12.2.1; pulls in `-Cocoa`,
`-CoreAudio`, `-CoreMedia`, `-Quartz`; no brew/system deps).

**Probe results (all 13 checks passed on this M4 Pro, macOS 15.x):**
- `AVAudioEngine`, `inputNode`/`outputNode`, `AVAudioPlayerNode` all reachable via PyObjC.
- `inputNode.setVoiceProcessingEnabled_error_(True, None)` → `(True, None)` (success,
  no NSError); `isVoiceProcessingEnabled` reads back True on the input node AND
  auto-enables on the output node, exactly as Apple documents ("enabling on either IO
  node enables the other").
- After enabling VP, `inputFormatForBus_(0)` → **5 ch, 48000 Hz, Float32, deinterleaved**
  on this machine — a VPIO-side aggregate format. A Pipecat transport will need
  channel-select + resample to 16 kHz mono int16 (AVAudioConverter, or take channel 0
  and resample in Python).
- `installTapOnBus_bufferSize_format_block_(0, 1024, fmt, python_function)` accepts a
  **plain Python callable as the ObjC block** and `removeTapOnBus_(0)` works — so pulling
  processed mic buffers into Python is bridgeable. (Tap blocks run off the RT render
  thread, so a Python/GIL callback is tolerable by design.)
- Caveat: the tap-probe process printed its results but then hung on interpreter exit
  after touching the HAL (needed SIGKILL, exit 137). Prototype must not rely on clean
  implicit teardown — stop the engine explicitly before exit.

**Constraints found in sources (to design around in Phase 2):**
- AEC only works when input AND output are routed through the **same engine** — TTS
  playback must go through an `AVAudioPlayerNode → mainMixerNode → outputNode` in the
  VP-enabled engine (selector `scheduleBuffer:completionHandler:` confirmed present),
  NOT through PyAudio. (Apple forums 733733)
- Device pairing matters: built-in mic → built-in speakers works; **mismatched devices
  (e.g. AirPods mic + MBP speakers) fail** with aggregate-device channel-count mismatch
  errors. (Apple forums 810129 / 772006) Fine for the speaker-echo use case, which is
  precisely built-in mic + built-in speakers.
- Expected **gain reduction / volume drop** when VP is on; disabling AGC via
  `kAUVoiceIOProperty_...` does NOT remove it. macOS 14+ adds
  `voiceProcessingOtherAudioDuckingConfiguration` to control ducking of other audio.
  (Apple forums 733733 / 721535; Apple docs)
- Apple engineer in 733733 explicitly recommends AVAudioEngine + input-node tap over
  raw CoreAudio `AudioDeviceIOProc` — supports choosing approach 1 over approach 2.

**Sources:**
- https://developer.apple.com/forums/thread/733733 (macOS echo cancellation, Apple
  engineer recommends AVAudioEngine + tap)
- https://developer.apple.com/forums/thread/810129 and /772006 (mismatched-device
  aggregate failure)
- https://developer.apple.com/forums/thread/721535 (volume issue with VPIO)
- https://developer.apple.com/documentation/avfaudio/avaudioinputnode/voiceprocessingotheraudioduckingconfiguration
- Local probe: `spike-vpio/probe_avaudioengine.py` (13/13 OK)

**Conclusion:** Approach 1 is feasible from Python and is the front-runner. Remaining
unknowns for Phase 2: (a) does the tap actually deliver buffers at a usable cadence
while the engine runs (needs mic TCC permission for the terminal), (b) does AEC
measurably attenuate played audio (HUMAN-GATED), (c) format conversion 48k/5ch → 16k
mono.

**Next step:** Phase 1, next unchecked task — assess approach 2 (`AUVoiceProcessingIO`
via AudioToolbox/ctypes). Given the Apple-engineer recommendation above, this can be a
brief desk assessment (is it worth the extra complexity vs approach 1?) — note the
gist https://gist.github.com/d08f98b14328baa5eddbdf98d0ab8b91 (ObjC AUGraph +
VoiceProcessingIO) whose own input callback comment says "Not being called at all".
