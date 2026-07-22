"""Probe: is AVAudioEngine voice processing reachable from PyObjC?

Checks (no audio started, no mic permission needed for the selector checks):
  1. AVAudioEngine + inputNode/outputNode exist via PyObjC
  2. setVoiceProcessingEnabled:error: selector exists on both IO nodes
  3. installTapOnBus:bufferSize:format:block: selector exists (mic capture path)
  4. AVAudioPlayerNode + scheduleBuffer (TTS render path through same engine)
  5. Attempt to actually enable voice processing (may touch the HAL)

Run: uv run python probe_avaudioengine.py
"""
import AVFoundation  # pyobjc-framework-AVFoundation

results = []

def check(label, fn):
    try:
        val = fn()
        results.append((label, "OK", val))
    except Exception as e:  # noqa: BLE001 - probe reports everything
        results.append((label, "FAIL", repr(e)))

engine = AVFoundation.AVAudioEngine.alloc().init()
check("engine created", lambda: type(engine).__name__)

inp = engine.inputNode()
out = engine.outputNode()
check("inputNode", lambda: type(inp).__name__)
check("outputNode", lambda: type(out).__name__)

check("inputNode responds to setVoiceProcessingEnabled:error:",
      lambda: bool(inp.respondsToSelector_(b"setVoiceProcessingEnabled:error:")))
check("outputNode responds to setVoiceProcessingEnabled:error:",
      lambda: bool(out.respondsToSelector_(b"setVoiceProcessingEnabled:error:")))
check("inputNode responds to isVoiceProcessingEnabled",
      lambda: bool(inp.respondsToSelector_(b"isVoiceProcessingEnabled")))
check("inputNode responds to installTapOnBus:bufferSize:format:block:",
      lambda: bool(inp.respondsToSelector_(b"installTapOnBus:bufferSize:format:block:")))

player = AVFoundation.AVAudioPlayerNode.alloc().init()
check("AVAudioPlayerNode created", lambda: type(player).__name__)
check("player responds to scheduleBuffer:completionHandler:",
      lambda: bool(player.respondsToSelector_(b"scheduleBuffer:completionHandler:")))

# The real attempt: enable voice processing. PyObjC maps the NSError** out-param
# to a (BOOL, NSError) return tuple.
check("setVoiceProcessingEnabled(True) on inputNode",
      lambda: inp.setVoiceProcessingEnabled_error_(True, None))
check("isVoiceProcessingEnabled after enable",
      lambda: bool(inp.isVoiceProcessingEnabled()))
check("outputNode isVoiceProcessingEnabled (should auto-enable)",
      lambda: bool(out.isVoiceProcessingEnabled()))

check("inputNode format bus0 after VP",
      lambda: str(inp.inputFormatForBus_(0)))

for label, status, val in results:
    print(f"[{status}] {label}: {val}")
