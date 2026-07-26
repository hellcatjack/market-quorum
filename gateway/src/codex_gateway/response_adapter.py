from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from jsonschema import Draft7Validator
from jsonschema import ValidationError as JsonSchemaError

from codex_gateway.errors import CodexRuntimeFailure
from codex_gateway.models import ChatCompletionRequest, CodexTurnResult
from codex_gateway.request_adapter import CodexPrompt


def _completion_id() -> str:
    return f"chatcmpl_{uuid.uuid4().hex}"


def _call_id() -> str:
    return f"call_{uuid.uuid4().hex}"


def _reject_json_constant(constant: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {constant}")


def _ensure_utf8_encodable(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            try:
                current.encode("utf-8", "strict")
            except UnicodeEncodeError as exc:
                raise CodexRuntimeFailure("Codex returned text with invalid Unicode") from exc
        elif isinstance(current, dict):
            pending.extend(current)
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)


def to_chat_completion(
    request: ChatCompletionRequest,
    adapted: CodexPrompt,
    result: CodexTurnResult,
    *,
    completion_id: str | None = None,
    created: int | None = None,
    call_id_factory: Callable[[], str] = _call_id,
) -> dict[str, Any]:
    try:
        envelope = json.loads(result.final_message, parse_constant=_reject_json_constant)
        Draft7Validator(adapted.output_schema).validate(envelope)
    except (json.JSONDecodeError, JsonSchemaError, TypeError, ValueError, RecursionError) as exc:
        raise CodexRuntimeFailure("Codex returned an invalid structured response") from exc
    payload = envelope["result"]
    kind = payload["kind"]
    calls = payload["tool_calls"]
    if kind == "message" and calls:
        raise CodexRuntimeFailure("Codex returned tool calls in a message response")
    if kind == "tool_calls" and not calls:
        raise CodexRuntimeFailure("Codex returned an empty tool call response")
    if request.parallel_tool_calls is False and len(calls) > 1:
        raise CodexRuntimeFailure("Codex returned parallel tool calls when they are disabled")
    if any(call["name"] not in adapted.allowed_tool_names for call in calls):
        raise CodexRuntimeFailure("Codex returned an undeclared tool")
    tool_schemas = {tool.function.name: tool.function.parameters for tool in request.tools}
    decoded_calls = []
    try:
        for call in calls:
            arguments = json.loads(
                call["arguments"],
                parse_constant=_reject_json_constant,
            )
            Draft7Validator(tool_schemas[call["name"]]).validate(arguments)
            decoded_calls.append((call["name"], arguments))
    except (
        json.JSONDecodeError,
        JsonSchemaError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise CodexRuntimeFailure("Codex returned invalid tool arguments") from exc
    if kind == "message":
        message = {"role": "assistant", "content": payload["content"]}
        finish_reason = "stop"
    else:
        try:
            tool_calls = [
                {
                    "id": call_id_factory(),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    },
                }
                for name, arguments in decoded_calls
            ]
        except (TypeError, ValueError, RecursionError) as exc:
            raise CodexRuntimeFailure("Codex returned unserializable tool arguments") from exc
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        }
        finish_reason = "tool_calls"
    response = {
        "id": completion_id if completion_id is not None else _completion_id(),
        "object": "chat.completion",
        "created": created if created is not None else int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
        },
    }
    _ensure_utf8_encodable(response)
    return response
