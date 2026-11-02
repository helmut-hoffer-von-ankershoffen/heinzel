"""`hz ui` on a test kernel for a mounted browser test: one free port, one session token, stopped when the test ends."""

import threading
from typing import Self
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import Kernel
from heinzel.heads import web


class MountedServer:
    """The web head serving `kernel` in a thread; `with MountedServer(kernel, monkeypatch) as server:`."""

    def __init__(self, kernel: Kernel, monkeypatch: pytest.MonkeyPatch) -> None:
        """Serve `kernel`; `monkeypatch` records the uvicorn server so it can be asked to exit."""
        self.kernel = kernel
        self.url = ""
        self.token = ""
        self._servers: list[uvicorn.Server] = []
        original = uvicorn.Server.__init__
        servers = self._servers

        def remember(server: uvicorn.Server, config: uvicorn.Config) -> None:
            original(server, config)
            servers.append(server)

        monkeypatch.setattr(uvicorn.Server, "__init__", remember)
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        """Start serving and wait for the address.

        Raises:
            AssertionError: If the server does not announce itself in time.
        """
        started = threading.Event()

        def announce(address: str) -> None:
            self.url = address
            started.set()

        self._thread = threading.Thread(
            target=web.serve,
            args=(self.kernel,),
            kwargs={"port": 0, "browser": False, "announce": announce},
            daemon=True,
        )
        self._thread.start()
        assert started.wait(15), "hz ui did not start"
        self.token = parse_qs(urlsplit(self.url).fragment).get("token", [""])[0]
        assert self.token
        return self

    def __exit__(self, *exc: object) -> None:
        """Ask the server to stop and wait for its thread."""
        for server in self._servers[:1]:
            server.should_exit = True
        if self._thread is not None:
            self._thread.join(15)
            assert not self._thread.is_alive()
