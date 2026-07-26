from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from codex_gateway.app import create_app
from codex_gateway.config import Settings
from codex_gateway.models import CodexTurnResult, TokenUsage
from jsonschema import Draft7Validator
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel
from tradingagents.llm_clients.factory import create_llm_client

FIXED_DATA = json.loads(
    Path(__file__)
    .with_name("fixtures")
    .joinpath("fixed_market_data.json")
    .read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def disable_external_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


class ScriptedRuntime:
    ready = True
    health_detail = "ready"

    def __init__(self, messages):
        self.messages = iter(messages)
        self.prompts = []

    async def start(self):
        return None

    async def stop(self):
        return None

    async def complete(
        self,
        prompt,
        output_schema,
        *,
        pinned_config=None,
        request_id=None,
        run_id=None,
        retry_count=0,
    ):
        del pinned_config, request_id, run_id, retry_count
        self.prompts.append((prompt, output_schema))
        message = next(self.messages)
        Draft7Validator(output_schema).validate(message)
        return CodexTurnResult(json.dumps(message), TokenUsage(10, 5))


def start_server(runtime):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    app = create_app(runtime=runtime, settings=Settings(port=port))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        raise AssertionError("Uvicorn integration server did not start")
    return server, thread, sock, f"http://127.0.0.1:{port}/v1"


def stop_server(server, thread, sock):
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
    assert not thread.is_alive()


@tool
def get_fixed_price(symbol: str) -> dict:
    """Return fixed fixture market data for a ticker."""
    return FIXED_DATA[symbol]


def test_tradingagents_tool_result_round_trip(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "local")
    runtime = ScriptedRuntime(
        [
            {
                "result": {
                    "kind": "tool_calls",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "get_fixed_price",
                            "arguments": '{"symbol":"NVDA"}',
                        }
                    ],
                }
            },
            {
                "result": {
                    "kind": "message",
                    "content": "NVDA fixture price is 123.45 USD.",
                    "tool_calls": [],
                }
            },
        ]
    )
    server, thread, sock, base_url = start_server(runtime)
    try:
        llm = create_llm_client(
            provider="openai_compatible", model="codex", base_url=base_url
        ).get_llm()
        bound = llm.bind_tools([get_fixed_price])
        first = bound.invoke([HumanMessage(content="Get the fixed NVDA price")])
        assert first.tool_calls[0]["name"] == "get_fixed_price"
        observation = get_fixed_price.invoke(first.tool_calls[0]["args"])
        second = bound.invoke(
            [
                HumanMessage(content="Get the fixed NVDA price"),
                first,
                ToolMessage(
                    content=json.dumps(observation),
                    tool_call_id=first.tool_calls[0]["id"],
                ),
            ]
        )
        assert "123.45" in second.content
        encoded = (
            runtime.prompts[1][0]
            .split("<openai_request_json>\n", 1)[1]
            .split("\n</openai_request_json>", 1)[0]
        )
        messages = json.loads(encoded)["messages"]
        assert [message["role"] for message in messages] == [
            "user",
            "assistant",
            "tool",
        ]
        assert messages[-1]["tool_call_id"] == first.tool_calls[0]["id"]
    finally:
        stop_server(server, thread, sock)


class PriceDecision(BaseModel):
    symbol: str
    price: float


def test_tradingagents_structured_output(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "local")
    runtime = ScriptedRuntime(
        [
            {
                "result": {
                    "kind": "tool_calls",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "PriceDecision",
                            "arguments": '{"symbol":"NVDA","price":123.45}',
                        }
                    ],
                }
            }
        ]
    )
    server, thread, sock, base_url = start_server(runtime)
    try:
        llm = create_llm_client(
            provider="openai_compatible", model="codex", base_url=base_url
        ).get_llm()
        result = llm.with_structured_output(PriceDecision).invoke("Return the fixture")
        assert result == PriceDecision(symbol="NVDA", price=123.45)
    finally:
        stop_server(server, thread, sock)
