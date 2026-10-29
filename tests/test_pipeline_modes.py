"""Tests for `build_pipeline(transport, modes)` — the session stories.

Online mode is a cloud cascade over the PUBLIC index only. The privacy
guarantee is file-level, so these tests make the private db path and every
Ollama lookup raise: an online build that touches either one fails here.

Offline mode is the default, and anything that is not exactly "online" builds
it — a garbage mode never silently ships data to the cloud.

A SessionModes sets each service separately, so the last section covers mixed
sessions: an online brain with local audio still opens only the public index,
and a local brain with cloud audio never builds the OpenAI embedder.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger
from pipecat.processors.aggregators.llm_context import is_given
from pipecat.processors.frame_processor import FrameProcessor

import config
import ingest
import pipeline
import services
import tools
import tunables
from modes import SessionModes

FIXTURES = Path(__file__).parent / "fixtures"
ONLINE_KEYS = ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY")


@pytest.fixture
def fake_keys(monkeypatch):
    """All three online keys present, obviously fake."""
    for name in ONLINE_KEYS:
        monkeypatch.setenv(name, f"fake-{name.lower()}")


@pytest.fixture
def public_db(tmp_path, monkeypatch):
    """A public index built with FakeEmbedder, pointed at by config."""
    data_dir = tmp_path / "public"
    data_dir.mkdir()
    (data_dir / "pub17.txt").write_text(
        "The standard deduction for a single filer is reported in Publication 17."
    )
    db_path = tmp_path / "locat-public.db"
    ingest.ingest(data_dir, db_path, ingest.FakeEmbedder())
    monkeypatch.setenv("LOCAT_PUBLIC_RAG_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LOCAT_PUBLIC_RAG_DB", str(db_path))
    return db_path


@pytest.fixture
def stub_online_services(monkeypatch):
    # Generic online-shape tests run on cascade: the split needs real LLM
    # services (LLMWithBackend wraps them) and has its own dedicated tests.
    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "cascade")
    for name in ("build_online_stt", "build_online_tts"):
        monkeypatch.setattr(services, name, lambda *a, **kw: FrameProcessor())
    monkeypatch.setattr(pipeline, "build_online_llm", lambda *a, **kw: FrameProcessor())
    monkeypatch.setattr(pipeline, "build_vad_processor", lambda *a, **kw: FrameProcessor())


@pytest.fixture
def stub_offline_services(monkeypatch):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade")
    for name in ("build_stt", "build_tts"):
        monkeypatch.setattr(services, name, lambda *a, **kw: FrameProcessor())
    for name in ("build_llm", "build_vad_processor"):
        monkeypatch.setattr(pipeline, name, lambda *a, **kw: FrameProcessor())


@pytest.fixture
def no_private_db(monkeypatch):
    """Opening the private records db is a test failure."""

    def boom(*_args, **_kwargs):
        raise AssertionError("online pipeline reached for the private db")

    monkeypatch.setattr(config, "rag_db_path", boom)
    monkeypatch.setattr(config, "rag_data_dir", boom)


@pytest.fixture
def no_ollama(monkeypatch):
    """Any Ollama lookup from an online build is a test failure."""

    def boom(*_args, **_kwargs):
        raise AssertionError("online pipeline touched Ollama")

    monkeypatch.setattr(config, "ollama_base_url", boom)
    monkeypatch.setattr(config, "ollama_api_url", boom)


def fake_transport():
    return SimpleNamespace(input=FrameProcessor, output=FrameProcessor)


def tool_names(context) -> list[str]:
    if not is_given(context.tools):
        return []
    return [tool.name for tool in context.tools.standard_tools]


def test_online_pipeline_uses_public_db_only(
    public_db, fake_keys, stub_online_services, no_private_db
):
    built = pipeline.build_pipeline(fake_transport(), modes="online")
    assert built.pipeline is not None
    assert tool_names(built.context) == ["semantic_search"]


def test_online_tools_semantic_only(public_db):
    schemas = tools.build_public_tools(public_db, ingest.FakeEmbedder())
    assert [schema.name for schema in schemas] == ["semantic_search"]


def test_online_no_ollama_touch(
    public_db, fake_keys, stub_online_services, no_private_db, no_ollama
):
    built = pipeline.build_pipeline(fake_transport(), modes="online")
    assert built.llm is not None
    assert built.tts is not None


def test_online_prompt_is_the_online_story(
    public_db, fake_keys, stub_online_services, no_private_db
):
    built = pipeline.build_pipeline(fake_transport(), modes="online")
    prompt = built.context.messages[0]["content"]
    assert prompt.startswith(tunables.LOCAT_ONLINE_CASCADE_PROMPT)
    assert "You run entirely offline" not in prompt
    assert "{language_line}" not in prompt


def test_online_cascade_prompt_offers_spanish_practice():
    assert "Spanish" in tunables.online_system_prompt()


def test_mode_default_and_garbage_offline(tmp_path, monkeypatch, stub_offline_services):
    monkeypatch.setenv("LOCAT_RAG_DB", str(tmp_path / "missing" / "locat.db"))
    for built in (
        pipeline.build_pipeline(fake_transport()),
        pipeline.build_pipeline(fake_transport(), modes="garbage"),
    ):
        prompt = built.context.messages[0]["content"]
        assert prompt.startswith(tunables.LOCAT_OFFLINE_CASCADE_PROMPT)


def test_online_missing_public_db_degrades(
    tmp_path, monkeypatch, fake_keys, stub_online_services, no_private_db
):
    monkeypatch.setenv("LOCAT_PUBLIC_RAG_DB", str(tmp_path / "missing" / "locat-public.db"))
    monkeypatch.setenv("LOCAT_PUBLIC_RAG_DATA_DIR", str(tmp_path / "public"))
    built = pipeline.build_pipeline(fake_transport(), modes="online")
    assert tool_names(built.context) == []
    assert pipeline.public_rag_summary() == (
        "no public index — run ./locat.sh index-rag with OPENAI_API_KEY set"
    )


def test_public_rag_summary_reports_the_public_index(public_db):
    summary = pipeline.public_rag_summary()
    assert summary.startswith("index: ")
    assert str(public_db.parent) in summary or "public" in summary


# --- online llmwithbackend split (LOCAT_ONLINE_ARCHITECTURE, default) ----------


@pytest.fixture
def stub_online_av(monkeypatch):
    """Stub only STT/TTS/VAD: the split wraps real (offline-constructed) LLM services."""
    for name in ("build_online_stt", "build_online_tts"):
        monkeypatch.setattr(services, name, lambda *a, **kw: FrameProcessor())
    monkeypatch.setattr(pipeline, "build_vad_processor", lambda *a, **kw: FrameProcessor())


def test_online_split_is_the_default_and_builds_llm_with_backend(
    public_db, fake_keys, stub_online_av, no_private_db, no_ollama, monkeypatch
):
    from pipecat.pipeline.llm_with_backend import LLMWithBackend

    monkeypatch.delenv("LOCAT_ONLINE_ARCHITECTURE", raising=False)
    built = pipeline.build_pipeline(fake_transport(), modes="online")

    # The front holds no context tools — delegate is pipecat's stock builtin.
    assert tool_names(built.context) == []
    assert built.context.messages[0]["content"].startswith(
        "You are a private financial thinking partner. This session is running in online mode"
    )

    lwb = next(p for p in built.pipeline._processors if isinstance(p, LLMWithBackend))
    worker = lwb.backend
    assert tool_names(worker.context) == ["semantic_search"]
    backend_system = worker.context.messages[0]["content"]
    assert backend_system.startswith(tunables.LOCAT_ONLINE_ANALYST_PROMPT)
    assert "Today's date is" in backend_system


def test_online_split_backend_uses_its_own_model(
    public_db, fake_keys, stub_online_av, no_private_db, no_ollama, monkeypatch
):
    from pipecat.pipeline.llm_with_backend import LLMWithBackend

    monkeypatch.setenv("LOCAT_ONLINE_RAG_LLM_MODEL", "gpt-4.1")
    built = pipeline.build_pipeline(fake_transport(), modes="online")
    lwb = next(p for p in built.pipeline._processors if isinstance(p, LLMWithBackend))
    assert lwb.backend.llm._settings.model == "gpt-4.1"


def test_online_split_without_public_index_falls_back_to_cascade(
    tmp_path, fake_keys, stub_online_services, no_private_db, monkeypatch
):
    from pipecat.pipeline.llm_with_backend import LLMWithBackend

    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOCAT_PUBLIC_RAG_DB", str(tmp_path / "missing" / "locat-public.db"))
    monkeypatch.setenv("LOCAT_PUBLIC_RAG_DATA_DIR", str(tmp_path / "public"))
    built = pipeline.build_pipeline(fake_transport(), modes="online")
    assert not any(isinstance(p, LLMWithBackend) for p in built.pipeline._processors)


def test_online_architecture_knob(monkeypatch):
    monkeypatch.delenv("LOCAT_ONLINE_ARCHITECTURE", raising=False)
    assert config.online_architecture() == "llmwithbackend"
    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "cascade")
    assert config.online_architecture() == "cascade"
    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "sandwich")
    with pytest.raises(SystemExit):
        config.online_architecture()


def test_online_rag_llm_model_default_and_override(monkeypatch):
    monkeypatch.delenv("LOCAT_ONLINE_RAG_LLM_MODEL", raising=False)
    assert config.online_rag_llm_model() == "gpt-4o-mini"
    monkeypatch.setenv("LOCAT_ONLINE_RAG_LLM_MODEL", "gpt-4.1")
    assert config.online_rag_llm_model() == "gpt-4.1"


# --- offline architectures (LOCAT_ARCHITECTURE: three-way branch) -------------


@pytest.fixture
def records_db(tmp_path, monkeypatch):
    """A private records index built with FakeEmbedder, pointed at by config."""
    data_dir = tmp_path / "records"
    data_dir.mkdir()
    shutil.copy(FIXTURES / "transactions-small.csv", data_dir / "transactions.csv")
    shutil.copy(FIXTURES / "note.txt", data_dir / "note.txt")
    db_path = tmp_path / "locat.db"
    ingest.ingest(data_dir, db_path, ingest.FakeEmbedder())
    monkeypatch.setenv("LOCAT_RAG_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LOCAT_RAG_DB", str(db_path))
    return db_path


@pytest.fixture
def stub_offline_av(monkeypatch):
    """Stub only STT/TTS/VAD: the LLM builders stay real for model assertions."""
    for name in ("build_stt", "build_tts"):
        monkeypatch.setattr(services, name, lambda *a, **kw: FrameProcessor())
    monkeypatch.setattr(pipeline, "build_vad_processor", lambda *a, **kw: FrameProcessor())


def test_simple_chat_has_no_tools_and_its_own_prompt(monkeypatch, stub_offline_services):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    built = pipeline.build_pipeline(fake_transport())
    assert tool_names(built.context) == []
    prompt = built.context.messages[0]["content"]
    assert prompt.startswith(tunables.LOCAT_SIMPLE_CHAT_PROMPT)
    assert "Today's date is" in prompt


def test_simple_chat_never_opens_the_records_db(
    monkeypatch, stub_offline_services, no_private_db
):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    built = pipeline.build_pipeline(fake_transport())  # no_private_db raises on any touch
    assert built.pipeline is not None


def test_cascade_holds_the_four_tools_on_the_rag_model(
    records_db, stub_offline_av, monkeypatch
):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade")
    monkeypatch.setenv("LOCAT_RAG_LLM_MODEL", "qwen3.8:27b")
    built = pipeline.build_pipeline(fake_transport())
    assert set(tool_names(built.context)) == {
        "lookup",
        "filter_rows",
        "aggregate",
        "semantic_search",
    }
    assert built.llm._settings.model == "qwen3.8:27b"
    assert built.context.messages[0]["content"].startswith(tunables.LOCAT_OFFLINE_CASCADE_PROMPT)


def test_cascade_without_index_builds_toolless(monkeypatch, stub_offline_services, tmp_path):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade")
    monkeypatch.setenv("LOCAT_RAG_DB", str(tmp_path / "missing" / "locat.db"))
    built = pipeline.build_pipeline(fake_transport())
    assert tool_names(built.context) == []
    assert built.context.messages[0]["content"].startswith(tunables.LOCAT_OFFLINE_CASCADE_PROMPT)


# --- mixed per-service sessions (SessionModes) --------------------------------


class Marked(FrameProcessor):
    """Stub service that says which builder made it."""

    def __init__(self, label: str):
        super().__init__()
        self.label = label


@pytest.fixture
def labelled_av(monkeypatch):
    """All four STT/TTS builders stubbed, each marking its own name."""
    for name in ("build_stt", "build_tts", "build_online_stt", "build_online_tts"):
        monkeypatch.setattr(services, name, lambda n=name: Marked(n))
    monkeypatch.setattr(pipeline, "build_vad_processor", lambda *a, **kw: FrameProcessor())


@pytest.fixture
def warnings_logged():
    """Collect WARNING-level loguru messages emitted inside the test."""
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    yield records
    logger.remove(sink_id)


def built_av(built) -> list[str]:
    return [p.label for p in built.pipeline._processors if isinstance(p, Marked)]


def service_modes(stt, llm, tts, session="offline") -> SessionModes:
    return SessionModes(session=session, stt=stt, llm=llm, tts=tts)


def test_online_brain_with_local_audio_never_opens_the_private_db(
    public_db, fake_keys, labelled_av, no_private_db, no_ollama, monkeypatch
):
    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "cascade")
    monkeypatch.setattr(pipeline, "build_online_llm", lambda *a, **kw: FrameProcessor())
    built = pipeline.build_pipeline(
        fake_transport(), service_modes(stt="offline", llm="online", tts="offline")
    )
    assert built_av(built) == ["build_stt", "build_tts"]
    assert tool_names(built.context) == ["semantic_search"]
    assert built.context.messages[0]["content"].startswith(tunables.LOCAT_ONLINE_CASCADE_PROMPT)


def test_local_brain_with_online_audio_never_builds_the_openai_embedder(
    records_db, fake_keys, labelled_av, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("offline brain constructed the OpenAI embedder")

    monkeypatch.setattr(ingest, "OpenAIEmbedder", boom)
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade")
    built = pipeline.build_pipeline(
        fake_transport(), service_modes(stt="offline", llm="offline", tts="online")
    )
    assert built_av(built) == ["build_stt", "build_online_tts"]
    assert built.context.messages[0]["content"].startswith(tunables.LOCAT_OFFLINE_CASCADE_PROMPT)
    assert set(tool_names(built.context)) == {
        "lookup",
        "filter_rows",
        "aggregate",
        "semantic_search",
    }


def test_local_brain_warns_which_audio_crosses_the_network(
    records_db, fake_keys, labelled_av, warnings_logged, monkeypatch
):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade")
    pipeline.build_pipeline(
        fake_transport(), service_modes(stt="online", llm="offline", tts="online")
    )
    assert len(warnings_logged) == 1
    assert "Deepgram" in warnings_logged[0] and "Cartesia" in warnings_logged[0]


def test_unmixed_sessions_warn_about_nothing(
    public_db, fake_keys, labelled_av, warnings_logged, monkeypatch
):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "cascade")
    monkeypatch.setattr(pipeline, "build_online_llm", lambda *a, **kw: FrameProcessor())
    pipeline.build_pipeline(fake_transport(), service_modes("offline", "offline", "offline"))
    pipeline.build_pipeline(fake_transport(), service_modes("online", "online", "online"))
    assert warnings_logged == []


def test_bare_mode_string_sets_all_three_services(
    public_db, fake_keys, labelled_av, no_private_db, monkeypatch
):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    monkeypatch.setenv("LOCAT_ONLINE_ARCHITECTURE", "cascade")
    monkeypatch.setattr(pipeline, "build_online_llm", lambda *a, **kw: FrameProcessor())
    online = pipeline.build_pipeline(fake_transport(), "online")
    assert built_av(online) == ["build_online_stt", "build_online_tts"]
    offline = pipeline.build_pipeline(fake_transport(), "garbage")
    assert built_av(offline) == ["build_stt", "build_tts"]
