import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingng_platform.vendors.stocklean import (
    STOCKLEAN_RESEARCH_INTAKE_CONTRACT_VERSION,
    StockLeanResearchCandidateResponse,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "stocklean"


@pytest.mark.parametrize(
    "name,readiness",
    [("research_candidate_waiting.json", "waiting"), ("research_candidate_ready.json", "ready")],
)
def test_stocklean_research_intake_fixture_round_trips(name, readiness):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    response = StockLeanResearchCandidateResponse.model_validate(payload)

    assert response.contract_version == STOCKLEAN_RESEARCH_INTAKE_CONTRACT_VERSION
    assert response.items[0].readiness == readiness
    assert response.model_dump(mode="json") == payload


def test_stocklean_ready_item_requires_manifest():
    payload = json.loads((FIXTURES / "research_candidate_ready.json").read_text(encoding="utf-8"))
    payload["items"][0]["manifest"] = None

    with pytest.raises(ValidationError):
        StockLeanResearchCandidateResponse.model_validate(payload)
