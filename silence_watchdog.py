"""Watchdog for dropped or slow turns: after the user stops speaking, if no
bot speech passes within the interval, speak a short reminder so a hung or
forgotten tool call never ends in dead air.

Armed by UserStoppedSpeakingFrame or a `lofi-busy` signal; disarmed by the
user speaking again or bot text flowing through. While busy (a records lookup
in flight), streamed LLM text does NOT disarm — while a backend works the front
chats while the analyst works, and that chatter must not silence the watchdog.
Busy clears on `lofi-idle`, a route line, a spoken answer, or `lofi-relay`
(backend answer delivered; relay run gets a fresh timer). The canned
filler bypasses this processor (queued directly on the TTS service), so a
spoken filler keeps it armed while a tool call runs. Reminders repeat up to
``max_reminders`` per turn, then it gives up quietly.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMTextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

from tools import BUSY_MESSAGE_TYPE, IDLE_MESSAGE_TYPE, RAG_MESSAGE_TYPE, RELAY_MESSAGE_TYPE
from tunables import LOFI_REMINDER_PHRASE

REMINDER_INTERVAL_SECS = 30.0
MAX_REMINDERS = 3


class SilenceWatchdog(FrameProcessor):
    def __init__(
        self,
        phrase: str = LOFI_REMINDER_PHRASE,
        interval_secs: float = REMINDER_INTERVAL_SECS,
        max_reminders: int = MAX_REMINDERS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._phrase = phrase
        self._interval_secs = interval_secs
        self._max_reminders = max_reminders
        self._task: asyncio.Task | None = None
        self._busy = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._arm()
        elif isinstance(frame, (UserStartedSpeakingFrame, EndFrame)):
            self._busy = False
            await self._disarm()
        elif isinstance(frame, RTVIServerMessageFrame):
            message_type = frame.data.get("type")
            if message_type == BUSY_MESSAGE_TYPE:
                self._busy = True
                self._arm()
            elif message_type == IDLE_MESSAGE_TYPE:
                self._busy = False
                await self._disarm()
            elif message_type == RAG_MESSAGE_TYPE:
                self._busy = False
            elif message_type == RELAY_MESSAGE_TYPE:
                # Lookup done, relay run starting: a deadline armed at
                # delegation would fire mid-relay — restart the timer fresh;
                # the relay's own text (busy now off) disarms it.
                self._busy = False
                self._arm()
        elif isinstance(frame, TTSSpeakFrame) and frame.text.strip():
            self._busy = False
            await self._disarm()
        elif isinstance(frame, LLMTextFrame) and frame.text.strip() and not self._busy:
            await self._disarm()

        await self.push_frame(frame, direction)

    def _arm(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = self.create_task(self._remind())

    async def _disarm(self) -> None:
        if self._task is not None and not self._task.done():
            await self.cancel_task(self._task)
        self._task = None

    async def _remind(self) -> None:
        for _ in range(self._max_reminders):
            await asyncio.sleep(self._interval_secs)
            # Spoken only — reminder lines in the LLM context teach a small
            # front model to answer with waiting lines (and then invent a
            # result) instead of delegating.
            await self.push_frame(TTSSpeakFrame(self._phrase, append_to_context=False))
