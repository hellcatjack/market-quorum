import pytest
from pydantic import ValidationError

from codex_gateway.models import ChatCompletionRequest


def test_accepts_tradingagents_tool_round_trip():
    request = ChatCompletionRequest.model_validate(
        {
            "model": "codex",
            "messages": [
                {"role": "user", "content": "Get NVDA"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_price", "arguments": '{"symbol":"NVDA"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "123.45"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_price",
                        "parameters": {
                            "type": "object",
                            "properties": {"symbol": {"type": "string"}},
                            "required": ["symbol"],
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "get_price"}},
            "client_extension": "ignored",
        }
    )
    assert request.messages[2].tool_call_id == "call_1"
    assert request.model_extra == {"client_extension": "ignored"}


@pytest.mark.parametrize("field,value", [("stream", True), ("n", 2)])
def test_rejects_unsupported_generation_shape(field, value):
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
                field: value,
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [("stream", 0), ("stream", "false"), ("n", True), ("n", 1.0)],
)
def test_rejects_type_confusable_generation_shape(field, value):
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
                field: value,
            }
        )


def test_tool_message_requires_call_id():
    with pytest.raises(ValidationError, match="tool_call_id"):
        ChatCompletionRequest.model_validate(
            {
                "model": "codex",
                "messages": [{"role": "tool", "content": "123.45"}],
            }
        )


def test_rejects_multimodal_content():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "codex",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            }
        )
