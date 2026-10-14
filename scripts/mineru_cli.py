#!/usr/bin/env python3
"""Run MinerU's `mineru` command with every request to its local API on a fresh connection.

    python -I scripts/mineru_cli.py -p <images> -o <out> -b pipeline

It runs in the MinerU env (the parser runner invokes it there) and takes exactly the arguments `mineru`
takes. `mineru` starts a local API server, submits the pages and polls each task's status once a second
over one kept-alive HTTP connection. While the server parses, its event loop stalls for seconds at a
time, and the server's keep-alive timer can close the idle connection just as the next poll reaches it:
the connection is reset, the poll fails with an `httpx.ReadError` whose message is empty, the task is
reported failed with no reason, and the output of every page in the task is lost. On a whole half (three
tasks of up to 64 pages) every task failed this way. With no idle connection kept, each poll opens its
own, so a connection is never closed under a request; nothing else about the run changes.

The patch reaches `mineru` only because its client module imports `httpx` and builds its client as
`httpx.AsyncClient(...)` (MinerU 3.4.5, the version `envs/mineru.yml` pins). Before `mineru` runs, that is
checked, and a client module built any other way is refused rather than run unpatched.

Only `httpx` and `mineru` are imported, and only when it runs, so the module imports without them.
"""

import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

# How MinerU's client module builds the client that polls its local API.
CLIENT_CONSTRUCTION = "httpx.AsyncClient("


class MineruWrapperError(RuntimeError):
    """MinerU's client no longer builds its client the way the patch reaches."""


def poll_on_fresh_connections(httpx_module: Any) -> type:
    """Make every `httpx.AsyncClient` created from now on keep no idle connection.

    Args:
        httpx_module: The `httpx` module `mineru` builds its client from.

    Returns:
        The class now bound to `httpx_module.AsyncClient`.
    """
    base = httpx_module.AsyncClient
    limits = httpx_module.Limits(max_keepalive_connections=0)

    class FreshConnectionAsyncClient(base):  # type: ignore[valid-type, misc]
        """An `httpx.AsyncClient` that keeps no idle connection."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **{**kwargs, "limits": limits})

    httpx_module.AsyncClient = FreshConnectionAsyncClient
    return FreshConnectionAsyncClient


def check_patch_applies(client: ModuleType, httpx_module: ModuleType, patched: type) -> None:
    """Refuse a MinerU client module the patch does not reach.

    Args:
        client: MinerU's `mineru.cli.client` module.
        httpx_module: The `httpx` module that was patched.
        patched: The class `poll_on_fresh_connections` bound.

    Raises:
        MineruWrapperError: The client module does not use the patched `httpx` module, binds its own
            `AsyncClient`, or does not build its client as `httpx.AsyncClient(...)`.
    """
    source_file = Path(inspect.getfile(client))
    problems = []
    if getattr(client, "httpx", None) is not httpx_module:
        problems.append("it does not import the `httpx` module as `httpx`")
    if "AsyncClient" in vars(client):
        problems.append("it binds `AsyncClient` itself (e.g. `from httpx import AsyncClient`)")
    if getattr(httpx_module, "AsyncClient", None) is not patched:
        problems.append("`httpx.AsyncClient` is not the patched class")
    if CLIENT_CONSTRUCTION not in source_file.read_text():
        problems.append(f"it never builds a client as `{CLIENT_CONSTRUCTION}...)`")
    if problems:
        raise MineruWrapperError(
            "MinerU's client is not reached by the fresh-connection patch, so its status polls could "
            "fail.\n"
            f"  What:     {source_file} is not built the way the patch expects: {'; '.join(problems)}.\n"
            f"  Where:    {source_file} (its httpx import and client construction), and "
            f"{Path(__file__).resolve()} -> poll_on_fresh_connections\n"
            f"  Expected: `import httpx` at module level and the polling client built as "
            f"`{CLIENT_CONSTRUCTION}timeout=..., follow_redirects=True)`, as in mineru==3.4.5.\n"
            "  Recover:  rebuild the env at the pinned version (`conda env update -f envs/mineru.yml "
            "--prune`), or update poll_on_fresh_connections to reach the new client."
        )


def main() -> int:
    """Run `mineru` with the arguments this script was given, after checking the patch reaches it."""
    import httpx
    from mineru.cli import client

    patched = poll_on_fresh_connections(httpx)
    try:
        check_patch_applies(client, httpx, patched)
    except MineruWrapperError as err:
        print(str(err), file=sys.stderr)
        return 1
    client.main(prog_name="mineru")
    return 0


if __name__ == "__main__":
    sys.exit(main())
