"""One long-lived MCP stdio session, callable from synchronous code.

`mcp-server-datahub` is a subprocess speaking MCP over stdio, and the reference
client is asynchronous. Everything above `core/datahub/` is synchronous and has
no reason to become otherwise: the pipeline is a sequence of stages, not a
concurrent program, and turning it async to accommodate one transport would put
`await` in front of the severity engine.

So the async machinery is confined here. A dedicated event loop runs on a
background thread, the session is opened once and reused, and callers see a
plain `call(tool, arguments) -> Any`.

Why a persistent session rather than a connection per call: starting the server
costs roughly a second, and a three-hop lineage walk makes dozens of calls. A
connection per call turns a three-second analysis into a thirty-second one,
which is the difference between a check a reviewer waits for and one they learn
to ignore.

**What is NOT verified here**: the tool *names* and argument shapes this module
is driven with were read from the installed `mcp-server-datahub` package rather
than guessed (see `core/datahub/mcp_client.py`), but no call in this file has
been executed against a running DataHub in this repository. `blast-radius
doctor` is what turns that into a real answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
import threading
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

from core.errors import DataHubAccessError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp import ClientSession


@runtime_checkable
class ToolCaller(Protocol):
    """What a reader or writer needs from a session: call a tool, and close.

    Depending on this rather than on `McpSession` keeps the transport
    substitutable, which is what makes the tool-payload mapping testable
    without a subprocess.
    """

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call one tool and return its decoded result."""
        ...

    def close(self) -> None:
        """Release whatever the session holds."""
        ...


#: Bound on one tool call. Generous enough for a wide lineage query on a cold
#: catalog, short enough that a wedged server fails the check rather than
#: holding a CI job open until the runner's own timeout kills it.
CALL_TIMEOUT_SECONDS: float = 60.0

#: Bound on the initial handshake, which is fast when it works at all.
CONNECT_TIMEOUT_SECONDS: float = 30.0

#: Bound on shutdown. A server that will not exit must not stop us exiting.
CLOSE_TIMEOUT_SECONDS: float = 10.0


class McpSession:
    """A synchronous handle on one `mcp-server-datahub` process.

    Not thread safe: one session belongs to one reader or writer, which the
    pipeline drives from a single thread.
    """

    def __init__(
        self,
        server_command: str,
        gms_url: str,
        token: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._argv = shlex.split(server_command) or [server_command]
        self._gms_url = gms_url
        self._token = token
        self._extra_env = dict(extra_env or {})
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._stack: Any = None

    # -- lifecycle -----------------------------------------------------------

    def _server_env(self) -> dict[str, str]:
        """The environment the server subprocess is started with.

        Deliberately NOT the whole of `os.environ`: the CI job that runs
        blast-radius also holds an `ANTHROPIC_API_KEY` and a `GITHUB_TOKEN`, and
        the metadata server has no business being handed either.
        """
        env = {"DATAHUB_GMS_URL": self._gms_url}
        if self._token:
            env["DATAHUB_GMS_TOKEN"] = self._token
        env.update(self._extra_env)
        return env

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="blast-radius-mcp", daemon=True)
        thread.start()
        self._loop, self._thread = loop, thread
        return loop

    def _run(self, coro: Any, timeout: float) -> Any:
        """Run a coroutine on the session's loop and wait for it, bounded."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            msg = f"MCP call timed out after {timeout:g}s"
            raise DataHubAccessError(msg) from exc

    async def _open(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        stack = AsyncExitStack()
        parameters = StdioServerParameters(
            command=self._argv[0],
            args=self._argv[1:],
            env=self._server_env(),
        )
        read, write = await stack.enter_async_context(stdio_client(parameters))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack, self._session = stack, session

    def connect(self) -> Self:
        """Start the server and complete the MCP handshake. Idempotent."""
        if self._session is not None:
            return self
        try:
            import mcp  # noqa: F401
        except ImportError as exc:
            msg = (
                "the `mcp` extra is not installed, so the MCP access path is "
                "unavailable. Install it with `uv sync --extra mcp`, or set "
                "BLAST_RADIUS_DATAHUB_MODE=sdk to use the Python SDK path."
            )
            raise DataHubAccessError(msg) from exc

        try:
            self._run(self._open(), CONNECT_TIMEOUT_SECONDS)
        except DataHubAccessError:
            raise
        except Exception as exc:
            msg = (
                f"could not start {' '.join(self._argv)!r}: {exc}. "
                "Check that mcp-server-datahub is installed and on PATH."
            )
            raise DataHubAccessError(msg) from exc
        return self

    def close(self) -> None:
        """Shut the server down and stop the loop. Safe to call twice."""
        if self._stack is not None:
            # Shutdown must not mask the error that prompted it: a server that
            # died mid-analysis fails again on the way out, and the useful
            # message is the first one.
            with contextlib.suppress(Exception):
                self._run(self._stack.aclose(), CLOSE_TIMEOUT_SECONDS)
            self._stack = self._session = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=CLOSE_TIMEOUT_SECONDS)
            self._loop = self._thread = None

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # -- calls ---------------------------------------------------------------

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call one tool and return its decoded result.

        Raises `DataHubAccessError` on transport failure or on a tool that
        reports an error, so a caller never has to tell a failed read from a
        read that legitimately found nothing.
        """
        self.connect()
        session = self._session
        if session is None:  # pragma: no cover - connect() raises instead
            msg = "MCP session is not connected"
            raise DataHubAccessError(msg)

        try:
            result = self._run(session.call_tool(tool, arguments or {}), CALL_TIMEOUT_SECONDS)
        except DataHubAccessError:
            raise
        except Exception as exc:
            msg = f"MCP tool {tool!r} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        if getattr(result, "isError", False):
            msg = f"MCP tool {tool!r} returned an error: {_text_of(result)}"
            raise DataHubAccessError(msg)
        return decode_result(result)


def _text_of(result: Any) -> str:
    """Concatenate the text blocks of a tool result, for error messages."""
    parts = [
        block.text
        for block in (getattr(result, "content", None) or [])
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(parts).strip() or "(no detail)"


def decode_result(result: Any) -> Any:
    """Decode a `CallToolResult` into plain Python data.

    Prefers `structuredContent`, which FastMCP populates from the tool's own
    return value, and falls back to parsing the text block. Text that is not
    JSON is returned as a string rather than coerced: a caller that expected a
    mapping should fail on the shape, not on a silent empty dict that reads as
    "DataHub knows nothing about this".
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps a non-dict return value under "result".
        if set(structured) == {"result"}:
            return structured["result"]
        return structured

    text = _text_of(result)
    if not text or text == "(no detail)":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
