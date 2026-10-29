"""Tests for the tool wiring in build_pipeline: the conversation model is
offered `ask_financial_records` exactly when an index exists, and no tools at
all when it doesn't. Replaces tests/test_pipeline_rag.py.

The STT/LLM/TTS builders and the VAD stage are stubbed — this asserts the shape
of the context, not that models load.
"""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_context import is_given
from pipecat.processors.frame_processor import FrameProcessor

import ingest
import pipeline
import services
import tools

FIXTURES = Path(__file__).parent / "fixtures"
TRANSACTIONS_CSV = FIXTURES / "transactions-small.csv"


@pytest.fixture
def db(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(TRANSACTIONS_CSV, data_dir / "transactions.csv")
    shutil.copy(FIXTURES / "note.txt", data_dir / "note.txt")
    db_path = tmp_path / "lofi.db"
    ingest.ingest(data_dir, db_path, ingest.FakeEmbedder())
    return db_path


@pytest.fixture
def stub_services(monkeypatch):
    # Generic pipeline-shape tests run on cascade: split needs real LLM
    # services (LLMWithBackend wraps them) and has its own dedicated tests.
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade")
    for name in ("build_stt", "build_llm", "build_tts", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda *a, **kw: FrameProcessor())


def fake_transport():
    return SimpleNamespace(input=FrameProcessor, output=FrameProcessor)


def tool_names(context) -> list[str]:
    if not is_given(context.tools):
        return []
    return [tool.name for tool in context.tools.standard_tools]


def test_index_present_offers_the_four_retrieval_tools(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())
    assert sorted(tool_names(built.context)) == [
        "aggregate", "filter_rows", "lookup", "semantic_search",
    ]


def test_system_prompt_is_grounded_when_index_present(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade")
    import datetime

    import store
    import tunables

    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())
    content = built.context.messages[0]["content"]
    assert "Today's date is" in content
    first, last = store.date_range(db)
    assert f"The transaction records run from {first} to {last}." in content
    assert "The transaction categories are: " in content
    assert content.startswith(tunables.LOFI_OFFLINE_CASCADE_PROMPT)
    pinned = tunables.system_prompt(datetime.date(2026, 10, 12), (first, last))
    assert pinned.startswith(tunables.LOFI_OFFLINE_CASCADE_PROMPT)
    assert pinned.endswith(f"Today's date is 2026-10-12. The transaction records run from {first} to {last}.")


def test_no_index_offers_no_tools(tmp_path, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_RAG_DB", str(tmp_path / "missing" / "lofi.db"))
    built = pipeline.build_pipeline(fake_transport())
    assert tool_names(built.context) == []


def test_pipeline_still_builds_without_an_index(tmp_path, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_RAG_DB", str(tmp_path / "missing" / "lofi.db"))
    built = pipeline.build_pipeline(fake_transport())
    assert built.pipeline is not None
    assert built.context.messages[0]["role"] == "system"


def test_built_bot_exposes_llm_and_tts(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())
    assert built.llm is not None
    assert built.tts is not None


class EventfulLLM(FrameProcessor):
    """FrameProcessor that, like a real LLM service, has the function-call event."""

    def __init__(self):
        super().__init__()
        self._register_event_handler("on_function_calls_started")


def test_filler_registered_on_function_calls_started(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    monkeypatch.setattr(pipeline, "build_cascade_llm", lambda *a, **kw: EventfulLLM())
    built = pipeline.build_pipeline(fake_transport())
    assert built.llm._event_handlers["on_function_calls_started"].handlers


def test_filler_phrase_is_short_spoken_text(db):
    assert pipeline.LOFI_FILLER_PHRASE.endswith(".")
    assert "{" not in pipeline.LOFI_FILLER_PHRASE


def test_filler_handler_pushes_busy_message_and_filler(db, monkeypatch, stub_services):
    import asyncio

    from pipecat.frames.frames import TTSSpeakFrame
    from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

    class RecordingLLM(EventfulLLM):
        def __init__(self):
            super().__init__()
            self.frames = []

        async def push_frame(self, frame, *args, **kwargs):
            self.frames.append(frame)

    class RecordingTTS(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.queued = []

        async def queue_frame(self, frame, *args, **kwargs):
            self.queued.append(frame)

    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    monkeypatch.setattr(pipeline, "build_cascade_llm", lambda *a, **kw: RecordingLLM())
    monkeypatch.setattr(pipeline, "build_tts", RecordingTTS)
    built = pipeline.build_pipeline(fake_transport())
    handler = built.llm._event_handlers["on_function_calls_started"].handlers[0]
    asyncio.run(handler(built.llm, []))

    busy = [f for f in built.llm.frames if isinstance(f, RTVIServerMessageFrame)]
    assert busy and busy[0].data == {"type": pipeline.BUSY_MESSAGE_TYPE}
    assert [f.text for f in built.tts.queued if isinstance(f, TTSSpeakFrame)] == [
        pipeline.LOFI_FILLER_PHRASE
    ]


def test_bot_entry_points_import_cleanly():
    import importlib

    for module in ("bot", "bot_browser"):
        importlib.import_module(module)


def test_llm_service_disables_reasoning():
    import services

    llm = services.build_llm()
    assert llm._settings.extra == {"extra_body": {"reasoning_effort": "none"}}


def test_invalid_architecture_exits_with_guidance(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    monkeypatch.setenv("LOFI_ARCHITECTURE", "sandwich")
    with pytest.raises(SystemExit):
        pipeline.build_pipeline(fake_transport())


def test_pipeline_includes_the_silence_watchdog(db, monkeypatch, stub_services):
    from silence_watchdog import SilenceWatchdog

    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())
    assert any(isinstance(p, SilenceWatchdog) for p in built.pipeline._processors)


def test_split_architecture_builds_llm_with_backend(db, monkeypatch):
    # Real LLM services (construction is offline); stub only STT/TTS/VAD.
    from pipecat.pipeline.llm_with_backend import LLMWithBackend

    import tunables

    for name in ("build_stt", "build_tts", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda: FrameProcessor())
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())

    # The front holds no records tools — delegate is a builtin, not a context tool.
    assert tool_names(built.context) == []
    assert built.context.messages[0]["content"].startswith(tunables.SPLIT_FRONT_PROMPT)

    lwb = next(p for p in built.pipeline._processors if isinstance(p, LLMWithBackend))
    worker = lwb.backend
    assert sorted(tool_names(worker.context)) == [
        "aggregate", "filter_rows", "lookup", "semantic_search",
    ]
    backend_system = worker.context.messages[0]["content"]
    assert tunables.ANALYST_SYSTEM_PROMPT in backend_system
    assert "Today's date is" in backend_system


def test_split_frontend_gets_domain_named_delegate(db, monkeypatch):
    # qwen2.5:7b never calls a tool named "delegate" (measured 0%); renamed to
    # a domain term it calls reliably — the connector installs it accordingly.
    from pipecat.pipeline.llm_with_backend import LLMWithBackend

    for name in ("build_stt", "build_tts", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda: FrameProcessor())
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())

    lwb = next(p for p in built.pipeline._processors if isinstance(p, LLMWithBackend))
    builtin = lwb.frontend.get_llm_adapter().builtin_tools
    assert list(builtin) == ["ask_financial_records"]
    assert "delegate" not in builtin


def test_backend_worker_uses_LOFI_FRONTEND_PAIRING_INSTRUCTION(db):
    # Compact instruction replaces the stock frontier-sized one (Task 15).
    import tunables
    from pipecat.workers.llm.backend_llm_worker import BACKEND_PAIRING_INSTRUCTION

    worker = pipeline.build_records_backend(str(db))
    appended = worker.llm._appended_system_instructions
    assert tunables.LOFI_BACKEND_PAIRING_INSTRUCTION in appended
    assert BACKEND_PAIRING_INSTRUCTION not in appended


def test_backend_transform_strips_think_and_is_awaitable(db):
    import asyncio

    from types import SimpleNamespace

    worker = pipeline.build_records_backend(str(db))
    transform = worker._transform_output
    out = SimpleNamespace(text="<think>hmm</think>You spent $40.00.")
    result = asyncio.run(transform(out))
    assert result.text == "You spent $40.00."
    only_think = SimpleNamespace(text="<think>all reasoning</think>")
    assert asyncio.run(transform(only_think)) is None


def test_split_pipeline_includes_backend_tool_panel(db, monkeypatch):
    for name in ("build_stt", "build_tts", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda: FrameProcessor())
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())
    assert any(isinstance(p, pipeline.BackendToolPanel) for p in built.pipeline._processors)


def test_cascade_pipeline_has_no_backend_tool_panel(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())
    assert not any(isinstance(p, pipeline.BackendToolPanel) for p in built.pipeline._processors)


def test_backend_tool_panel_renders_lofi_rag_message(db):
    import asyncio

    from pipecat.frames.frames import (
        ExternalFunctionCall,
        ExternalFunctionCallResultFrame,
        ExternalFunctionCallsStartedFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

    panel = pipeline.BackendToolPanel()
    pushed = []

    async def record(frame, *args, **kwargs):
        pushed.append(frame)

    panel.push_frame = record

    async def run():
        await panel.process_frame(
            ExternalFunctionCallsStartedFrame(
                function_calls=[ExternalFunctionCall("semantic_search", "call-1")]
            ),
            FrameDirection.DOWNSTREAM,
        )
        await panel.process_frame(
            ExternalFunctionCallResultFrame(
                function_name="semantic_search",
                tool_call_id="call-1",
                arguments={"query": "secret passphrase"},
                result={"results": [{"source": "x.txt", "page": None, "text": "hi"}]},
            ),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    rag = [
        f for f in pushed
        if isinstance(f, RTVIServerMessageFrame) and f.data.get("type") == tools.RAG_MESSAGE_TYPE
    ]
    assert len(rag) == 1
    data = rag[0].data
    assert data["tool"] == "semantic_search"
    assert data["args"] == {"query": "secret passphrase"}
    assert data["count"] == 1
    assert data["chunks"] == [{"source": "x.txt", "page": None, "text": "hi"}]
    assert data["elapsed_ms"] >= 0


def test_spoken_backend_output_renders_without_relay_note():
    # Ollama maps developer-role notes to USER messages; the stock RELAY_NOTE
    # then re-triggers the relay on every later turn (measured: the front
    # repeats the old result instead of delegating the new question).
    from pipecat.pipeline.llm_with_backend import BACKEND_MESSAGE_PREFIX

    connector = pipeline.build_records_connector()

    spoken = SimpleNamespace(text="You spent $40.00.", prefers_spoken=True, is_thought=False)
    message = connector.render_output(spoken)
    assert message["content"] == f"{BACKEND_MESSAGE_PREFIX}You spent $40.00."
    assert "Relay this" not in message["content"]
    assert message["role"] == "developer"

    note = SimpleNamespace(text="checked rows", prefers_spoken=False, is_thought=False)
    assert "Relay this" not in connector.render_output(note)["content"]


def test_backend_transform_strips_markdown(db):
    import asyncio

    worker = pipeline.build_records_backend(str(db))
    out = SimpleNamespace(text="It is **BLUE MANATEE 436921**, in *passphrase-smoke.txt* (`data/`).")
    assert asyncio.run(worker._transform_output(out)).text == (
        "It is BLUE MANATEE 436921, in passphrase-smoke.txt (data/)."
    )


class RecordingTTSService(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.queued = []
        self.pushed = []

    async def queue_frame(self, frame, *args, **kwargs):
        self.queued.append(frame)

    async def push_frame(self, frame, *args, **kwargs):
        self.pushed.append(frame)


def test_cascade_filler_is_spoken_but_kept_out_of_context(db, monkeypatch, stub_services):
    import asyncio

    from pipecat.frames.frames import TTSSpeakFrame

    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    monkeypatch.setattr(pipeline, "build_cascade_llm", lambda *a, **kw: EventfulLLM())
    monkeypatch.setattr(pipeline, "build_tts", RecordingTTSService)
    built = pipeline.build_pipeline(fake_transport())
    handler = built.llm._event_handlers["on_function_calls_started"].handlers[0]
    asyncio.run(handler(built.llm, []))

    fillers = [f for f in built.tts.queued if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in fillers] == [pipeline.LOFI_FILLER_PHRASE]
    # Canned waiting lines in the context teach a small front model to answer
    # with waiting lines (and then invent results) instead of delegating.
    assert fillers[0].append_to_context is False


def test_split_speaks_canned_filler_out_of_context(db, monkeypatch):
    import asyncio

    from pipecat.frames.frames import TTSSpeakFrame

    for name in ("build_stt", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda: FrameProcessor())
    monkeypatch.setattr(pipeline, "build_tts", RecordingTTSService)
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    built = pipeline.build_pipeline(fake_transport())

    front = built.llm.frontend
    handler = front._event_handlers["on_function_calls_started"].handlers[0]
    asyncio.run(handler(front, []))
    fillers = [f for f in built.tts.queued if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in fillers] == [pipeline.LOFI_FILLER_PHRASE]
    # Waiting lines in the context teach the temp-0 front to answer records
    # questions with waiting lines and no tool call.
    assert fillers[0].append_to_context is False


def test_split_connector_does_not_rerun_front_on_delegate():
    # The post-delegate re-run narrates — or ANSWERS: the temp-0 7B spoke an
    # invented passphrase on that run. Off: the canned filler speaks instead;
    # the online split keeps the spoken ack.
    connector = pipeline.build_records_connector()
    assert connector._respond_on_delegate is False


def _deliver_all(connector, frontend, events):
    import asyncio

    async def run():
        for event in events:
            await connector.deliver(frontend, event)

    asyncio.run(run())


def _nudges(frontend):
    from pipecat.frames.frames import LLMMessagesAppendFrame

    return [
        f
        for f in frontend.queued
        if isinstance(f, LLMMessagesAppendFrame) and f.messages == [] and f.run_llm is True
    ]


def test_unmarked_final_answer_still_runs_the_front():
    # Analyst sometimes omits the spoken mark on its final answer; the note
    # lands with run_llm=False and the bot sits mute on a finished answer
    # until the user speaks again.
    from pipecat.frames.frames import LLMMessagesAppendFrame
    from pipecat.workers.llm.backend_llm_worker import BackendIdle, BackendOutput

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(
        connector,
        frontend,
        [BackendOutput(text="You visited three times.", prefers_spoken=False), BackendIdle()],
    )

    appends = [f for f in frontend.queued if isinstance(f, LLMMessagesAppendFrame)]
    assert len(appends) == 2
    note, nudge = appends
    assert note.messages and note.run_llm is not True
    assert nudge.messages == [] and nudge.run_llm is True


def test_spoken_final_answer_gets_no_nudge():
    from pipecat.workers.llm.backend_llm_worker import BackendIdle, BackendOutput

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(
        connector,
        frontend,
        [BackendOutput(text="You visited three times.", prefers_spoken=True), BackendIdle()],
    )
    assert _nudges(frontend) == []


def test_spoken_final_answer_goes_straight_to_tts():
    # The analyst's spoken answer skips the front relay (4.6s
    # answer-to-speech): TTSSpeakFrame to TTS, note appended with
    # run_llm=False so follow-ups still read a complete transcript.
    from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
    from pipecat.workers.llm.backend_llm_worker import BackendIdle, BackendOutput

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(
        connector,
        frontend,
        [BackendOutput(text="You visited three times.", prefers_spoken=True), BackendIdle()],
    )

    speaks = [f for f in frontend.queued if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in speaks] == ["You visited three times."]
    assert speaks[0].append_to_context is False
    appends = [f for f in frontend.queued if isinstance(f, LLMMessagesAppendFrame)]
    assert len(appends) == 1
    assert appends[0].messages and appends[0].run_llm is False
    assert "You visited three times." in appends[0].messages[0]["content"]


def test_backend_error_gets_no_nudge():
    # The error path already appends its own run_llm=True message.
    from pipecat.workers.llm.backend_llm_worker import BackendError, BackendIdle

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(connector, frontend, [BackendError(error="boom"), BackendIdle()])
    assert _nudges(frontend) == []


def test_nudge_tracking_resets_each_lull():
    from pipecat.workers.llm.backend_llm_worker import BackendIdle, BackendOutput

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(
        connector,
        frontend,
        [
            BackendOutput(text="note only", prefers_spoken=False),
            BackendIdle(),
            BackendOutput(text="Spoken answer.", prefers_spoken=True),
            BackendIdle(),
        ],
    )
    assert len(_nudges(frontend)) == 1


def _relay_signals(frontend):
    from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

    return [
        f
        for f in frontend.pushed
        if isinstance(f, RTVIServerMessageFrame) and f.data.get("type") == tools.RELAY_MESSAGE_TYPE
    ]


def test_every_delivery_front_run_signals_relay():
    # Each front run triggered by a delivery (spoken answer, error, idle
    # nudge) restarts the watchdog timer: the deadline armed at delegation
    # otherwise fires mid-relay, speaking a waiting line after the answer
    # already arrived.
    from pipecat.workers.llm.backend_llm_worker import BackendError, BackendIdle, BackendOutput

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(
        connector,
        frontend,
        [
            BackendOutput(text="Spoken answer.", prefers_spoken=True),
            BackendIdle(),
            BackendError(error="boom"),
            BackendIdle(),
            BackendOutput(text="unmarked answer", prefers_spoken=False),
            BackendIdle(),
        ],
    )
    assert len(_relay_signals(frontend)) == 3


def test_silent_notes_do_not_signal_relay():
    from pipecat.workers.llm.backend_llm_worker import BackendOutput

    connector = pipeline.build_records_connector()
    frontend = RecordingTTSService()
    _deliver_all(
        connector,
        frontend,
        [BackendOutput(text="note only", prefers_spoken=False, is_thought=True)],
    )
    assert _relay_signals(frontend) == []


def test_watchdog_reminder_skips_context():
    import asyncio

    from pipecat.frames.frames import TTSSpeakFrame

    from silence_watchdog import SilenceWatchdog

    watchdog = SilenceWatchdog(interval_secs=0.01, max_reminders=1)
    pushed = []

    async def record(frame, *args, **kwargs):
        pushed.append(frame)

    watchdog.push_frame = record

    async def run():
        await watchdog._remind()

    asyncio.run(run())
    reminders = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert reminders and all(f.append_to_context is False for f in reminders)


def test_backend_tool_panel_announces_pending_call(db):
    """Started/in-progress phases reach the UI so a slow lookup shows activity."""
    import asyncio

    from pipecat.frames.frames import (
        ExternalFunctionCall,
        ExternalFunctionCallCancelFrame,
        ExternalFunctionCallInProgressFrame,
        ExternalFunctionCallResultFrame,
        ExternalFunctionCallsStartedFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

    panel = pipeline.BackendToolPanel()
    pushed = []

    async def record(frame, *args, **kwargs):
        pushed.append(frame)

    panel.push_frame = record

    def messages(message_type):
        return [
            f.data
            for f in pushed
            if isinstance(f, RTVIServerMessageFrame) and f.data.get("type") == message_type
        ]

    async def run():
        await panel.process_frame(
            ExternalFunctionCallsStartedFrame(
                function_calls=[ExternalFunctionCall("semantic_search", "call-1")]
            ),
            FrameDirection.DOWNSTREAM,
        )
        await panel.process_frame(
            ExternalFunctionCallInProgressFrame(
                function_name="semantic_search",
                tool_call_id="call-1",
                arguments={"query": "secret passphrase"},
            ),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    pending = messages(tools.RAG_PENDING_MESSAGE_TYPE)
    assert len(pending) == 2
    assert pending[0] == {
        "type": tools.RAG_PENDING_MESSAGE_TYPE,
        "tool": "semantic_search",
        "args": {},
        "active": True,
    }
    assert pending[1]["args"] == {"query": "secret passphrase"}
    assert pending[1]["active"] is True
    # No route line until the result lands.
    assert messages(tools.RAG_MESSAGE_TYPE) == []

    async def finish():
        await panel.process_frame(
            ExternalFunctionCallResultFrame(
                function_name="semantic_search",
                tool_call_id="call-1",
                arguments={"query": "secret passphrase"},
                result={"results": []},
            ),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(finish())
    assert len(messages(tools.RAG_MESSAGE_TYPE)) == 1
    # Result ends the call; no extra pending message needed to clear it.
    assert len(messages(tools.RAG_PENDING_MESSAGE_TYPE)) == 2

    async def cancel():
        await panel.process_frame(
            ExternalFunctionCallsStartedFrame(
                function_calls=[ExternalFunctionCall("sql_query", "call-2")]
            ),
            FrameDirection.DOWNSTREAM,
        )
        await panel.process_frame(
            ExternalFunctionCallCancelFrame(function_name="sql_query", tool_call_id="call-2"),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(cancel())
    pending = messages(tools.RAG_PENDING_MESSAGE_TYPE)
    assert pending[-2]["tool"] == "sql_query" and pending[-2]["active"] is True
    assert pending[-1] == {
        "type": tools.RAG_PENDING_MESSAGE_TYPE,
        "tool": "sql_query",
        "args": {},
        "active": False,
    }


def test_split_pipeline_warms_the_analyst(db, monkeypatch):
    for name in ("build_stt", "build_tts", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda: FrameProcessor())
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    calls: list = []
    monkeypatch.setattr(
        services, "warm_analyst", lambda system_messages=None: calls.append(system_messages)
    )

    async def build():
        pipeline.build_pipeline(fake_transport())

    asyncio.run(build())
    # Warmup prefills the exact live composition: pairing instruction first,
    # then the analyst system message.
    assert len(calls) == 1
    import tunables

    pairing, system = calls[0]
    assert pairing["content"] == tunables.LOFI_BACKEND_PAIRING_INSTRUCTION
    assert system["content"].startswith(tunables.ANALYST_SYSTEM_PROMPT)


def test_cascade_pipeline_warms_its_own_model(db, monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade")
    monkeypatch.setenv("LOFI_RAG_DB", str(db))
    calls: list = []
    monkeypatch.setattr(
        services, "warm_analyst", lambda system_messages=None: calls.append(system_messages)
    )

    async def build():
        return pipeline.build_pipeline(fake_transport())

    built = asyncio.run(build())
    # Warmup prefills the one system message the cascade runs with, verbatim.
    assert len(calls) == 1
    (system,) = calls[0]
    assert system["content"] == built.context.messages[0]["content"]


def test_simple_chat_pipeline_warms_nothing(monkeypatch, stub_services):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade-simple-chat")
    calls: list = []
    monkeypatch.setattr(
        services, "warm_analyst", lambda system_messages=None: calls.append(system_messages)
    )

    async def build():
        pipeline.build_pipeline(fake_transport())

    asyncio.run(build())
    assert calls == []
