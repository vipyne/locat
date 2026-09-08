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
    command: str, state_dir: Path, ollama_host: str, **extra_env: str
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LOCAT_ENV_FILE": "/dev/null",
        "LOCAT_STATE_DIR": str(state_dir),
        "OLLAMA_HOST": ollama_host,
        **extra_env,
    }
    return subprocess.run(
        [LOCAT, command], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
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
