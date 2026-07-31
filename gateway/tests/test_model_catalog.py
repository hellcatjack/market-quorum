import pytest

from codex_gateway.model_catalog import CodexModelOption, normalize_model_catalog


def test_normalizes_reasoning_models_filters_reserved_and_deduplicates_efforts():
    payload = {
        "data": [
            {
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "hidden": False,
                "defaultReasoningEffort": "medium",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "medium"},
                    {"reasoningEffort": "medium"},
                    {"reasoningEffort": "high"},
                ],
            },
            {
                "id": "codex-fast",
                "defaultReasoningEffort": "high",
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
            },
            {
                "id": "no-reasoning-model",
                "defaultReasoningEffort": None,
                "supportedReasoningEfforts": [],
            },
        ]
    }

    assert normalize_model_catalog(payload) == (
        CodexModelOption(
            id="gpt-5.6-sol",
            default_reasoning_effort="medium",
            supported_reasoning_efforts=("low", "medium", "high"),
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": "not-a-list"},
        {"data": []},
        {
            "data": [
                {
                    "id": "gpt-5.6-sol",
                    "defaultReasoningEffort": "xhigh",
                    "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                }
            ]
        },
    ],
)
def test_rejects_malformed_or_entirely_unusable_catalog(payload):
    with pytest.raises(ValueError, match="model catalog"):
        normalize_model_catalog(payload)


def test_rejects_catalog_over_the_hard_cap():
    row = {
        "defaultReasoningEffort": "low",
        "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
    }
    payload = {"data": [{"id": f"model-{index}", **row} for index in range(101)]}

    with pytest.raises(ValueError, match="model catalog"):
        normalize_model_catalog(payload, max_models=100)
