import os
import re
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOFI = str(REPO / "lofi.sh")

FAKE_TAGS_SERVER = """
import http.server

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"models": []}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
"""


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_lofi(
    command: str,
    state_dir: Path,
    ollama_host: str,
    extra_args: list[str] | None = None,
    **extra_env: str,
) -> subprocess.CompletedProcess:
    # Drop inherited LOFI_* vars: importing config in the pytest process loads
    # the repo .env into os.environ, which would override LOFI_ENV_FILE=/dev/null.
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("LOFI_")},
        "LOFI_ENV_FILE": "/dev/null",
        "LOFI_STATE_DIR": str(state_dir),
        "OLLAMA_HOST": ollama_host,
        **extra_env,
    }
    return subprocess.run(
        [LOFI, command, *(extra_args or [])],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def start_fake_ollama() -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen(
        [sys.executable, "-c", FAKE_TAGS_SERVER], stdout=subprocess.PIPE, text=True
    )
    port = int(proc.stdout.readline())
    return proc, port


def test_stop_only_kills_recorded_pids(tmp_path):
    victim = subprocess.Popen(["sleep", "300"])
    bystander = subprocess.Popen(["sleep", "300"])
    try:
        (tmp_path / "bot.pid").write_text(str(victim.pid))
        result = run_lofi("stop", tmp_path, f"127.0.0.1:{closed_port()}")
        assert result.returncode == 0
        victim.wait(timeout=10)
        assert victim.poll() is not None
        assert bystander.poll() is None
        assert not (tmp_path / "bot.pid").exists()
    finally:
        for proc in (victim, bystander):
            if proc.poll() is None:
                proc.kill()


def test_stop_cleans_stale_pidfile_without_killing(tmp_path):
    proc = subprocess.Popen(["sleep", "300"])
    proc.kill()
    proc.wait()
    (tmp_path / "bot.pid").write_text(str(proc.pid))
    result = run_lofi("stop", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    assert "stale" in result.stdout
    assert not (tmp_path / "bot.pid").exists()


def test_status_exits_zero_with_nothing_running(tmp_path):
    result = run_lofi("status", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    assert "ollama   not running" in result.stdout


def test_status_lists_the_conversation_model(tmp_path):
    result = run_lofi("status", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    assert re.search(r"^\s+LLM\s+qwen2\.5:7b", result.stdout, re.M), result.stdout
    assert re.search(r"^\s+ANALYST\s+qwen3:8b", result.stdout, re.M), result.stdout
    assert "bot      not running" in result.stdout


def test_status_reports_foreign_ollama_untouched(tmp_path):
    server, port = start_fake_ollama()
    try:
        result = run_lofi("status", tmp_path, f"127.0.0.1:{port}")
        assert result.returncode == 0
        assert "NOT started by lofi" in result.stdout
        assert server.poll() is None
    finally:
        server.kill()


def test_index_rag_empty_data_dir_prints_hint_and_exits_zero(tmp_path):
    data_dir = tmp_path / "docs"
    result = run_lofi(
        "index-rag",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOFI_RAG_DATA_DIR=str(data_dir),
        LOFI_RAG_INDEX_DIR=str(tmp_path / "idx"),
    )
    assert result.returncode == 0
    assert str(data_dir) in result.stdout
    assert data_dir.is_dir()


def test_status_rag_line_without_index(tmp_path):
    result = run_lofi(
        "status",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOFI_RAG_INDEX_DIR=str(tmp_path / "idx"),
    )
    assert result.returncode == 0
    assert "rag      no index (run ./lofi.sh index-rag)" in result.stdout


def test_status_rag_line_with_index(tmp_path):
    import ingest

    index_dir = tmp_path / "idx"
    ingest.ingest(REPO / "tests" / "fixtures", index_dir / "lofi.db", ingest.FakeEmbedder())
    result = run_lofi(
        "status",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOFI_RAG_INDEX_DIR=str(index_dir),
    )
    assert result.returncode == 0
    assert re.search(r"rag      index: \d+ chunks from 4 files", result.stdout)


def test_stop_leaves_foreign_ollama_alone(tmp_path):
    server, port = start_fake_ollama()
    try:
        result = run_lofi("stop", tmp_path, f"127.0.0.1:{port}")
        assert result.returncode == 0
        assert "NOT started by lofi — leaving it alone" in result.stdout
        assert server.poll() is None
    finally:
        server.kill()


def test_consolidate_via_lofi(tmp_path):
    model_dir = tmp_path / "models"
    (model_dir / "huggingface" / "hub").mkdir(parents=True)
    result = run_lofi(
        "consolidate",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        LOFI_MODEL_DIR=str(model_dir),
    )
    assert result.returncode == 0
    assert f"browse everything: ls -al {model_dir}" in result.stdout


def test_status_labels_borrowed_ollama_store(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    external = tmp_path / "home-ollama"
    external.mkdir()
    (model_dir / "ollama").symlink_to(external)
    result = run_lofi(
        "status",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        LOFI_MODEL_DIR=str(model_dir),
    )
    assert result.returncode == 0
    assert f"store: borrowed -> {external}" in result.stdout


def make_session_logs(state_dir: Path, count: int = 3) -> list[Path]:
    logs = state_dir / "logs"
    logs.mkdir(parents=True)
    paths = []
    for i in range(count):
        p = logs / f"bot-2026090{i + 1}-120000.log"
        p.write_text(f"session {i + 1} content\n")
        paths.append(p)
    return paths


def test_get_debug_log_bundles_latest_session(tmp_path):
    make_session_logs(tmp_path)
    result = run_lofi("get-debug-log", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    bundle = Path(result.stdout.strip().splitlines()[-1].split()[-1])
    assert bundle.is_file()
    content = bundle.read_text()
    assert "session 3 content" in content
    assert "session 1 content" not in content
    bundle.unlink()


def test_get_debug_log_n_sessions_back(tmp_path):
    make_session_logs(tmp_path)
    result = run_lofi("get-debug-log", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["2"])
    assert result.returncode == 0
    bundle = Path(result.stdout.strip().splitlines()[-1].split()[-1])
    assert "session 2 content" in bundle.read_text()
    bundle.unlink()


def test_get_debug_log_without_sessions_explains(tmp_path):
    result = run_lofi("get-debug-log", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 1
    assert "no session logs" in result.stdout + result.stderr


def test_per_command_help_configure(tmp_path):
    result = run_lofi("configure", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "-i" in result.stdout and "-v" in result.stdout


def test_per_command_help_start(tmp_path):
    result = run_lofi("start", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "browser" in result.stdout and "headphones" in result.stdout


def test_per_command_help_start_documents_online_only(tmp_path):
    result = run_lofi("start", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "--online-only" in result.stdout
    assert "LOFI_ONLINE_ONLY=1" in result.stdout


def test_per_command_help_start_documents_offline_only_and_architecture(tmp_path):
    result = run_lofi("start", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "--offline-only" in result.stdout
    assert "LOFI_OFFLINE_ONLY=1" in result.stdout
    assert "--cascade" in result.stdout
    assert "--llmwithbackend" in result.stdout
    assert "LOFI_ARCHITECTURE" in result.stdout


def test_start_conflicting_mode_flags_refused(tmp_path):
    result = run_lofi(
        "start",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        extra_args=["--online-only", "--offline-only"],
    )
    assert result.returncode == 1
    assert "--online-only" in result.stderr and "--offline-only" in result.stderr


def test_start_conflicting_architecture_flags_refused(tmp_path):
    result = run_lofi(
        "start",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        extra_args=["--cascade", "--llmwithbackend"],
    )
    assert result.returncode == 1
    assert "--cascade" in result.stderr and "--llmwithbackend" in result.stderr


def test_start_headphones_refused_when_config_says_online_only(tmp_path):
    # Decoy bot.pid so a wrong code path hits the already-running guard
    # instead of launching the real stack.
    decoy = subprocess.Popen(["sleep", "300"])
    try:
        (tmp_path / "bot.pid").write_text(str(decoy.pid))
        result = run_lofi(
            "start",
            tmp_path,
            f"127.0.0.1:{closed_port()}",
            extra_args=["-t", "headphones"],
            LOFI_ONLINE_ONLY="1",
        )
        assert result.returncode == 1
        assert "browser bot" in result.stderr
    finally:
        decoy.kill()


def test_start_offline_only_flag_overrides_online_only_config(tmp_path):
    """--offline-only beats LOFI_ONLINE_ONLY=1 from a config file, so the
    headphones refusal does not fire; the already-running guard proves we
    got past it without launching anything."""
    decoy = subprocess.Popen(["sleep", "300"])
    try:
        (tmp_path / "bot.pid").write_text(str(decoy.pid))
        result = run_lofi(
            "start",
            tmp_path,
            f"127.0.0.1:{closed_port()}",
            extra_args=["-t", "headphones", "--offline-only"],
            LOFI_ONLINE_ONLY="1",
        )
        assert result.returncode == 1
        assert "already running" in result.stderr
        assert "browser bot" not in result.stderr
    finally:
        decoy.kill()


def test_models_command_removed(tmp_path):
    result = run_lofi("models", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 1
    assert "unknown command" in result.stderr


def test_configure_all_flag_removed(tmp_path):
    result = run_lofi("configure", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["-a"])
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def isolated_store_env(tmp_path: Path) -> dict:
    model_dir = tmp_path / "models"
    (model_dir / "huggingface" / "hub").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    (tmp_path / "xdg").mkdir()
    # config.py setdefaults the kokoro/piper paths into os.environ on first
    # import, so an earlier test importing it leaks the real store into this
    # env via **os.environ — pin every store-derived var explicitly.
    return {
        "LOFI_MODEL_DIR": str(model_dir),
        "HF_HOME": str(model_dir / "huggingface"),
        "OLLAMA_MODELS": str(model_dir / "ollama"),
        "LOFI_KOKORO_MODEL_PATH": str(model_dir / "kokoro" / "kokoro-v1.0.onnx"),
        "LOFI_KOKORO_VOICES_PATH": str(model_dir / "kokoro" / "voices-v1.0.bin"),
        "LOFI_PIPER_DOWNLOAD_DIR": str(model_dir / "piper"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
    }


def test_status_shows_machine_and_downloaded_sections(tmp_path):
    result = run_lofi(
        "status",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        **isolated_store_env(tmp_path),
    )
    assert result.returncode == 0
    assert "machine" in result.stdout
    assert "chip:" in result.stdout
    assert "downloaded" in result.stdout
    assert "no models downloaded yet" in result.stdout


def test_status_verbose_shows_full_catalogs(tmp_path):
    env = isolated_store_env(tmp_path)
    catalog = Path(env["LOFI_MODEL_DIR"]) / ".llm-catalog"
    catalog.write_text(
        "# generated 9999999999 2099-01-01 models=2\n"
        "status-test-model:7b|5||\n"
        "way-too-big-model:999b|999||\n"
    )
    result = run_lofi(
        "status",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        extra_args=["-v"],
        LOFI_CATALOG_MAX_AGE_DAYS="0",
        **env,
    )
    assert result.returncode == 0
    assert "LLM catalog" in result.stdout
    assert "status-test-model:7b" in result.stdout
    assert "way-too-big-model:999b" in result.stdout  # untrimmed, like old configure -a
    assert "STT catalog" in result.stdout
    assert "TTS catalog" in result.stdout


def test_command_line_env_beats_env_file(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("LOFI_LLM_MODEL=from-file:1b\n")
    result = run_lofi(
        "status",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOFI_ENV_FILE=str(env_file),
        LOFI_LLM_MODEL="from-cli:1b",
    )
    assert result.returncode == 0
    assert re.search(r"^\s+LLM\s+from-cli:1b", result.stdout, re.M), result.stdout


def test_env_file_inline_comments_stripped(tmp_path):
    """Unquoted inline ` # comment` is not part of the value — matches
    python-dotenv, so shell and bot resolve the same model tag."""
    rc = tmp_path / "lofirc"
    rc.write_text("LOFI_RAG_LLM_MODEL=qwen3.8:27b   # records analyst backend\n")
    result = run_lofi("status", tmp_path, f"127.0.0.1:{closed_port()}", LOFI_ENV_FILE=str(rc))
    assert result.returncode == 0
    assert re.search(r"^\s+ANALYST\s+qwen3\.8:27b \(", result.stdout, re.M), result.stdout
    assert "# records analyst backend" not in result.stdout


def test_start_reports_missing_models_and_launches_nothing(tmp_path):
    port = closed_port()
    # The Python side reads LOFI_OLLAMA_BASE_URL, not OLLAMA_HOST.
    env = {
        **isolated_store_env(tmp_path),
        "LOFI_OLLAMA_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "LOFI_WEB_PORT": str(closed_port()),
    }
    result = run_lofi(
        "start",
        tmp_path / "state",
        f"127.0.0.1:{port}",
        extra_args=["--offline-only", "--cascade"],
        **env,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "not downloaded:" in out
    assert "ollama:qwen2.5:7b" in out
    assert "prefetch_models.py" in out
    assert not (tmp_path / "state" / "bot.pid").exists()


def test_start_online_only_missing_keys_fails_before_launch(tmp_path):
    env = {
        **isolated_store_env(tmp_path),
        "OPENAI_API_KEY": "",
        "DEEPGRAM_API_KEY": "",
        "CARTESIA_API_KEY": "",
        "LOFI_WEB_PORT": str(closed_port()),
    }
    result = run_lofi(
        "start",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        extra_args=["--online-only"],
        **env,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "OPENAI_API_KEY" in out
    assert "not downloaded" not in out
    assert not (tmp_path / "state" / "bot.pid").exists()


def test_usage_and_start_help_document_setup(tmp_path):
    result = run_lofi("help", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    assert "./lofi.sh setup" in result.stdout
    result = run_lofi("start", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert 'setup needed: run "./lofi.sh setup"' in result.stdout


def test_per_command_help_start_documents_simple_chat(tmp_path):
    result = run_lofi("start", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "--cascade-simple-chat" in result.stdout


def test_start_simple_chat_conflicts_with_other_architecture_flags(tmp_path):
    for other in ("--cascade", "--llmwithbackend"):
        result = run_lofi(
            "start",
            tmp_path,
            f"127.0.0.1:{closed_port()}",
            extra_args=["--cascade-simple-chat", other],
        )
        assert result.returncode == 1
        assert "--cascade-simple-chat" in result.stderr and other in result.stderr


def test_start_simple_chat_overrides_online_only(tmp_path):
    # Decoy bot.pid stops start at the already-running guard, after the
    # override warning prints but before anything launches.
    decoy = subprocess.Popen(["sleep", "300"])
    try:
        (tmp_path / "bot.pid").write_text(str(decoy.pid))
        result = run_lofi(
            "start",
            tmp_path,
            f"127.0.0.1:{closed_port()}",
            extra_args=["--cascade-simple-chat", "--online-only"],
        )
        assert result.returncode == 1
        assert "already running" in result.stderr
        assert "start: simple-chat is offline — overriding online-only." in result.stdout
    finally:
        decoy.kill()
