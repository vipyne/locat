"""Probe: is the AUVoiceProcessingIO component reachable from Python via ctypes?

Approach-2 assessment (SPIKE_VPIO.md). This only checks component discovery +
instantiation + initialize — NOT the render-callback plumbing, which is the
actual hard part (RT-thread C callbacks). Run: uv run python probe_auvpio_ctypes.py
"""

import ctypes
import ctypes.util
import struct
import sys

at = ctypes.CDLL("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")


def fourcc(s: str) -> int:
    return struct.unpack(">I", s.encode("ascii"))[0]


class AudioComponentDescription(ctypes.Structure):
    _fields_ = [
        ("componentType", ctypes.c_uint32),
        ("componentSubType", ctypes.c_uint32),
        ("componentManufacturer", ctypes.c_uint32),
        ("componentFlags", ctypes.c_uint32),
        ("componentFlagsMask", ctypes.c_uint32),
    ]


checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


desc = AudioComponentDescription(
    fourcc("auou"), fourcc("vpio"), fourcc("appl"), 0, 0
)

at.AudioComponentFindNext.restype = ctypes.c_void_p
at.AudioComponentFindNext.argtypes = [ctypes.c_void_p, ctypes.POINTER(AudioComponentDescription)]
comp = at.AudioComponentFindNext(None, ctypes.byref(desc))
check("AudioComponentFindNext(auou/vpio/appl)", comp is not None, f"component={comp:#x}" if comp else "not found")
if not comp:
    sys.exit(1)

instance = ctypes.c_void_p()
status = at.AudioComponentInstanceNew(ctypes.c_void_p(comp), ctypes.byref(instance))
check("AudioComponentInstanceNew", status == 0 and instance.value, f"status={status}")

# kAudioOutputUnitProperty_EnableIO = 2003, input scope=1 element=1
one = ctypes.c_uint32(1)
status = at.AudioUnitSetProperty(instance, 2003, 1, 1, ctypes.byref(one), 4)
check("EnableIO on input bus", status == 0, f"status={status}")

status = at.AudioUnitInitialize(instance)
check("AudioUnitInitialize", status == 0, f"status={status}")

if status == 0:
    at.AudioUnitUninitialize(instance)
at.AudioComponentInstanceDispose(instance)
print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
