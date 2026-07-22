"""scripts/check_audio.py — isolate the microphone input path.

Diagnostic for "the bot speaks but never responds / won't be interrupted".
Both of those symptoms mean the INPUT half of the pipeline is dead: no audio in
-> Silero VAD never fires -> no STT -> no LLM reply, and no VAD -> no barge-in.

This script bypasses Pipecat entirely and reads the raw microphone with PyAudio
(same format Pipecat uses: 16 kHz mono int16), then prints a live level meter.

Run it, then TALK:

    uv run python scripts/check_audio.py

- If the bars MOVE when you speak  -> the mic works; the bug is downstream
  (VAD / STT / turn-taking / LLM), not the mic.
- If the bars stay flat near zero  -> no audio is reaching Python. Almost always
  one of: (a) macOS microphone permission not granted to your terminal, or
  (b) the wrong default input device. The device list printed below helps with (b);
  for (a) see System Settings > Privacy & Security > Microphone.
"""

import sys
import time

try:
    import pyaudio
except ImportError:
    sys.exit("pyaudio not installed. Run: uv sync  (or: uv add pyaudio)")

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = int(RATE / 100) * 2  # 20 ms, matching LocalAudioInputTransport
SECONDS = 8


def list_devices(pa: "pyaudio.PyAudio") -> None:
    """Print every input-capable device and the system default input."""
    print("=== Input-capable audio devices ===")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) > 0:
            print(f"  [{i:2d}] {info['name']}  "
                  f"(in-ch={info['maxInputChannels']}, {int(info['defaultSampleRate'])} Hz)")
    try:
        default = pa.get_default_input_device_info()
        print(f"\nDefault input device: [{default['index']}] {default['name']}")
        print("(Set INPUT_DEVICE_INDEX in .env to override which mic bot.py uses.)")
    except Exception as exc:  # noqa: BLE001
        print(f"\n!! No default input device: {exc}")


def level_meter(pa: "pyaudio.PyAudio") -> None:
    """Open the default mic and print a live peak-amplitude meter."""
    import audioop  # stdlib; simple RMS/peak without numpy

    print(f"\n=== Level meter — TALK NOW for ~{SECONDS}s (Ctrl-C to stop) ===")
    stream = pa.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                     input=True, frames_per_buffer=CHUNK)
    peak_seen = 0
    end = time.monotonic() + SECONDS
    try:
        while time.monotonic() < end:
            data = stream.read(CHUNK, exception_on_overflow=False)
            peak = audioop.max(data, 2)  # 0..32767
            peak_seen = max(peak_seen, peak)
            bars = int((peak / 32767) * 50)
            print(f"\r  |{'#' * bars:<50}| {peak:5d}", end="", flush=True)
    finally:
        stream.stop_stream()
        stream.close()
    import math
    dbfs = 20 * math.log10(peak_seen / 32767) if peak_seen > 0 else float("-inf")
    print(f"\n\nMax peak observed: {peak_seen} / 32767  ({dbfs:.0f} dBFS)")
    if peak_seen < 200:
        print("VERDICT: input is DEAD (flat near zero). No audio reaching Python —")
        print("  fix macOS mic permission or the input device. Not a code bug.")
    elif peak_seen < 2000:
        print("VERDICT: audio flows but is TOO QUIET (< ~-24 dBFS). Silero VAD likely")
        print("  never triggers at this level, so the bot never hears you and can't be")
        print("  interrupted. Raise macOS input volume / pick the right mic / get closer,")
        print("  then re-run and aim for a peak in the THOUSANDS. Not a code bug.")
    else:
        print("VERDICT: healthy input level. If the bot still doesn't respond, the bug")
        print("  is downstream (VAD threshold / STT / turn-taking) — investigate there.")


def main() -> None:
    pa = pyaudio.PyAudio()
    try:
        list_devices(pa)
        level_meter(pa)
    finally:
        pa.terminate()


if __name__ == "__main__":
    main()
