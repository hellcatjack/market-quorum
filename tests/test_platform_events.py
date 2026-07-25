from types import MethodType, SimpleNamespace

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.openai_client import OpenAIClient


class _MemoryLog:
    def get_past_context(self, ticker):
        return ""

    def store_decision(self, **kwargs):
        return None


class _CompiledGraph:
    def __init__(self, chunks):
        self.chunks = chunks

    def stream(self, initial_state, **kwargs):
        yield from self.chunks

    def invoke(self, initial_state, **kwargs):
        final_state = {}
        for chunk in self.chunks:
            final_state.update(chunk)
        return final_state


class _Propagator:
    def __init__(self):
        self.received_callbacks = None

    def create_initial_state(self, *args, **kwargs):
        return {"company_of_interest": "NVDA", "trade_date": "2026-07-25"}

    def get_graph_args(self, callbacks=None):
        self.received_callbacks = callbacks
        return {}


def make_minimal_graph(chunks, event_callback=None, callbacks=None):
    graph = SimpleNamespace(
        callbacks=callbacks or [],
        config={"checkpoint_enabled": False},
        debug=False,
        event_callback=event_callback,
        graph=_CompiledGraph(chunks),
        memory_log=_MemoryLog(),
        propagator=_Propagator(),
        resolve_instrument_context=lambda ticker, asset_type: "",
        _log_state=lambda trade_date, final_state: None,
        process_signal=lambda decision: "Hold",
    )
    graph._run_graph = MethodType(TradingAgentsGraph._run_graph, graph)
    return graph


def test_platform_event_callback_receives_each_stream_value():
    events = []
    graph = make_minimal_graph(
        chunks=[
            {"market_report": "m"},
            {"final_trade_decision": "**Rating**: Hold"},
        ],
        event_callback=events.append,
    )

    graph._run_graph("NVDA", "2026-07-25")

    assert events == [
        {"market_report": "m"},
        {"final_trade_decision": "**Rating**: Hold"},
    ]


def test_programmatic_run_passes_callbacks_to_tool_nodes():
    callback = object()
    graph = make_minimal_graph(
        chunks=[{"final_trade_decision": "Hold"}],
        callbacks=[callback],
    )

    graph._run_graph("NVDA", "2026-07-25")

    assert graph.propagator.received_callbacks == [callback]


def test_openai_compatible_client_forwards_default_headers(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "local")

    llm = OpenAIClient(
        "codex",
        "http://127.0.0.1:8000/v1",
        provider="openai_compatible",
        default_headers={"X-TradingNG-Run-ID": "run-1"},
    ).get_llm()

    assert llm.default_headers["X-TradingNG-Run-ID"] == "run-1"
