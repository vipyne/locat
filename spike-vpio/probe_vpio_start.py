"""Bisect the VPIO -10875 (kAudioUnitErr_FailedInitialization) start failure.

Tries to start an AVAudioEngine with voice processing under progressively more of
the graph, to localize what actually breaks initialization:

  bare                : engine + inputNode VP on, then start (no mixer/player/tap)
  mixer               : + realize mainMixerNode (mixer->output connection)
  mixer+player@outfmt : + player connected to mixer at the OUTPUT format (48 kHz)
  mixer+player@22k    : + player connected at 22050 Hz mono (what aec_prototype does)

Each uses a fresh engine. Reports start ok + output format + error per config.

Run:  uv run python probe_vpio_start.py
"""

import os
import sys
import time

import AVFoundation


def try_start(label, *, mixer=False, player=False, player_sr=None, direct_out=False):
    engine = AVFoundation.AVAudioEngine.alloc().init()
    inp = engine.inputNode()
    out = engine.outputNode()

    ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
    if not ok:
        print(f"[{label}] setVoiceProcessingEnabled FAILED: {err}", flush=True)
        return
    vp = f"in={bool(inp.isVoiceProcessingEnabled())} out={bool(out.isVoiceProcessingEnabled())}"

    if direct_out:
        # connect a player STRAIGHT to outputNode — never touch mainMixerNode
        fmt = out.outputFormatForBus_(0)
        p = AVFoundation.AVAudioPlayerNode.alloc().init()
        engine.attachNode_(p)
        engine.connect_to_format_(p, out, fmt)
    elif mixer or player:
        mm = engine.mainMixerNode()
        if player:
            if player_sr is None:
                fmt = out.outputFormatForBus_(0)  # engine's real output format (48k)
            else:
                fmt = AVFoundation.AVAudioFormat.alloc(
                    ).initStandardFormatWithSampleRate_channels_(player_sr, 1)
            p = AVFoundation.AVAudioPlayerNode.alloc().init()
            engine.attachNode_(p)
            engine.connect_to_format_(p, mm, fmt)

    engine.prepare()
    ok, err = engine.startAndReturnError_(None)
    of = out.outputFormatForBus_(0)
    print(f"[{label}] start ok={bool(ok)} vp({vp}) "
          f"outfmt={of.channelCount()}ch@{of.sampleRate():.0f}Hz "
          f"err={err.localizedDescription() if err else None}", flush=True)
    if ok:
        time.sleep(0.3)
        engine.stop()


for label, kw in [
    ("bare", {}),
    ("mixer", {"mixer": True}),
    ("mixer+player@outfmt", {"mixer": True, "player": True}),
    ("mixer+player@22k", {"mixer": True, "player": True, "player_sr": 22050.0}),
    ("player->output(no-mixer)", {"direct_out": True}),
]:
    try:
        try_start(label, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] EXCEPTION {exc!r}", flush=True)

sys.stdout.flush()
os._exit(0)
