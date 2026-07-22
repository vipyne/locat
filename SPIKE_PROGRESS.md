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
- [x] Assess approach 2: `AUVoiceProcessingIO` audio unit via AudioToolbox/ctypes or PyObjC
      — REACHABLE from ctypes (4/4 probe checks) but INFERIOR to approach 1: RT-thread
      render callbacks vs Python/GIL; see findings 2026-07-22
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

### 2026-07-22 — Phase 1: approach 2 assessed (AUVoiceProcessingIO via ctypes) → REACHABLE BUT INFERIOR, not chosen
**What was tried:** desk research + a minimal ctypes probe,
`spike-vpio/probe_auvpio_ctypes.py` (no new deps — plain `ctypes.CDLL` on the
AudioToolbox framework).

**Probe results (4/4 checks passed, clean process exit — no HAL hang this time):**
- `AudioComponentFindNext(auou/vpio/appl)` finds the VoiceProcessingIO component.
- `AudioComponentInstanceNew` → status 0; `AudioUnitSetProperty(EnableIO, input
  scope, bus 1)` → status 0; `AudioUnitInitialize` → status 0.
- So the component is fully reachable/instantiable from pure-Python ctypes.

**Why it is NOT the chosen approach (the hard part is not reachability):**
- Driving an output audio unit requires `AURenderCallback`s that fire on the
  **real-time audio thread**. A ctypes `CFUNCTYPE` callback there must acquire the
  Python GIL on the RT thread → priority inversion / glitches by construction.
  Approach 1's `installTap` deliberately delivers buffers OFF the RT thread (the
  same reason the Apple engineer in forums thread 733733 recommends AVAudioEngine +
  tap over raw `AudioDeviceIOProc`-style CoreAudio).
- The only public VPIO-via-AUGraph example found (gist
  d08f98b14328baa5eddbdf98d0ab8b91, Objective-C) is itself broken — its render
  callback is annotated "Not being called at all" and a 2018 commenter's "does this
  work?" went unanswered. Even in ObjC this path is fiddly.
- Extra manual plumbing vs approach 1: device binding
  (`kAudioOutputUnitProperty_CurrentDevice`), AudioBufferList construction, format
  negotiation, all via hand-written ctypes structs.
- Related friction datapoint: Apple forums thread 651361 (AudioUnit APIs inside a
  Python process) hit component-enumeration anomalies (only system AUs visible);
  not blocking here (vpio IS a system AU and we found it), but a sign this layer
  misbehaves inside Python hosts.

**Sources:**
- https://developer.apple.com/documentation/audiotoolbox/kaudiounitsubtype_voiceprocessingio
- https://developer.apple.com/forums/thread/733733 (Apple engineer: prefer
  AVAudioEngine + tap; tap runs off the RT thread)
- https://developer.apple.com/forums/thread/651361 (AudioUnit enumeration anomalies
  inside Python extension)
- https://gist.github.com/d08f98b14328baa5eddbdf98d0ab8b91 (broken ObjC AUGraph +
  VPIO example)
- Local probe: `spike-vpio/probe_auvpio_ctypes.py` (4/4 OK)

**Conclusion:** Approach 2 is feasible-in-principle but strictly more work and more
RT-thread risk than approach 1 for zero extra benefit (same underlying VPIO DSP).
Keep only as a fallback if approach 1's tap cadence proves unusable in Phase 2.

**Next step:** Phase 1, next unchecked task — assess approach 4 (fallback): software
WebRTC APM binding (`webrtc-audio-processing` / `pywebrtc-audio-processing`) as a
Pipecat `audio_in_filter` fed the playback reference. Desk assessment: does a
maintained Python binding exist for Apple Silicon, and can Pipecat's filter API
supply the far-end reference signal?
