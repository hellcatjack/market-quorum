from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
ExitHandler = Callable[[BaseException], Awaitable[None]]

# Codex responses can contain long tool results, so retain several MiB per JSONL record.
STREAM_READER_LIMIT = 4 * 1024 * 1024
STDERR_READ_SIZE = 64 * 1024
STDERR_LOG_LIMIT = 8 * 1024

logger = logging.getLogger(__name__)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)(["']?(?:authorization|api[-_]?key|access[-_]?token|token|secret|password)"""
    r"""["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|(?:Bearer\s+)?[^\s,;]+)"""
)


async def _ignore_notification(method: str, params: dict[str, Any]) -> None:
    return None


async def _ignore_exit(error: BaseException) -> None:
    return None


class JsonRpcError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"JSON-RPC {code}: {message}")
        self.code = code
        self.message = message


class TransportClosed(RuntimeError):
    pass


class AppServerTransport:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        on_notification: NotificationHandler = _ignore_notification,
        on_exit: ExitHandler = _ignore_exit,
    ):
        self._argv = tuple(argv)
        self._on_notification = on_notification
        self._on_exit = on_exit
        self._process: asyncio.subprocess.Process | None = None
        self._closing_process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._notification_task: asyncio.Task[None] | None = None
        self._notification_queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self._startup_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._exit_tasks: set[asyncio.Task[None]] = set()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        while True:
            shutdown = self._shutdown_task
            if shutdown is not None:
                await asyncio.shield(shutdown)
                continue
            if self.running:
                return
            startup = self._startup_task
            if startup is None:
                startup = self._create_task(self._start_transition())
                self._startup_task = startup
            await asyncio.shield(startup)
            return

    async def stop(self) -> None:
        shutdown = self._shutdown_task
        if shutdown is None:
            shutdown = self._create_task(self._stop_transition())
            self._shutdown_task = shutdown
        await asyncio.shield(shutdown)

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.running:
            raise TransportClosed("app-server transport is not running")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def _start_transition(self) -> None:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_READER_LIMIT,
            )
            queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
            self._process = process
            self._notification_queue = queue
            self._reader_task = self._create_task(self._read_stdout(process, queue))
            self._stderr_task = self._create_task(self._read_stderr(process))
            self._notification_task = self._create_task(self._dispatch_notifications(queue))
        except BaseException:
            if process is not None:
                await self._cleanup_process(
                    process,
                    TransportClosed("app-server transport failed to start"),
                    exclude=asyncio.current_task(),
                )
            raise
        finally:
            if self._startup_task is asyncio.current_task():
                self._startup_task = None

    async def _stop_transition(self) -> None:
        try:
            startup = self._startup_task
            if startup is not None:
                with contextlib.suppress(BaseException):
                    await asyncio.shield(startup)
            process = self._process or self._closing_process
            await self._cleanup_process(
                process,
                TransportClosed("app-server transport stopped"),
                exclude=asyncio.current_task(),
            )
        finally:
            if self._shutdown_task is asyncio.current_task():
                self._shutdown_task = None

    async def _terminal_transition(
        self, process: asyncio.subprocess.Process, error: BaseException
    ) -> None:
        try:
            await self._cleanup_process(process, error, exclude=asyncio.current_task())
        finally:
            if self._shutdown_task is asyncio.current_task():
                self._shutdown_task = None

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise TransportClosed("app-server transport is not writable")
        encoded = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        try:
            async with self._write_lock:
                if self._process is not process or process.returncode is not None:
                    raise TransportClosed("app-server transport is not writable")
                process.stdin.write(encoded)
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            error = TransportClosed(f"app-server transport pipe closed: {exc}")
            self._begin_terminal(process, error)
            raise error from exc

    async def _read_stdout(
        self,
        process: asyncio.subprocess.Process,
        queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    ) -> None:
        error: BaseException = TransportClosed("app-server stdout closed")
        try:
            assert process.stdout is not None
            while raw := await process.stdout.readline():
                message = json.loads(raw)
                if "method" in message and "id" not in message:
                    queue.put_nowait((message["method"], message.get("params") or {}))
                    continue
                if "method" in message and "id" in message:
                    await self._write(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "Gateway does not accept server requests",
                            },
                        }
                    )
                    continue
                future = self._pending.get(message.get("id"))
                if future is None or future.done():
                    continue
                if "error" in message:
                    rpc_error = message["error"]
                    future.set_exception(
                        JsonRpcError(rpc_error.get("code", -1), rpc_error.get("message", ""))
                    )
                else:
                    future.set_result(message.get("result") or {})
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            error = TransportClosed(f"app-server protocol failed: {exc}")
        finally:
            self._begin_terminal(process, error)

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        pending = bytearray()
        discarding_line = False
        try:
            assert process.stderr is not None
            while chunk := await process.stderr.read(STDERR_READ_SIZE):
                offset = 0
                while offset < len(chunk):
                    if discarding_line:
                        newline = chunk.find(b"\n", offset)
                        if newline < 0:
                            break
                        offset = newline + 1
                        discarding_line = False
                        continue

                    newline = chunk.find(b"\n", offset)
                    end = len(chunk) if newline < 0 else newline
                    segment = chunk[offset:end]
                    capacity = STDERR_LOG_LIMIT - len(pending)
                    if len(segment) > capacity:
                        pending.extend(segment[:capacity])
                        self._log_stderr(bytes(pending), truncated=True)
                        pending.clear()
                        if newline < 0:
                            discarding_line = True
                            break
                        offset = newline + 1
                        continue

                    pending.extend(segment)
                    if newline < 0:
                        break
                    self._log_stderr(bytes(pending), truncated=False)
                    pending.clear()
                    offset = newline + 1
            if pending:
                self._log_stderr(bytes(pending), truncated=False)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._begin_terminal(process, TransportClosed(f"app-server stderr failed: {exc}"))

    @staticmethod
    def _log_stderr(raw: bytes, *, truncated: bool) -> None:
        if not raw:
            return
        message = raw.decode("utf-8", errors="replace")
        redacted = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", message)
        suffix = " [TRUNCATED]" if truncated else ""
        logger.warning("codex_app_server_stderr %s%s", redacted, suffix)

    async def _dispatch_notifications(
        self, queue: asyncio.Queue[tuple[str, dict[str, Any]]]
    ) -> None:
        while True:
            method, params = await queue.get()
            try:
                await self._on_notification(method, params)
            except asyncio.CancelledError:
                raise
            except BaseException:
                continue

    def _begin_terminal(self, process: asyncio.subprocess.Process, error: BaseException) -> None:
        if self._process is not process:
            return
        self._process = None
        self._closing_process = process
        self._fail_pending(error)
        if self._shutdown_task is None:
            shutdown = self._create_task(self._terminal_transition(process, error))
            self._shutdown_task = shutdown
            shutdown.add_done_callback(lambda task: self._schedule_exit_handler(error))

    async def _cleanup_process(
        self,
        process: asyncio.subprocess.Process | None,
        error: BaseException,
        *,
        exclude: asyncio.Task[Any] | None,
    ) -> None:
        if process is not None and self._process is process:
            self._process = None
        if process is not None:
            self._closing_process = process
        self._fail_pending(error)
        tasks = (self._reader_task, self._stderr_task, self._notification_task)
        self._reader_task = None
        self._stderr_task = None
        self._notification_task = None
        self._notification_queue = None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        for task in tasks:
            if task is not None and task is not exclude:
                task.cancel()
        for task in tasks:
            if task is not None and task is not exclude:
                with contextlib.suppress(BaseException):
                    await task
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(BaseException):
                    await process.wait()
            except BaseException:
                pass
        if self._closing_process is process:
            self._closing_process = None

    async def _call_exit_handler(self, error: BaseException) -> None:
        with contextlib.suppress(BaseException):
            await self._on_exit(error)

    def _schedule_exit_handler(self, error: BaseException) -> None:
        task = self._create_task(self._call_exit_handler(error))
        self._exit_tasks.add(task)
        task.add_done_callback(self._exit_tasks.discard)

    def _fail_pending(self, error: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[Any]) -> None:
        with contextlib.suppress(asyncio.CancelledError, BaseException):
            task.exception()

    def _create_task(self, coroutine: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        task.add_done_callback(self._consume_task_result)
        return task
