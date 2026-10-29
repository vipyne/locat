"""Per-service offline/online resolution — one record for a whole session.

Three knobs pin a service regardless of what the connect asks for:
``LOCAT_STT_MODE``, ``LOCAT_LLM_MODE``, ``LOCAT_TTS_MODE`` (config.py). An
unpinned service follows the session; an unrecognized session is offline.

    resolve_modes(session_mode(runner_args)) -> validate_modes(modes)

The LLM mode is the brain mode: persona, architecture, and which RAG db opens
all key off ``modes.llm``, and an online brain reads only the public index.
"""

from dataclasses import dataclass

import config

ONLINE_MODE = "online"
OFFLINE_MODE = "offline"
SESSION_MODE = "session"

SERVICES = ("stt", "llm", "tts")


@dataclass(frozen=True)
class SessionModes:
    """Effective mode per service, plus the session mode they resolved from."""

    session: str
    stt: str
    llm: str
    tts: str

    @property
    def online_services(self) -> tuple[str, ...]:
        return tuple(s for s in SERVICES if getattr(self, s) == ONLINE_MODE)

    @property
    def offline_services(self) -> tuple[str, ...]:
        return tuple(s for s in SERVICES if getattr(self, s) == OFFLINE_MODE)

    @property
    def any_online(self) -> bool:
        return bool(self.online_services)

    @property
    def all_offline(self) -> bool:
        return not self.online_services


_PINS = {"stt": config.stt_mode, "llm": config.llm_mode, "tts": config.tts_mode}


def resolve_modes(session: str | None) -> SessionModes:
    """Fold the session mode and the three pins into one record.

    Precedence per service: pin > session > offline. ``cascade-simple-chat``
    is local-only, so it forces every service offline.
    """
    base = session if session in (OFFLINE_MODE, ONLINE_MODE) else OFFLINE_MODE
    if config.architecture() == "cascade-simple-chat":
        return SessionModes(
            session=OFFLINE_MODE, stt=OFFLINE_MODE, llm=OFFLINE_MODE, tts=OFFLINE_MODE
        )
    resolved = {}
    for service, pin in _PINS.items():
        value = pin()
        resolved[service] = base if value == SESSION_MODE else value
    return SessionModes(session=base, **resolved)


def validate_modes(session_modes: SessionModes) -> None:
    """Refuse a session this process can't serve, naming the fix.

    Serve flags win over pins: ``--offline-only`` is the airgap, so it refuses
    an online STT or TTS pin too. Missing cloud keys are reported for the
    online services only, all in one message.
    """
    if config.offline_only() and session_modes.any_online:
        crossing = ", ".join(session_modes.online_services)
        raise RuntimeError(
            f"this bot was started with --offline-only — {crossing} would "
            "cross the network. Clear the LOCAT_*_MODE pins in .locatrc, or "
            "restart without --offline-only."
        )
    if config.online_only() and session_modes.offline_services:
        local = ", ".join(session_modes.offline_services)
        raise RuntimeError(
            f"this bot was started with --online-only — the local stack is "
            f"not ready, so {local} cannot run offline. Clear the "
            "LOCAT_*_MODE pins in .locatrc, or restart without --online-only."
        )
    missing = config.missing_online_keys(services=session_modes.online_services)
    if missing:
        crossing = ", ".join(session_modes.online_services)
        raise RuntimeError(
            f"online {crossing} needs cloud credentials — missing from .env: "
            f"{', '.join(missing)}. Add them and reconnect, or run those "
            "services offline."
        )
