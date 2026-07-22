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
- [x] Assess approach 4 (fallback): software WebRTC APM binding (`webrtc-audio-processing`)
      as `audio_in_filter` with playback reference — NOT VIABLE: no binding installs on
      macOS arm64 (verified build failures), and Pipecat's filter API has no far-end
      reference input; see findings 2026-07-22
- [x] Read installed Pipecat transport source
      (`.venv/lib/python3.12/site-packages/pipecat/transports/local/audio.py`,
      `base_input.py`, `base_output.py`) and record the interface a VPIO transport must
      implement (approach 3 groundwork) — DONE, full interface spec in findings 2026-07-22
- [x] Record chosen approach + required packages in Findings, with cited sources —
      CHOSEN: approach 1 (AVAudioEngine + `setVoiceProcessingEnabled` via PyObjC);
      see consolidated decision entry 2026-07-22

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

### 2026-07-22 — Phase 1: approach 4 assessed (software WebRTC APM as audio_in_filter) → NOT VIABLE, fallback rejected
**What was tried:** (a) surveyed PyPI for WebRTC APM bindings and checked their wheel
platforms via the PyPI JSON API; (b) empirically attempted installs into the isolated
spike venv (`uv pip install --python spike-vpio/.venv/bin/python <pkg>`); (c) read the
installed Pipecat 1.5.0 filter/transport source to see whether a filter can even get
the far-end (playback) reference.

**Packaging results (both fronts fail on Apple Silicon):**
- `webrtc-audio-processing` 0.1.3 (xiongyihui): DEAD — last sdist 2018, only wheels are
  cp27/cp36 **linux_armv7l** (2019). Install attempt on this M4 Pro fails immediately:
  `error: command 'swig' failed: No such file or directory` (needs swig via brew, and
  even then it wraps a ~2016 APM snapshot; not pursued further — unmaintained).
- `aec-audio-processing` 1.0.1 (Sept 2025, the only *active* AEC binding found): ships
  **Windows-only wheels** (cp311–313 win_amd64) + sdist. The sdist build on macOS arm64
  compiles vendored webrtc via meson/ninja and FAILS: repeated
  `../webrtc/api/scoped_refptr.h:82: error: no template named 'Nullable' in namespace
  'absl'` (×3 per TU) — vendored webrtc expects an abseil with `absl::Nullable/Nonnull`,
  incompatible with what the build resolves here. Fixing = pinning/patching abseil in
  someone else's vendored C++ build → "excessive effort" territory for a *fallback*.
- `webrtc-noise-gain` 1.3.0 (rhasspy, maintained): **no AEC at all** (NS + AGC only)
  and no macOS wheels in the latest release. Not applicable.
- Nothing was actually installed into the spike venv (both installs failed cleanly);
  no brew deps added. (swig, and likely a matching abseil, WOULD be needed to go
  further — deliberately not installed.)

**Architectural result (the deeper blocker, independent of packaging):**
- Pipecat's filter interface `BaseAudioFilter.filter(audio: bytes) -> bytes`
  (`pipecat/audio/filters/base_audio_filter.py`) receives ONLY near-end mic bytes.
  `base_input.py:281-283` applies it inline on input frames. There is **no far-end
  reference input anywhere in the filter API** — but WebRTC APM AEC requires the
  played-back signal, time-aligned to ~10 ms frames.
- Output goes through a separate PyAudio stream in `LocalAudioOutputTransport.
  write_audio_frame` (`pipecat/transports/local/audio.py:174-185`, blocking write in an
  executor). Feeding an APM would mean hand-wiring a ring buffer from the output
  transport into the input filter AND estimating device playout latency (PyAudio gives
  only coarse `get_output_latency()`); software AEC quality collapses when the
  reference is misaligned. That is custom-transport-scale work (≈ approach 3) stacked
  on top of an unmaintained C++ build — strictly worse than approach 1, where the OS
  sees the true playout timeline for free.

**Sources:**
- https://pypi.org/project/webrtc-audio-processing/ (release history: 2018/2019, armv7l only)
- https://pypi.org/project/aec-audio-processing/ (1.0.1, 2025-09-01, win_amd64 wheels only)
- https://pypi.org/project/webrtc-noise-gain/ (1.3.0, NS+AGC only, no echo cancellation)
- Local build logs (swig failure; absl::Nullable compile errors) — reproduced 2026-07-22
- Installed source: `.venv/.../pipecat/audio/filters/base_audio_filter.py`,
  `.venv/.../pipecat/transports/base_input.py:281`, `.../transports/local/audio.py:174`

**Conclusion:** Approach 4 is REJECTED as a fallback: no maintained Apple Silicon
binding exists (verified by real install attempts), and Pipecat's `audio_in_filter`
API structurally cannot deliver the far-end reference an APM needs. If approach 1's
tap fails in Phase 2, the honest alternative is headphones/interruption-tuning (DROP),
not software AEC.

**Next step:** Phase 1, next unchecked task — read installed Pipecat transport source
(`.venv/.../pipecat/transports/local/audio.py`, `base_input.py`, `base_output.py`) and
record the exact interface a VPIO transport must implement (approach 3 groundwork):
constructor params, `push_audio_frame`/`write_audio_frame` contracts, sample-rate
negotiation, and where start/stop hooks live.

### 2026-07-22 — Phase 1: Pipecat 1.5.0 transport interface read → VPIO transport contract recorded
**What was done:** read the installed Pipecat 1.5.0 source (NOT the context hub, which
lags 1.5.x): `pipecat/transports/local/audio.py`, `base_input.py`, `base_output.py`,
`base_transport.py`, plus `pipeline/worker.py` + `frames/frames.py` for sample-rate
defaults, and `bot.py` (read-only) for how the transport is constructed today.

**The interface a `VPIOTransport` must implement (approach 3):**

*Top level — subclass `BaseTransport` (`base_transport.py:92`):*
- Implement `input() -> FrameProcessor` and `output() -> FrameProcessor`, returning
  cached instances (see `LocalAudioTransport.input/output`, `local/audio.py:215-233`).
- Params: subclass `TransportParams` (pydantic, `base_transport.py:25`); bot.py sets
  only `audio_in_enabled=True, audio_out_enabled=True` + device indices
  (`bot.py:69-75`). NOTE `TransportParams` silently ignores unknown kwargs (pydantic);
  bot.py:62-64 already documents that gotcha.
- KEY STRUCTURAL DIFFERENCE vs `LocalAudioTransport`: input and output must share ONE
  `AVAudioEngine` (AEC needs the playout reference in the same engine). So the
  `VPIOTransport` owns the engine + player node and hands references to both sides —
  engine start must be idempotent (both sides' `start()` fire on the same StartFrame)
  and engine stop must happen explicitly in whichever `cleanup()` runs (guarded),
  given the probe's known hang-on-implicit-teardown.

*Input side — subclass `BaseInputTransport` (`base_input.py:36`):*
- `__init__(params)` → `super().__init__(params)`.
- `async start(frame: StartFrame)`: MUST first `await super().start(frame)` (base sets
  `self._sample_rate = params.audio_in_sample_rate or frame.audio_in_sample_rate`,
  `base_input.py:128`, and starts any `audio_in_filter`); then set up capture; then
  MUST `await self.set_transport_ready(frame)` — that call creates the internal
  `_audio_in_queue` + processing task (`base_input.py:177-184,255-259`); skipping it
  makes `push_audio_frame` crash (queue doesn't exist).
- Delivery contract (from `LocalAudioInputTransport._audio_in_callback`,
  `local/audio.py:103-113`): from the capture-callback thread, build
  `InputAudioRawFrame(audio=<int16 mono bytes>, sample_rate=self._sample_rate,
  num_channels=params.audio_in_channels)` and hand it to the loop via
  `asyncio.run_coroutine_threadsafe(self.push_audio_frame(frame),
  self.get_event_loop())`. PyAudio uses 20 ms chunks; cadence is flexible (frames go
  through an asyncio queue; only a 0.5 s no-audio warning timeout,
  `base_input.py:33`). The AVAudioEngine tap block (runs off the RT thread — safe for
  Python) must convert its 48 kHz 5-ch deinterleaved Float32 buffers (probe finding
  above) → 16 kHz mono int16 before pushing: take channel 0, resample 48k→16k
  (3:1 integer ratio — trivial decimation after low-pass, or `AVAudioConverter`).
- Base class handles everything downstream: VAD/filter/passthrough
  (`base_input.py:267-297`), stop/pause/cancel. Override `cleanup()` → `await
  super().cleanup()` then explicitly stop tap/engine.

*Output side — subclass `BaseOutputTransport` (`base_output.py:60`):*
- `async start(frame)`: `await super().start(frame)` (base sets `_sample_rate =
  params.audio_out_sample_rate or frame.audio_out_sample_rate` and
  `_audio_chunk_size` = 10 ms bytes × `audio_out_10ms_chunks` (default 4 → 40 ms
  chunks), `base_output.py:129-135`); then set up playback; then `await
  self.set_transport_ready(frame)` (spins up the `MediaSender` machinery and pushes
  `OutputTransportReadyFrame` upstream, `base_output.py:161-202`).
- Implement `async write_audio_frame(frame: OutputAudioRawFrame) -> bool`
  (`base_output.py:241`): called by the MediaSender audio task with exactly
  `_audio_chunk_size` bytes of int16 mono ALREADY resampled to the transport's out
  rate (MediaSender resamples every incoming frame, `base_output.py:588-590` — the
  VPIO side never sees the TTS's native rate). Return True on success (False stops
  downstream propagation, which the assistant context aggregator relies on).
- BACKPRESSURE IS THE CONTRACT: PyAudio's blocking `stream.write` in a 1-thread
  executor (`local/audio.py:174-188`) is what paces the whole send loop and keeps
  interruption latency at ~1 chunk. `AVAudioPlayerNode.scheduleBuffer:` is
  fire-and-forget, so the VPIO output must await a completion-handler-released
  semaphore to keep ≤1–2 chunks in flight, or barge-in will let seconds of scheduled
  TTS keep playing (base interruption handling only stops FUTURE writes by
  cancelling the audio task, `base_output.py:548-575`; already-scheduled audio is the
  transport's problem — consider `playerNode.stop()` on interruption, hooked via the
  input side or a small override of `_handle_frame` for `InterruptionFrame`).
- int16 → Float32 conversion into an `AVAudioPCMBuffer` at the player node's format;
  the engine's mixer handles 24 kHz → hardware-rate conversion if the buffer/node
  format is declared as 24 kHz mono Float32.

*Sample-rate negotiation:* bot.py uses default `PipelineParams()` (`bot.py:249`) →
StartFrame carries `audio_in_sample_rate=16000`, `audio_out_sample_rate=24000`
(`pipeline/worker.py:160-161`, `frames/frames.py:923-924`). So the VPIO transport
must deliver 16 kHz mono int16 in, accept 24 kHz mono int16 out — matching today's
behavior with zero bot.py changes beyond the `AUDIO_BACKEND=vpio` switch.

**Conclusion:** the Pipecat side is small and well-defined — two subclasses, one
`write_audio_frame`, one capture→`push_audio_frame` bridge, both formats known. The
real engineering risks stay on the AVFoundation side: (a) tap cadence/latency,
(b) output backpressure + interruption flush via `AVAudioPlayerNode`, (c) 5-ch→mono
selection. All three are exactly what the Phase 2 standalone prototype exercises.

**Next step:** Phase 1, last unchecked task — record the CHOSEN approach (approach 1:
AVAudioEngine + `setVoiceProcessingEnabled` via PyObjC, per the three assessments
above) + required packages (`pyobjc-framework-AVFoundation` 12.2.1) as a short
consolidated entry with cited sources, then move to Phase 2 (standalone AEC
prototype script in `spike-vpio/`).

### 2026-07-22 — Phase 1 COMPLETE: chosen approach recorded → APPROACH 1 (AVAudioEngine VP via PyObjC)

**Decision (consolidates the four assessments above):**

| Approach | Verdict | Key evidence |
|---|---|---|
| 1. AVAudioEngine + `setVoiceProcessingEnabled` (PyObjC) | **CHOSEN** | 13/13 local probe checks passed (`probe_avaudioengine.py`); VP enables cleanly; `installTap` accepts a Python callable and runs OFF the RT thread; Apple engineer explicitly recommends this API for AEC (forums 733733) |
| 2. `AUVoiceProcessingIO` via ctypes | Rejected (fallback only) | Reachable (4/4 probe checks, `probe_auvpio_ctypes.py`) but requires render callbacks on the RT audio thread → Python/GIL priority inversion by construction; only public example is broken |
| 3. Custom Pipecat transport | Not an alternative — it's the *integration layer* for approach 1 | Full Pipecat 1.5.0 interface contract recorded above (transport-read entry, 2026-07-22) |
| 4. Software WebRTC APM (`audio_in_filter`) | Rejected | No Apple Silicon binding builds (swig / `absl::Nullable` failures, verified); Pipecat filter API has no far-end reference input |

**Required packages (spike venv only, already installed there):**
- `pyobjc-framework-AVFoundation==12.2.1` (pulls `pyobjc-framework-Cocoa`, `-CoreAudio`,
  `-CoreMedia`, `-Quartz`). No brew/system deps. Phase 3 would add the same single
  dependency to the main project as an optional extra behind `AUDIO_BACKEND=vpio`.

**Design constraints carried into Phase 2 (from the assessments above):**
1. TTS playback MUST go through the same VP-enabled engine
   (`AVAudioPlayerNode → mainMixerNode → outputNode`) — that's what gives AEC its
   reference (forums 733733).
2. Post-VP input format on this machine is 48 kHz / 5-ch deinterleaved Float32 →
   need channel-0 select + 48k→16k resample for Pipecat.
3. Stop the engine explicitly before interpreter exit (probe hung on implicit HAL
   teardown, needed SIGKILL).
4. Built-in mic + built-in speakers only for testing (mismatched devices fail with
   aggregate-channel errors, forums 810129/772006).
5. Expect VP-on gain reduction (forums 721535) — measure RMS ratios, not absolutes.

**Sources:** all cited inline in the four assessment entries above (Apple forums
733733, 810129, 772006, 721535, 651361; Apple AVFoundation/AudioToolbox docs; PyPI
JSON API for the three APM bindings; local probes `probe_avaudioengine.py` 13/13 and
`probe_auvpio_ctypes.py` 4/4; installed Pipecat 1.5.0 source).

**Next step:** Phase 2, first task — write the smallest standalone script in
`spike-vpio/` (e.g. `aec_prototype.py`) that: builds one VP-enabled `AVAudioEngine`,
schedules a known tone/clip on an `AVAudioPlayerNode` through the speakers, taps the
input node, records captured audio to WAV + computes RMS, runnable with VP on vs off
(`--no-vp` flag) for an A/B echo measure. Note: first live capture run will trigger
the macOS mic TCC permission prompt for the terminal — the run itself is HUMAN-GATED
(speakers + quiet room), so the iteration that writes the script should also write
`VPIO_BLOCKED.md` with exact run steps + numbers to report, create `VPIO_BLOCKED`,
and stop.
