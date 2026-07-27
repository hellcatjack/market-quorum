from tradingng_platform.records.contracts import InstrumentHistoryItem
from tradingng_platform.system.contracts import CapacityView


def test_capacity_and_history_contracts_expose_authoritative_model_routes():
    assert "model_routing" in CapacityView.model_fields
    assert {
        "gateway_fast_model",
        "gateway_fast_reasoning_effort",
        "gateway_slow_model",
        "gateway_slow_reasoning_effort",
    }.issubset(InstrumentHistoryItem.model_fields)
