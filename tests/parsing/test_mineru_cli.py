"""The MinerU wrapper: `mineru` with every request to its local API on a fresh connection.

A kept-alive connection can be closed by MinerU's stalled local API just as a status poll reaches it, which
fails the task with an empty reason. The wrapper makes `httpx.AsyncClient` keep no idle connection. Its
logic is checked against a stand-in `httpx`; its effect is checked with the real `httpx` in the MinerU env
when that env exists, by counting the connections a local server sees.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts import mineru_cli
from scripts.mineru_cli import poll_on_fresh_connections
from tests.conftest import REPO_ROOT

MINERU_ENV = "docparse-mineru"


def _stand_in_httpx() -> ModuleType:
    """A stand-in `httpx` whose client and limits record what they were given."""
    module = ModuleType("httpx")

    class Limits:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    module.Limits = Limits  # type: ignore[attr-defined]
    module.AsyncClient = AsyncClient  # type: ignore[attr-defined]
    return module


def test_a_client_keeps_no_idle_connection_and_keeps_its_other_settings() -> None:
    httpx = _stand_in_httpx()
    original = httpx.AsyncClient
    poll_on_fresh_connections(httpx)
    client = httpx.AsyncClient("positional", timeout=7, follow_redirects=True)
    assert isinstance(client, original)
    assert client.args == ("positional",)
    assert client.kwargs["timeout"] == 7 and client.kwargs["follow_redirects"] is True
    assert client.kwargs["limits"].kwargs == {"max_keepalive_connections": 0}


def test_limits_a_caller_passes_are_replaced() -> None:
    httpx = _stand_in_httpx()
    poll_on_fresh_connections(httpx)
    client = httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=20))
    assert client.kwargs["limits"].kwargs == {"max_keepalive_connections": 0}


def test_the_module_imports_neither_httpx_nor_mineru() -> None:
    probe = "import sys, scripts.mineru_cli; print(sorted(set(sys.modules) & {'httpx', 'mineru'}))"
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "[]"
    assert mineru_cli.__doc__ is not None and "fresh connection" in mineru_cli.__doc__


# Run in the MinerU env: a local server records the client port of every request it answers, and an
# `httpx.AsyncClient` makes three requests, with the wrapper's patch applied or not.
_COUNT_CONNECTIONS = """
import asyncio, importlib.util, json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx

ports = []
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        ports.append(self.client_address[1])
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
if sys.argv[2] == "patched":
    spec = importlib.util.spec_from_file_location("mineru_cli", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.poll_on_fresh_connections(httpx)

async def poll():
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            (await client.get(f"http://127.0.0.1:{server.server_address[1]}/")).raise_for_status()

asyncio.run(poll())
server.shutdown()
print(json.dumps(len(set(ports))))
"""


def _conda() -> str | None:
    return os.environ.get("CONDA_EXE") or shutil.which("conda")


def _mineru_env_has_httpx() -> bool:
    conda = _conda()
    if conda is None:
        return False
    done = subprocess.run(
        [conda, "run", "-n", MINERU_ENV, "python", "-I", "-c", "import httpx"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


@pytest.mark.skipif(not _mineru_env_has_httpx(), reason=f"the {MINERU_ENV} env is not built here")
@pytest.mark.parametrize(("mode", "connections"), [("shipped", 1), ("patched", 3)])
def test_each_request_opens_its_own_connection_in_the_mineru_env(mode: str, connections: int) -> None:
    conda = _conda()
    assert conda is not None
    script = Path(mineru_cli.__file__).resolve()
    done = subprocess.run(
        [conda, "run", "-n", MINERU_ENV, "python", "-I", "-c", _COUNT_CONNECTIONS, str(script), mode],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == connections
