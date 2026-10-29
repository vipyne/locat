"""Shared pipeline construction — one brain, every transport.

`build_pipeline(transport)` assembles the full-duplex pipeline used by every bot:

    transport.input() -> VAD -> STT -> user-context -> LLM -> TTS -> transport.output() -> assistant-context

Retrieval is no longer a pipeline stage. When an index exists the conversation
model is offered one tool, `ask_financial_records` (tools.py), whose handler
runs the records agent outside the pipeline.

Bot files keep only what differs per transport: transport construction, preflight,
greeting, and runner glue.
"""

import re
import time
from dataclasses import dataclass

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import (see config.py).
import config

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    ExternalFunctionCallCancelFrame,
    ExternalFunctionCallInProgressFrame,
    ExternalFunctionCallResultFrame,
    ExternalFunctionCallsStartedFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregator,
    LLMContextAggregatorPair,
    LLMUserAggregator,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import BaseTransport

import ingest
import store
import tools
from rag_agent import grounded_question, strip_think
import services
from services import (
    build_backend_llm,
    build_cascade_llm,
    build_llm,
    build_online_backend_llm,
    build_online_llm,
    build_online_stt,
    build_online_tts,
    build_stt,
    build_tts,
)
from silence_watchdog import SilenceWatchdog
from tunables import (
    ANALYST_SYSTEM_PROMPT,
    LOFI_BACKEND_PAIRING_INSTRUCTION,
    LOFI_FRONTEND_PAIRING_INSTRUCTION,
    DELEGATE_TOOL_DESCRIPTION,
    DELEGATE_TOOL_NAME,
    LOFI_FILLER_PHRASE,
    QUESTION_PREFIX,
    SPLIT_FRONT_PROMPT,
    online_analyst_prompt,
    online_front_prompt,
    online_system_prompt,
    simple_chat_prompt,
    system_prompt,
)

ONLINE_MODE = "online"
OFFLINE_MODE = "offline"


@dataclass
class BuiltBot:
    pipeline: Pipeline
    context: LLMContext
    user_aggregator: LLMUserAggregator
    assistant_aggregator: LLMAssistantAggregator
    llm: object
    tts: object


def build_vad_processor() -> VADProcessor:
    return VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                confidence=config.vad_confidence(),
                min_volume=config.vad_min_volume(),
                start_secs=config.vad_start_secs(),
                stop_secs=config.vad_stop_secs(),
            )
        )
    )


BUSY_MESSAGE_TYPE = tools.BUSY_MESSAGE_TYPE

_missing_index_logged = False


def _index_ready(db_path) -> bool:
    global _missing_index_logged
    if store.has_index(db_path):
        return True
    if not _missing_index_logged:
        logger.info(f"no RAG index found at {db_path} — run ./lofi.sh index-rag")
        _missing_index_logged = True
    return False


def build_records_tools() -> list | None:
    """Cascade only: the four tools go straight on the conversation model."""
    if config.architecture() != "cascade":
        return None
    db_path = config.rag_db_path()
    if not _index_ready(db_path):
        return None
    embedder = ingest.OllamaEmbedder(config.ollama_api_url(), config.embed_model())
    return tools.build_tools(db_path, embedder)


# Shown in the What's Running panel's rag field when an online session has no
# public index to retrieve from.
NO_PUBLIC_INDEX_NOTICE = "no public index — run ./lofi.sh index-rag with OPENAI_API_KEY set"


def public_rag_summary() -> str:
    """`rag` field for an online session: public index stats, or the notice."""
    db_path = config.public_rag_db_path()
    if not store.has_index(db_path):
        return NO_PUBLIC_INDEX_NOTICE
    return store.stats_summary(db_path, config.public_rag_data_dir())


def build_public_tools() -> list | None:
    """Online mode's tools: semantic_search over the public index, or none.

    The public db is the only one online code opens — the private records db
    is not mounted into an online session at all.
    """
    db_path = config.public_rag_db_path()
    if not store.has_index(db_path):
        logger.info(f"{NO_PUBLIC_INDEX_NOTICE} ({db_path})")
        return None
    embedder = ingest.OpenAIEmbedder(
        config.online_embed_model(), services.online_key("OPENAI_API_KEY")
    )
    return tools.build_public_tools(db_path, embedder)


def build_records_connector():
    """BackendConnector tuned for a small local front model.

    Measured on qwen2.5:7b: a tool literally named "delegate" is never called
    (the model narrates instead), and the stock frontier-sized pairing
    instruction suppresses calling entirely — a domain-named tool plus the
    compact instruction (and front temperature 0, see build_pipeline) delegates
    3/3. The tool description is domain-worded for the same reason (see
    DELEGATE_TOOL_DESCRIPTION).
    """
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.frames.frames import LLMMessagesAppendFrame
    from pipecat.pipeline.llm_with_backend import (
        BACKEND_MESSAGE_PREFIX,
        BackendConnector,
        BackendError,
        BackendIdle,
        BackendOutput,
    )
    from pipecat.services.llm_service import FunctionCallParams

    class RecordsBackendConnector(BackendConnector):
        # Per-lull delivery state: something appended / something ran the front.
        _lull_appended = False
        _lull_ran_front = False
        @property
        def tools(self):
            async def handler(params: FunctionCallParams):
                await self.delegate(params)

            return [
                FunctionSchema(
                    name=DELEGATE_TOOL_NAME,
                    description=DELEGATE_TOOL_DESCRIPTION,
                    properties=self.request_strategy.tool_parameters,
                    required=self.request_strategy.tool_required,
                    handler=handler,
                ),
            ]

        # Ollama has no developer role, so backend notes reach the front as
        # USER messages — the stock per-message RELAY_NOTE then reads as a
        # standing user demand, and the 7B front re-relays the old result on
        # every later turn instead of delegating the new question. A bare
        # "Backend:" prefix delegates; the compact pairing instruction
        # already says to relay results once.
        def render_output(self, output):
            message = super().render_output(output)
            if message is not None and output.prefers_spoken and not output.is_thought:
                message = dict(message, content=f"{BACKEND_MESSAGE_PREFIX}{output.text}")
            return message

        # Analyst sometimes omits the spoken mark on its final answer; the
        # note then lands with run_llm=False and nothing runs the front — the
        # bot sits mute on a finished answer until the user speaks. When a
        # lull ends with appended output and no front run, nudge one: an
        # empty append with run_llm=True runs the front on the context it
        # already holds, waits out in-flight speech, and persists no message.
        #
        # Every front run triggered here also signals lofi-relay: the
        # watchdog's deadline armed at delegation restarts fresh for the
        # relay run, else it fires mid-relay and speaks a waiting line
        # after the answer already arrived.
        async def deliver(self, frontend, event):
            # Analyst's spoken answer goes straight to TTS: the stock path
            # re-runs the front to re-verbalize it (4.6s answer-to-speech)
            # and rewords away the source file. The
            # note still lands in context with run_llm=False, so follow-up
            # delegation reads a complete transcript and no front run can
            # fabricate. The worker's transform_output already stripped
            # <think> blocks and markdown from event.text.
            if isinstance(event, BackendOutput) and event.prefers_spoken and not event.is_thought:
                logger.debug(f"Backend spoken answer straight to TTS: {event.text!r}")
                message = self.render_output(event)
                if message is not None:
                    await self._append(
                        frontend, LLMMessagesAppendFrame(messages=[message], run_llm=False)
                    )
                await frontend.queue_frame(TTSSpeakFrame(event.text, append_to_context=False))
                await self._signal_relay(frontend)
                self._lull_appended = True
                self._lull_ran_front = True
                return
            await super().deliver(frontend, event)
            if isinstance(event, BackendOutput):
                if self.render_output(event) is not None:
                    self._lull_appended = True
                    if event.prefers_spoken:
                        self._lull_ran_front = True
                        await self._signal_relay(frontend)
            elif isinstance(event, BackendError):
                self._lull_ran_front = True
                await self._signal_relay(frontend)
            elif isinstance(event, BackendIdle):
                if self._lull_appended and not self._lull_ran_front:
                    await self._append(
                        frontend, LLMMessagesAppendFrame(messages=[], run_llm=True)
                    )
                    await self._signal_relay(frontend)
                self._lull_appended = False
                self._lull_ran_front = False

        async def _signal_relay(self, frontend):
            await frontend.push_frame(
                RTVIServerMessageFrame(data={"type": tools.RELAY_MESSAGE_TYPE})
            )

    return RecordsBackendConnector(
        frontend_pairing_instruction=LOFI_FRONTEND_PAIRING_INSTRUCTION,
        # Post-delegate re-run narrates the handoff — or worse: the temp-0 7B
        # sometimes ANSWERS on that run with an invented passphrase, and
        # narrations accumulate in context as complete-looking answers the
        # front then imitates without the tool call (verify with
        # evals --stage followup). Off: the canned filler in build_pipeline
        # speaks instead. The online split keeps the spoken ack — its
        # frontier front is safe.
        respond_on_delegate=False,
    )


_MARKDOWN_EMPHASIS = re.compile(r"[*_]{1,3}([^*_]+?)[*_]{1,3}|`([^`]+)`")


def strip_markdown(text: str) -> str:
    """Drop emphasis/code markers the analyst sometimes adds — TTS reads them."""
    return _MARKDOWN_EMPHASIS.sub(lambda m: m.group(1) or m.group(2), text)


def analyst_system_prompt(db_path) -> str:
    """Analyst system message: ANALYST_SYSTEM_PROMPT + per-session grounding.

    grounded_question keeps grounding out of the system prompt for per-call
    paths; the backend context is built once per session, so folding grounding
    into the system message is equally cache-stable. The warmup prefills this
    same byte string, so it must match what build_records_backend installs.
    """
    grounding = grounded_question("", db_path).rsplit(QUESTION_PREFIX.rstrip(" "), 1)[0]
    return f"{ANALYST_SYSTEM_PROMPT}\n\n{grounding}"


def build_records_backend(db_path):
    """The split architecture's backend: a BackendLLMWorker holding the four
    retrieval tools and the analyst prompt (+ per-session date grounding).
    Thinking never reaches TTS: the worker renders output as spoken-or-note,
    and transform_output strips any inline <think> blocks as a backstop."""
    from pipecat.workers.llm.backend_llm_worker import BackendLLMWorker

    embedder = ingest.OllamaEmbedder(config.ollama_api_url(), config.embed_model())
    backend_system = analyst_system_prompt(db_path)

    async def _strip_think_output(output):
        text = strip_markdown(strip_think(output.text))
        if not text:
            return None
        output.text = text
        return output

    return BackendLLMWorker(
        llm=build_backend_llm(),
        context=LLMContext(
            messages=[{"role": "system", "content": backend_system}],
            tools=tools.build_backend_tools(db_path, embedder),
        ),
        transform_output=_strip_think_output,
        # Stock instruction inverts the >> mark on qwen3:8b — see the
        # LOFI_BACKEND_PAIRING_INSTRUCTION comment in tunables.py.
        pairing_instruction=LOFI_BACKEND_PAIRING_INSTRUCTION,
    )


class BackendToolPanel(FrameProcessor):
    """Mirrors the backend worker's tool calls into the UI's lofi-rag panel.

    The backend's handlers run outside the pipeline, so they can't push RTVI
    frames themselves; LLMWithBackend re-announces each call downstream as
    ExternalFunctionCall*Frames. Render each final result as the same lofi-rag
    message the cascade handlers send (tools.build_tools), and each call's
    start as a lofi-rag-pending message — an analyst turn runs 10-70 s, so the
    panel shows the lookup in flight instead of staying empty until it lands."""

    def __init__(self):
        super().__init__()
        self._started: dict[str, float] = {}

    async def _pending(self, tool: str, args: dict, active: bool) -> None:
        await self.push_frame(
            RTVIServerMessageFrame(
                data={
                    "type": tools.RAG_PENDING_MESSAGE_TYPE,
                    "tool": tool,
                    "args": args,
                    "active": active,
                }
            )
        )

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, ExternalFunctionCallsStartedFrame):
            for call in frame.function_calls:
                self._started[call.tool_call_id] = time.perf_counter()
                # Started phase carries name + id only; args arrive in_progress.
                await self._pending(call.function_name, {}, True)
        elif isinstance(frame, ExternalFunctionCallInProgressFrame):
            args = frame.arguments if isinstance(frame.arguments, dict) else {}
            await self._pending(frame.function_name, args, True)
        elif isinstance(frame, ExternalFunctionCallCancelFrame):
            self._started.pop(frame.tool_call_id, None)
            await self._pending(frame.function_name, {}, False)
        elif isinstance(frame, ExternalFunctionCallResultFrame) and frame.is_final:
            started = self._started.pop(frame.tool_call_id, None)
            result = frame.result if isinstance(frame.result, dict) else {}
            args = frame.arguments if isinstance(frame.arguments, dict) else {}
            elapsed_ms = result.get(
                "elapsed_ms",
                round((time.perf_counter() - started) * 1000, 2) if started else 0,
            )
            await self.push_frame(
                RTVIServerMessageFrame(
                    data={
                        "type": tools.RAG_MESSAGE_TYPE,
                        "tool": frame.function_name,
                        "args": args,
                        "count": tools._result_count(result),
                        "elapsed_ms": elapsed_ms,
                        "chunks": result.get("results", [])
                        if frame.function_name == "semantic_search"
                        else [],
                    }
                )
            )
        await self.push_frame(frame, direction)


def build_online_backend():
    """The online split's backend: a BackendLLMWorker holding semantic_search
    over the public index and the documents-analyst prompt. Stock pairing
    instruction — the frontier backend needs no small-model tuning."""
    from pipecat.workers.llm.backend_llm_worker import BackendLLMWorker

    embedder = ingest.OpenAIEmbedder(
        config.online_embed_model(), services.online_key("OPENAI_API_KEY")
    )
    return BackendLLMWorker(
        llm=build_online_backend_llm(),
        context=LLMContext(
            messages=[{"role": "system", "content": online_analyst_prompt()}],
            tools=tools.build_backend_tools(
                config.public_rag_db_path(), embedder, names=tools.PUBLIC_TOOL_NAMES
            ),
        ),
    )


def build_online_pipeline(transport: BaseTransport) -> BuiltBot:
    """Online session: Deepgram STT -> OpenAI front (-> OpenAI documents
    analyst on the llmwithbackend split) -> Cartesia TTS. Nothing here opens
    the private db or reaches Ollama."""
    public_tools = build_public_tools()
    splitting = config.online_architecture() == "llmwithbackend" and public_tools is not None
    if splitting:
        # Stock connector and pairing instructions: the point of the online
        # split is the untuned frontier path. The delegate tool is pipecat's.
        context = LLMContext(
            messages=[{"role": "system", "content": online_front_prompt()}]
        )
    else:
        prompt = online_system_prompt()
        context = (
            LLMContext(messages=[{"role": "system", "content": prompt}], tools=public_tools)
            if public_tools
            else LLMContext(messages=[{"role": "system", "content": prompt}])
        )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)
    front = build_online_llm()
    if splitting:
        from pipecat.pipeline.llm_with_backend import BackendConnector, LLMWithBackend

        llm = LLMWithBackend(
            frontend=front,
            backend=build_online_backend(),
            connector=BackendConnector(),
        )
    else:
        llm = front
    tts = build_online_tts()
    pipeline = Pipeline(
        [
            transport.input(),
            build_vad_processor(),
            build_online_stt(),
            user_aggregator,
            llm,
            *([BackendToolPanel()] if splitting else []),
            SilenceWatchdog(),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )
    return BuiltBot(
        pipeline=pipeline,
        context=context,
        user_aggregator=user_aggregator,
        assistant_aggregator=assistant_aggregator,
        llm=llm,
        tts=tts,
    )


def build_pipeline(transport: BaseTransport, mode: str = OFFLINE_MODE) -> BuiltBot:
    """Assemble the bot for one session.

    ``mode``: ``"online"`` builds the cloud cascade over the public index;
    every other value builds the offline stack.
    """
    if mode == ONLINE_MODE:
        return build_online_pipeline(transport)
    arch = config.architecture()
    simple_chat = arch == "cascade-simple-chat"
    if simple_chat:
        db_path = None
        records_tools = None
        splitting = False
        prompt = simple_chat_prompt()
    else:
        records_tools = build_records_tools()
        db_path = config.rag_db_path()
        splitting = arch == "llmwithbackend" and _index_ready(db_path)
    if splitting:
        # Load the analyst's weights AND prefill its system messages now, in
        # the background, so the first delegation pays neither the cold load
        # nor the system-prompt prefill (25s TTFB).
        # Message order mirrors the live request: pairing instruction first
        # (service system instruction), then the context's system message.
        services.warm_analyst(
            system_messages=[
                {"role": "system", "content": LOFI_BACKEND_PAIRING_INSTRUCTION},
                {"role": "system", "content": analyst_system_prompt(db_path)},
            ]
        )
        # The framework appends its own delegation guidance to this.
        prompt = SPLIT_FRONT_PROMPT
    elif not simple_chat:
        prompt = system_prompt(
            date_range=store.date_range(db_path) if records_tools else None,
            categories=store.categories(db_path) if records_tools else None,
        )
    context = (
        LLMContext(messages=[{"role": "system", "content": prompt}], tools=records_tools)
        if records_tools
        else LLMContext(messages=[{"role": "system", "content": prompt}])
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    if arch == "cascade":
        front = build_cascade_llm()
        # Load the cascade model's weights AND prefill the exact system prompt
        # it will run with, in the background — same reason as the split's
        # analyst warmup above (the first turn otherwise pays the prefill).
        services.warm_analyst(system_messages=[{"role": "system", "content": prompt}])
    else:
        # Split front runs at temperature 0: delegation reliability on a 7B
        # (measured 3/3 at 0, 2/3 at 0.7).
        front = build_llm(temperature=0 if splitting else None)
    if splitting:
        from pipecat.pipeline.llm_with_backend import LLMWithBackend

        llm = LLMWithBackend(
            frontend=front,
            backend=build_records_backend(db_path),
            connector=build_records_connector(),
        )
    else:
        llm = front
    tts = build_tts()

    # Filler covers a tool call or a delegation; simple-chat makes neither.
    if records_tools or splitting:

        @front.event_handler("on_function_calls_started")
        async def on_function_calls_started(service, function_calls):
            await service.push_frame(RTVIServerMessageFrame(data={"type": BUSY_MESSAGE_TYPE}))
            await tts.queue_frame(TTSSpeakFrame(LOFI_FILLER_PHRASE, append_to_context=False))

    pipeline = Pipeline(
        [
            transport.input(),
            build_vad_processor(),
            build_stt(),
            user_aggregator,
            llm,
            *([BackendToolPanel()] if splitting else []),
            SilenceWatchdog(),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )
    return BuiltBot(
        pipeline=pipeline,
        context=context,
        user_aggregator=user_aggregator,
        assistant_aggregator=assistant_aggregator,
        llm=llm,
        tts=tts,
    )
