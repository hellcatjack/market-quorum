from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from codex_gateway.config import Settings, parse_codex_version
from codex_gateway.effective_config import EffectiveCodexConfig
from codex_gateway.errors import (
    CodexContextLimit,
    CodexInterrupted,
    CodexRateLimit,
    CodexRuntimeFailure,
    CodexTimeout,
    CodexUnavailable,
)
from codex_gateway.model_catalog import CodexModelOption, normalize_model_catalog
from codex_gateway.models import CodexTurnResult, TokenUsage
from codex_gateway.transport import AppServerTransport, JsonRpcError, TransportClosed

logger = logging.getLogger(__name__)
_RATE_LIMIT_CODES = {"usagelimitexceeded", "serveroverloaded", "sessionbudgetexceeded"}
_UNAUTHORIZED_CODES = {"unauthorized"}
_CONTEXT_LIMIT_CODES = {"contextwindowexceeded"}
_RESTART_DELAYS = (0.5, 1.0, 2.0, 4.0, 5.0)
_APP_SERVER_CONFIG_OVERRIDES = ("mcp_servers.playwright.enabled=false",)


class TransportLike(Protocol):
    running: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def request(self, method: str, params: dict) -> dict: ...

    async def notify(self, method: str, params: dict) -> None: ...


@dataclass
class _CompletionActivity:
    started_at: float
    last_progress_at: float
    request_id: str | None = None
    run_id: str | None = None
    retry_count: int = 0
    thread_id: str | None = None
    turn_id: str | None = None


@dataclass
class _TurnState:
    future: asyncio.Future
    activity: _CompletionActivity
    final_message: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class RuntimeActivity:
    accepting: bool
    active_completions: int
    oldest_active_seconds: float | None
    stalest_progress_seconds: float | None


def _normalize_error_info(info: Any) -> str | None:
    if isinstance(info, str):
        raw = info
    elif isinstance(info, dict) and info:
        raw = next(iter(info))
    else:
        return None
    return re.sub(r"[^a-z0-9]", "", str(raw).casefold()) or None


class CodexRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        transport_factory=None,
        validate_environment: bool = True,
        clock=time.monotonic,
    ):
        self.settings = settings
        self._validate_environment_enabled = validate_environment
        self._transport_factory = transport_factory or self._default_transport_factory
        self._clock = clock
        self._transport: TransportLike | None = None
        self._turns: dict[str, _TurnState] = {}
        self._activities: dict[int, _CompletionActivity] = {}
        self._next_activity_id = 1
        self._ready = False
        self._stopping = False
        self._restart_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._last_error = "not started"

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def health_detail(self) -> str:
        return "ready" if self._ready else self._last_error

    @property
    def active_completions(self) -> int:
        return len(self._activities)

    def activity_snapshot(self) -> RuntimeActivity:
        activities = tuple(self._activities.values())
        if not activities:
            return RuntimeActivity(
                accepting=self._ready and not self._stopping,
                active_completions=0,
                oldest_active_seconds=None,
                stalest_progress_seconds=None,
            )
        now = self._clock()
        return RuntimeActivity(
            accepting=self._ready and not self._stopping,
            active_completions=len(activities),
            oldest_active_seconds=max(max(0.0, now - item.started_at) for item in activities),
            stalest_progress_seconds=max(
                max(0.0, now - item.last_progress_at) for item in activities
            ),
        )

    def _require_transport(self) -> TransportLike:
        if not self._ready or self._transport is None:
            raise CodexUnavailable(self._last_error)
        return self._transport

    def _default_transport_factory(self, on_notification, on_exit):
        command = [self.settings.codex_bin, "app-server", "--listen", "stdio://"]
        for override in _APP_SERVER_CONFIG_OVERRIDES:
            command.extend(("--config", override))
        return AppServerTransport(
            command,
            on_notification=on_notification,
            on_exit=on_exit,
        )

    async def _validate_environment(self) -> None:
        try:
            version = await asyncio.create_subprocess_exec(
                self.settings.codex_bin,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Codex CLI was not found in PATH") from exc
        stdout, _ = await version.communicate()
        if version.returncode != 0:
            raise RuntimeError("Codex CLI version check failed")
        parsed = parse_codex_version(stdout.decode(errors="replace"))
        if parsed < self.settings.minimum_codex_version:
            raise RuntimeError(
                f"Codex CLI {parsed} is below minimum {self.settings.minimum_codex_version}"
            )
        if parsed > self.settings.verified_codex_version:
            logger.warning("Codex CLI is newer than the verified Gateway version")
        login = await asyncio.create_subprocess_exec(
            self.settings.codex_bin,
            "login",
            "status",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await login.wait()
        if login.returncode != 0:
            raise CodexUnavailable("Codex is not logged in; run 'codex login'")

    async def start(self) -> None:
        async with self._lifecycle_lock:
            self._stopping = False
            if self._ready and self._transport is not None and self._transport.running:
                return
            await self._start_validated_transport()

    async def _start_validated_transport(self) -> None:
        if self._validate_environment_enabled:
            try:
                await self._validate_environment()
            except CodexUnavailable as exc:
                self._last_error = exc.message
                self._ready = False
                return
        try:
            await self._start_transport()
        except Exception as exc:
            self._ready = False
            self._last_error = f"app-server start failed: {type(exc).__name__}"
            self._schedule_restart()

    async def stop(self) -> None:
        self._stopping = True
        restart = self._restart_task
        if restart is not None and restart is not asyncio.current_task():
            restart.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await restart
            if self._restart_task is restart:
                self._restart_task = None
        async with self._lifecycle_lock:
            self._ready = False
            self._last_error = "stopped"
            transport = self._transport
            self._transport = None
            if transport is not None:
                await transport.stop()
            self._fail_turns(CodexRuntimeFailure("Codex runtime stopped"))

    async def _start_transport(self) -> None:
        previous = self._transport
        if previous is not None:
            self._transport = None
            self._fail_turns(CodexRuntimeFailure("Codex app-server was replaced"))
            with contextlib.suppress(BaseException):
                await previous.stop()

        transport: TransportLike

        async def on_notification(method: str, params: dict[str, Any]) -> None:
            if self._transport is transport:
                await self._handle_notification(method, params)

        async def on_exit(error: BaseException) -> None:
            if self._transport is transport:
                await self._handle_exit(transport, error)

        transport = self._transport_factory(on_notification, on_exit)
        self._transport = transport
        try:
            await transport.start()
            await transport.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "tradingng_gateway",
                        "title": "TradingNG Codex Gateway",
                        "version": "0.1.0",
                    }
                },
            )
            await transport.notify("initialized", {})
            if self._transport is not transport or not transport.running:
                raise TransportClosed("Codex app-server exited during initialization")
        except BaseException:
            with contextlib.suppress(BaseException):
                await transport.stop()
            if self._transport is transport:
                self._transport = None
            raise
        self._ready = True
        self._last_error = "ready"

    async def effective_config(self) -> EffectiveCodexConfig:
        return await self._read_effective_config(Path(tempfile.gettempdir()))

    async def available_models(self) -> tuple[CodexModelOption, ...]:
        transport = self._require_transport()
        try:
            response = await transport.request(
                "model/list",
                {"limit": 100, "includeHidden": False},
            )
            return normalize_model_catalog(response, max_models=100)
        except (JsonRpcError, TransportClosed, TypeError, ValueError) as exc:
            raise CodexUnavailable("Codex model catalog is unavailable") from exc

    async def _read_effective_config(self, cwd: Path) -> EffectiveCodexConfig:
        transport = self._require_transport()
        response = await transport.request(
            "config/read",
            {"cwd": str(cwd), "includeLayers": False},
        )
        return EffectiveCodexConfig.from_read_response(response)

    async def complete(
        self,
        prompt: str,
        output_schema: dict,
        *,
        pinned_config: EffectiveCodexConfig | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
        retry_count: int = 0,
    ) -> CodexTurnResult:
        self._require_transport()
        activity_id = self._next_activity_id
        self._next_activity_id += 1
        now = self._clock()
        activity = _CompletionActivity(
            started_at=now,
            last_progress_at=now,
            request_id=request_id,
            run_id=run_id,
            retry_count=retry_count,
        )
        self._activities[activity_id] = activity
        try:
            with tempfile.TemporaryDirectory(prefix="tradingng-codex-") as cwd:
                turn = self._run_turn(
                    prompt,
                    output_schema,
                    Path(cwd),
                    pinned_config=pinned_config,
                    activity=activity,
                )
                timeout = self.settings.request_timeout_seconds
                if timeout is None:
                    return await turn
                try:
                    return await asyncio.wait_for(turn, timeout=timeout)
                except asyncio.TimeoutError as exc:
                    raise CodexTimeout(f"Codex request exceeded {timeout} seconds") from exc
        finally:
            self._activities.pop(activity_id, None)

    async def _run_turn(
        self,
        prompt: str,
        output_schema: dict,
        cwd: Path,
        *,
        pinned_config: EffectiveCodexConfig | None = None,
        activity: _CompletionActivity,
    ) -> CodexTurnResult:
        transport = self._transport
        if transport is None:
            raise CodexUnavailable("Codex app-server is unavailable")
        thread_id = None
        state = None
        try:
            effective = (
                pinned_config.require_complete()
                if pinned_config is not None
                else await self._read_effective_config(cwd)
            )
            logger.info(
                "resolved_codex_config model=%s reasoning_effort=%s",
                effective.model or "<default>",
                effective.reasoning_effort or "<default>",
            )
            thread_params = {
                "cwd": str(cwd),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "ephemeral": True,
            }
            if effective.model is not None:
                thread_params["model"] = effective.model
            started = await transport.request("thread/start", thread_params)
            thread_id = started["thread"]["id"]
            activity.thread_id = thread_id
            activity.last_progress_at = self._clock()
            state = _TurnState(asyncio.get_running_loop().create_future(), activity)
            self._turns[thread_id] = state
            turn_params = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": True},
                "outputSchema": output_schema,
            }
            if effective.reasoning_effort is not None:
                turn_params["effort"] = effective.reasoning_effort
            turn = await transport.request("turn/start", turn_params)
            activity.turn_id = turn["turn"]["id"]
            activity.last_progress_at = self._clock()
            logger.info(
                "codex_turn_started request_id=%s tradingng_run_id=%s retry_count=%d "
                "thread_id=%s turn_id=%s",
                activity.request_id or "<none>",
                activity.run_id or "<none>",
                activity.retry_count,
                activity.thread_id,
                activity.turn_id,
            )
            return await state.future
        except asyncio.CancelledError:
            if thread_id and state and activity.turn_id:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        transport.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": activity.turn_id},
                        ),
                        timeout=2,
                    )
            raise
        except (
            AttributeError,
            IndexError,
            JsonRpcError,
            KeyError,
            TransportClosed,
            TypeError,
            ValueError,
        ) as exc:
            raise CodexRuntimeFailure("Codex app-server request failed") from exc
        finally:
            if thread_id:
                self._turns.pop(thread_id, None)
            if state is not None:
                if not state.future.done():
                    state.future.cancel()
                elif not state.future.cancelled():
                    state.future.exception()

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        state = self._turns.get(params.get("threadId"))
        if state is None:
            return
        state.activity.last_progress_at = self._clock()
        try:
            self._apply_notification(state, method, params)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            if not state.future.done():
                state.future.set_exception(
                    CodexRuntimeFailure("Codex app-server notification was invalid")
                )

    def _apply_notification(self, state: _TurnState, method: str, params: dict[str, Any]) -> None:
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                state.final_message = item.get("text")
            return
        if method == "thread/tokenUsage/updated":
            last = (params.get("tokenUsage") or {}).get("last") or {}
            state.usage = TokenUsage(
                prompt_tokens=int(last.get("inputTokens", 0)),
                completion_tokens=int(last.get("outputTokens", 0)),
            )
            return
        if method != "turn/completed" or state.future.done():
            return
        turn = params.get("turn") or {}
        if state.final_message is None:
            for item in reversed(turn.get("items") or []):
                if item.get("type") == "agentMessage":
                    state.final_message = item.get("text")
                    break
        error = turn.get("error") or {}
        status = turn.get("status")
        info_code = _normalize_error_info(error.get("codexErrorInfo"))
        logger.info(
            "codex_turn_terminal request_id=%s tradingng_run_id=%s retry_count=%d "
            "thread_id=%s turn_id=%s status=%s codex_error_code=%s duration_ms=%d",
            state.activity.request_id or "<none>",
            state.activity.run_id or "<none>",
            state.activity.retry_count,
            state.activity.thread_id,
            state.activity.turn_id,
            status or "unknown",
            info_code or "<none>",
            int((self._clock() - state.activity.started_at) * 1000),
        )
        if status == "completed" and state.final_message is not None:
            state.future.set_result(CodexTurnResult(state.final_message, state.usage))
            return
        if info_code in _RATE_LIMIT_CODES:
            exception = CodexRateLimit("Codex capacity or session budget was exceeded")
        elif info_code in _UNAUTHORIZED_CODES:
            self._ready = False
            self._last_error = "Codex authentication is unavailable"
            exception = CodexUnavailable(self._last_error)
        elif info_code in _CONTEXT_LIMIT_CODES:
            exception = CodexContextLimit("Codex context window was exceeded")
        elif status == "interrupted":
            exception = CodexInterrupted("Codex turn was interrupted")
        else:
            exception = CodexRuntimeFailure("Codex turn failed")
        state.future.set_exception(exception)

    async def _handle_exit(self, transport: TransportLike, error: BaseException) -> None:
        if self._transport is not transport:
            return
        self._transport = None
        self._ready = False
        self._last_error = str(error)
        self._fail_turns(CodexRuntimeFailure("Codex app-server exited"))
        with contextlib.suppress(BaseException):
            await transport.stop()
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        if not self._stopping and (self._restart_task is None or self._restart_task.done()):
            self._restart_task = asyncio.create_task(self._restart_loop())

    async def _restart_loop(self) -> None:
        attempt = 0
        while not self._stopping:
            await asyncio.sleep(_RESTART_DELAYS[min(attempt, len(_RESTART_DELAYS) - 1)])
            async with self._lifecycle_lock:
                if self._stopping:
                    return
                if self._ready and self._transport is not None and self._transport.running:
                    return
                try:
                    await self._start_transport()
                    return
                except Exception as exc:
                    self._last_error = f"restart failed: {type(exc).__name__}"
                    attempt += 1

    def _fail_turns(self, error: BaseException) -> None:
        for state in self._turns.values():
            if not state.future.done():
                state.future.set_exception(error)
