import json

import pytest

from codex_gateway.errors import CodexRuntimeFailure
from codex_gateway.models import ChatCompletionRequest, CodexTurnResult, TokenUsage
from codex_gateway.request_adapter import build_codex_prompt
from codex_gateway.response_adapter import to_chat_completion


def make_request(choice="auto", **updates):
    payload = {
        "model": "codex",
        "messages": [{"role": "user", "content": "Price NVDA"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_price",
                    "parameters": {
                        "type": "object",
                        "properties": {"symbol": {"type": "string"}},
                        "required": ["symbol"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": choice,
    }
    payload.update(updates)
    return ChatCompletionRequest.model_validate(payload)


def codex_result(kind, content, tool_calls):
    return json.dumps(
        {
            "result": {
                "kind": kind,
                "content": content,
                "tool_calls": tool_calls,
            }
        }
    )


def test_text_completion_mapping():
    request = ChatCompletionRequest.model_validate(
        {
            "model": "codex",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    result = CodexTurnResult(
        codex_result("message", "hi", []),
        TokenUsage(11, 7),
    )
    response = to_chat_completion(
        request,
        build_codex_prompt(request),
        result,
        completion_id="chatcmpl_test",
        created=123,
    )
    assert response["choices"][0]["message"]["content"] == "hi"
    assert response["choices"][0]["finish_reason"] == "stop"
    assert response["usage"]["total_tokens"] == 18


def test_tool_completion_mapping():
    request = make_request("required")
    result = CodexTurnResult(
        codex_result(
            "tool_calls",
            "",
            [{"name": "get_price", "arguments": '{"symbol":"NVDA"}'}],
        ),
        TokenUsage(5, 3),
    )
    response = to_chat_completion(
        request,
        build_codex_prompt(request),
        result,
        completion_id="chatcmpl_test",
        created=123,
        call_id_factory=lambda: "call_test",
    )
    message = response["choices"][0]["message"]
    assert message["content"] is None
    assert message["tool_calls"][0]["id"] == "call_test"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"symbol": "NVDA"}
    assert response["choices"][0]["finish_reason"] == "tool_calls"


def test_encoded_tool_arguments_are_validated_against_client_schema():
    request = make_request("required")
    result = CodexTurnResult(
        json.dumps(
            {
                "result": {
                    "kind": "tool_calls",
                    "content": "",
                    "tool_calls": [{"name": "get_price", "arguments": '{"symbol":"NVDA"}'}],
                }
            }
        ),
        TokenUsage(),
    )
    response = to_chat_completion(request, build_codex_prompt(request), result)
    arguments = response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"symbol": "NVDA"}


def test_encoded_tool_arguments_must_match_client_schema():
    request = make_request("required")
    result = CodexTurnResult(
        json.dumps(
            {
                "result": {
                    "kind": "tool_calls",
                    "content": "",
                    "tool_calls": [{"name": "get_price", "arguments": '{"symbol":7}'}],
                }
            }
        ),
        TokenUsage(),
    )
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(request, build_codex_prompt(request), result)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        codex_result(
            "tool_calls",
            "",
            [{"name": "missing", "arguments": "{}"}],
        ),
        codex_result(
            "message",
            "hi",
            [{"name": "get_price", "arguments": '{"symbol":"NVDA"}'}],
        ),
    ],
)
def test_bad_output_is_runtime_failure(payload):
    request = make_request()
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(
            request,
            build_codex_prompt(request),
            CodexTurnResult(payload, TokenUsage()),
        )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_json_constants_are_runtime_failures(constant):
    request = make_request(
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "record_value",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "number"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
    )
    payload = codex_result(
        "tool_calls",
        "",
        [
            {
                "name": "record_value",
                "arguments": '{"value":' + constant + "}",
            }
        ],
    )
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(
            request,
            build_codex_prompt(request),
            CodexTurnResult(payload, TokenUsage()),
        )


def test_lone_surrogate_in_message_is_runtime_failure():
    request = make_request(tools=[])
    payload = codex_result("message", "\ud800", [])
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(
            request,
            build_codex_prompt(request),
            CodexTurnResult(payload, TokenUsage()),
        )


def test_non_ascii_message_content_is_preserved():
    request = make_request(tools=[])
    payload = codex_result("message", "café 🐍", [])
    response = to_chat_completion(
        request,
        build_codex_prompt(request),
        CodexTurnResult(payload, TokenUsage()),
    )
    assert response["choices"][0]["message"]["content"] == "café 🐍"


def test_lone_surrogate_in_nested_tool_arguments_is_runtime_failure():
    request = make_request(
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "record_payload",
                    "parameters": {
                        "type": "object",
                        "properties": {"payload": {"type": "object"}},
                        "required": ["payload"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
    )
    payload = codex_result(
        "tool_calls",
        "",
        [
            {
                "name": "record_payload",
                "arguments": '{"payload":{"nested":"\\ud800"}}',
            }
        ],
    )
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(
            request,
            build_codex_prompt(request),
            CodexTurnResult(payload, TokenUsage()),
        )


def test_parallel_tool_calls_false_rejects_multiple_calls():
    request = make_request(parallel_tool_calls=False)
    result = CodexTurnResult(
        codex_result(
            "tool_calls",
            "",
            [
                {"name": "get_price", "arguments": '{"symbol":"NVDA"}'},
                {"name": "get_price", "arguments": '{"symbol":"AAPL"}'},
            ],
        ),
        TokenUsage(),
    )
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(request, build_codex_prompt(request), result)


def test_deeply_nested_json_is_runtime_failure():
    payload = "[" * 10_000 + "]" * 10_000
    with pytest.raises(CodexRuntimeFailure):
        to_chat_completion(
            make_request(),
            build_codex_prompt(make_request()),
            CodexTurnResult(payload, TokenUsage()),
        )
