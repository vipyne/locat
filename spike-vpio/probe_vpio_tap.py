"""Probe: what makes the INPUT tap deliver buffers under VPIO?

aec_prototype's VPIO run starts + plays but the input tap fires 0 times. This tries
tap-delivery variations under voice processing and counts callbacks over ~2.5s
(ambient room noise is enough — no speaking needed):

  A none/native fmt, no input connection      (what aec_prototype does now -> 0?)
  B explicit MONO tap fmt, no input connection
  C none/native fmt, input->manualMixer(vol 0)->output   (drive input render)
  D explicit MONO fmt, input->manualMixer(vol 0)->output

Whichever gives callbacks > 0 is the fix to apply to aec_prototype.py.

Run:  cd spike-vpio && uv run python probe_vpio_tap.py
"""

import os
import sys
import threading
import time

import AVFoundation
import Foundation


def _wait(seconds, pump):
    """Wait `seconds`, either blocking (sleep) or servicing the main run loop."""
    if not pump:
        time.sleep(seconds)
        return
    deadline = Foundation.NSDate.dateWithTimeIntervalSinceNow_(seconds)
    Foundation.NSRunLoop.currentRunLoop().runUntilDate_(deadline)


def count_taps(label, *, explicit_mono=False, connect_input=False, pump=False):
    engine = AVFoundation.AVAudioEngine.alloc().init()
    inp = engine.inputNode()
    out = engine.outputNode()

    ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
    if not ok:
        print(f"[{label}] setVoiceProcessingEnabled failed: {err}", flush=True)
        return

    n = {"c": 0}
    lock = threading.Lock()

    def tap_block(buf, when):
        with lock:
            n["c"] += 1

    bus_fmt = inp.outputFormatForBus_(0)
    if explicit_mono:
        fmt = AVFoundation.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(
            bus_fmt.sampleRate(), 1)
    else:
        fmt = None

    if connect_input:
        mixer = AVFoundation.AVAudioMixerNode.alloc().init()
        engine.attachNode_(mixer)
        engine.connect_to_format_(inp, mixer, bus_fmt)
        engine.connect_to_format_(mixer, out, out.outputFormatForBus_(0))
        try:
            mixer.setOutputVolume_(0.0)  # no monitoring/feedback
        except Exception:
            mixer.setVolume_(0.0)

    inp.installTapOnBus_bufferSize_format_block_(0, 4800, fmt, tap_block)
    engine.prepare()
    ok, err = engine.startAndReturnError_(None)
    if not ok:
        print(f"[{label}] start FAILED: "
              f"{err.localizedDescription() if err else err}", flush=True)
        return
    _wait(2.5, pump)
    engine.stop()
    print(f"[{label}] tap callbacks in 2.5s: {n['c']}", flush=True)


for label, kw in [
    ("A none/native, no-connect", {}),
    ("B mono, no-connect", {"explicit_mono": True}),
    ("C none/native, input->mixer(0)->out", {"connect_input": True}),
    ("D mono, input->mixer(0)->out", {"explicit_mono": True, "connect_input": True}),
    ("E none/native, no-connect, RUN LOOP", {"pump": True}),
    ("F none/native, input->mixer(0)->out, RUN LOOP", {"connect_input": True, "pump": True}),
]:
    try:
        count_taps(label, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] EXCEPTION {exc!r}", flush=True)

sys.stdout.flush()
os._exit(0)
