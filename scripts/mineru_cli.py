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

Only `httpx` and `mineru` are imported, and only when it runs, so the module imports without them.
"""

import sys
from typing import Any


def poll_on_fresh_connections(httpx_module: Any) -> None:
    """Make every `httpx.AsyncClient` created from now on keep no idle connection.

    Args:
        httpx_module: The `httpx` module `mineru` builds its client from.
    """
    base = httpx_module.AsyncClient
    limits = httpx_module.Limits(max_keepalive_connections=0)

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        base.__init__(self, *args, **{**kwargs, "limits": limits})

    httpx_module.AsyncClient = type("FreshConnectionAsyncClient", (base,), {"__init__": __init__})


def main() -> int:
    """Run `mineru` with the arguments this script was given."""
    import httpx
    from mineru.cli import client

    poll_on_fresh_connections(httpx)
    client.main(prog_name="mineru")
    return 0


if __name__ == "__main__":
    sys.exit(main())
