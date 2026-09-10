import os
import re
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCAT = str(REPO / "locat.sh")

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


def run_locat(
    command: str,
    state_dir: Path,
    ollama_host: str,
    extra_args: list[str] | None = None,
    **extra_env: str,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LOCAT_ENV_FILE": "/dev/null",
        "LOCAT_STATE_DIR": str(state_dir),
        "OLLAMA_HOST": ollama_host,
        **extra_env,
    }
    return subprocess.run(
        [LOCAT, command, *(extra_args or [])],
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
        result = run_locat("stop", tmp_path, f"127.0.0.1:{closed_port()}")
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
    result = run_locat("stop", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    assert "stale" in result.stdout
    assert not (tmp_path / "bot.pid").exists()


def test_status_exits_zero_with_nothing_running(tmp_path):
    result = run_locat("status", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    assert "ollama   not running" in result.stdout
    assert "bot      not running" in result.stdout


def test_status_reports_foreign_ollama_untouched(tmp_path):
    server, port = start_fake_ollama()
    try:
        result = run_locat("status", tmp_path, f"127.0.0.1:{port}")
        assert result.returncode == 0
        assert "NOT started by locat" in result.stdout
        assert server.poll() is None
    finally:
        server.kill()


def test_index_rag_empty_data_dir_prints_hint_and_exits_zero(tmp_path):
    data_dir = tmp_path / "docs"
    result = run_locat(
        "index-rag",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOCAT_RAG_DATA_DIR=str(data_dir),
        LOCAT_RAG_INDEX_DIR=str(tmp_path / "idx"),
    )
    assert result.returncode == 0
    assert str(data_dir) in result.stdout
    assert data_dir.is_dir()


def test_status_rag_line_without_index(tmp_path):
    result = run_locat(
        "status",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOCAT_RAG_INDEX_DIR=str(tmp_path / "idx"),
    )
    assert result.returncode == 0
    assert "rag      no index (run ./locat.sh index-rag)" in result.stdout


def test_status_rag_line_with_index(tmp_path):
    import rag

    index_dir = tmp_path / "idx"
    rag.index(REPO / "tests" / "fixtures", index_dir, rag.FakeEmbedder())
    result = run_locat(
        "status",
        tmp_path,
        f"127.0.0.1:{closed_port()}",
        LOCAT_RAG_INDEX_DIR=str(index_dir),
    )
    assert result.returncode == 0
    assert re.search(r"rag      index: \d+ chunks from 3 files", result.stdout)


def test_stop_leaves_foreign_ollama_alone(tmp_path):
    server, port = start_fake_ollama()
    try:
        result = run_locat("stop", tmp_path, f"127.0.0.1:{port}")
        assert result.returncode == 0
        assert "NOT started by locat — leaving it alone" in result.stdout
        assert server.poll() is None
    finally:
        server.kill()


def test_consolidate_via_locat(tmp_path):
    model_dir = tmp_path / "models"
    (model_dir / "huggingface" / "hub").mkdir(parents=True)
    result = run_locat(
        "consolidate",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        LOCAT_MODEL_DIR=str(model_dir),
    )
    assert result.returncode == 0
    assert f"browse everything: ls -al {model_dir}" in result.stdout


def test_status_labels_borrowed_ollama_store(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    external = tmp_path / "home-ollama"
    external.mkdir()
    (model_dir / "ollama").symlink_to(external)
    result = run_locat(
        "status",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        LOCAT_MODEL_DIR=str(model_dir),
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
    result = run_locat("get-debug-log", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 0
    bundle = Path(result.stdout.strip().splitlines()[-1].split()[-1])
    assert bundle.is_file()
    content = bundle.read_text()
    assert "session 3 content" in content
    assert "session 1 content" not in content
    bundle.unlink()


def test_get_debug_log_n_sessions_back(tmp_path):
    make_session_logs(tmp_path)
    result = run_locat("get-debug-log", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["2"])
    assert result.returncode == 0
    bundle = Path(result.stdout.strip().splitlines()[-1].split()[-1])
    assert "session 2 content" in bundle.read_text()
    bundle.unlink()


def test_get_debug_log_without_sessions_explains(tmp_path):
    result = run_locat("get-debug-log", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 1
    assert "no session logs" in result.stdout + result.stderr


def test_per_command_help_configure(tmp_path):
    result = run_locat("configure", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "-i" in result.stdout and "-v" in result.stdout


def test_per_command_help_start(tmp_path):
    result = run_locat("start", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["help"])
    assert result.returncode == 0
    assert "moq" in result.stdout and "headphones" in result.stdout


def test_models_command_removed(tmp_path):
    result = run_locat("models", tmp_path, f"127.0.0.1:{closed_port()}")
    assert result.returncode == 1
    assert "unknown command" in result.stderr


def test_configure_all_flag_removed(tmp_path):
    result = run_locat("configure", tmp_path, f"127.0.0.1:{closed_port()}", extra_args=["-a"])
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
        "LOCAT_MODEL_DIR": str(model_dir),
        "HF_HOME": str(model_dir / "huggingface"),
        "OLLAMA_MODELS": str(model_dir / "ollama"),
        "LOCAT_KOKORO_MODEL_PATH": str(model_dir / "kokoro" / "kokoro-v1.0.onnx"),
        "LOCAT_KOKORO_VOICES_PATH": str(model_dir / "kokoro" / "voices-v1.0.bin"),
        "LOCAT_PIPER_DOWNLOAD_DIR": str(model_dir / "piper"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
    }


def test_status_shows_machine_and_downloaded_sections(tmp_path):
    result = run_locat(
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
    catalog = Path(env["LOCAT_MODEL_DIR"]) / ".llm-catalog"
    catalog.write_text(
        "# generated 9999999999 2099-01-01 models=2\n"
        "status-test-model:7b|5||\n"
        "way-too-big-model:999b|999||\n"
    )
    result = run_locat(
        "status",
        tmp_path / "state",
        f"127.0.0.1:{closed_port()}",
        extra_args=["-v"],
        LOCAT_CATALOG_MAX_AGE_DAYS="0",
        **env,
    )
    assert result.returncode == 0
    assert "LLM catalog" in result.stdout
    assert "status-test-model:7b" in result.stdout
    assert "way-too-big-model:999b" in result.stdout  # untrimmed, like old configure -a
    assert "STT catalog" in result.stdout
    assert "TTS catalog" in result.stdout
