"""Tests for the analyst warmup: a load-only generate fired at pipeline build so
the first delegation doesn't pay the analyst's cold load (11.5 s TTFB,
qwen3:8b). Backgrounded — startup never waits for it."""

import asyncio

import httpx

import config
import services


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records posts, optionally raises."""

    def __init__(self, raises: Exception | None = None):
        self.posts: list[tuple[str, dict]] = []
        self.closed = False
        self._raises = raises

    async def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        if self._raises is not None:
            raise self._raises
        return httpx.Response(200, json={"response": ""})

    async def aclose(self):
        self.closed = True


def test_warmup_posts_load_only_generate(monkeypatch):
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "qwen3:8b")
    client = FakeAsyncClient()
    asyncio.run(services.warm_analyst_model(client=client))
    assert len(client.posts) == 1
    url, body = client.posts[0]
    assert url == f"{config.ollama_api_url()}/api/generate"
    assert body["model"] == "qwen3:8b"
    # Empty prompt: Ollama loads the weights and generates nothing.
    assert body["prompt"] == ""
    assert body["stream"] is False
    # Caller-supplied client stays open.
    assert client.closed is False


def test_warmup_with_system_messages_prefills_via_v1_chat(monkeypatch):
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "qwen3.8:27b")
    client = FakeAsyncClient()
    messages = [
        {"role": "system", "content": "pairing instruction"},
        {"role": "system", "content": "analyst prompt + grounding"},
    ]
    asyncio.run(services.warm_analyst_model(system_messages=messages, client=client))
    assert len(client.posts) == 1
    url, body = client.posts[0]
    # /v1 chat renders the same template as the live requests, so the KV
    # prefix cache is warm for the first delegation.
    assert url == f"{config.ollama_base_url()}/chat/completions"
    assert body["model"] == "qwen3.8:27b"
    assert body["messages"] == messages
    assert body["max_tokens"] == 1


def test_warmup_messages_match_the_live_backend_composition(tmp_path, monkeypatch):
    # The prefill is only worth its tokens if it is byte-identical to what
    # build_records_backend installs plus the worker's pairing instruction.
    import shutil
    from pathlib import Path

    import ingest
    import pipeline
    import tunables

    fixtures = Path(__file__).parent / "fixtures"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(fixtures / "transactions-small.csv", data_dir / "transactions.csv")
    db_path = tmp_path / "lofi.db"
    ingest.ingest(data_dir, db_path, ingest.FakeEmbedder())

    backend = pipeline.build_records_backend(db_path)
    assert backend.context.messages[0]["content"] == pipeline.analyst_system_prompt(db_path)
    assert backend.llm._composed_system_instruction == tunables.LOFI_BACKEND_PAIRING_INSTRUCTION


def test_warmup_swallows_http_errors():
    client = FakeAsyncClient(raises=httpx.ConnectError("ollama down"))
    asyncio.run(services.warm_analyst_model(client=client))
    assert len(client.posts) == 1


def test_warm_analyst_runs_in_background_and_does_not_block(monkeypatch):
    ran = asyncio.Event()

    async def fake_warm(system_messages=None, client=None):
        ran.set()

    monkeypatch.setattr(services, "warm_analyst_model", fake_warm)

    async def scenario():
        services.warm_analyst()
        assert not ran.is_set()  # scheduled, not awaited
        await asyncio.wait_for(ran.wait(), timeout=1)

    asyncio.run(scenario())


def test_warm_analyst_without_event_loop_is_a_noop(monkeypatch):
    def boom(client=None):
        raise AssertionError("must not run without a loop")

    monkeypatch.setattr(services, "warm_analyst_model", boom)
    services.warm_analyst()
