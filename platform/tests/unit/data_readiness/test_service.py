import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from tradingng_platform.data_readiness.service import DataReadinessService
from tradingng_platform.vendors.stocklean import StockLeanResearchCandidateItem


def _claim():
    return SimpleNamespace(
        requirement_id=uuid.UUID(int=1),
        run_id=uuid.UUID(int=2),
        provider_request_id="42",
        version=1,
        lease_owner="worker-1",
    )


def _status(readiness="waiting", *, manifest_hash="b" * 64):
    payload = {
        "external_request_key": "run:XYZ",
        "candidate_request_id": 42,
        "candidate_id": 7,
        "symbol": "XYZ",
        "scope": "research",
        "identity": {
            "asset_type": "stock",
            "vendor_symbol": "XYZ",
        },
        "readiness": readiness,
        "required_products": ["market"],
    }
    if readiness == "waiting":
        payload["job"] = {
            "batch_id": 5,
            "stage": "loading_market_history",
            "completed_items": 0,
            "total_items": 1,
        }
    elif readiness == "ready":
        payload["manifest"] = {
            "snapshot_id": "snap-1",
            "manifest_sha256": manifest_hash,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "max_observation_date": "2026-07-31",
        }
    else:
        payload["error"] = {
            "code": "historical_version_unavailable",
            "message": "Historical document is unavailable",
            "retryable": False,
        }
    return StockLeanResearchCandidateItem.model_validate(payload)


class _Repository:
    def __init__(self, claim=None):
        self.claim = claim
        self.actions = []

    async def claim_due(self, worker_id, lease_seconds):
        self.actions.append(("claim", worker_id))
        claim, self.claim = self.claim, None
        return claim

    async def apply_waiting(self, claim, status, next_poll_at):
        self.actions.append(("waiting", status.job.stage))
        return True

    async def apply_ready(self, claim, status, manifest):
        self.actions.append(("ready", manifest.snapshot_id))
        return True

    async def apply_rejected(self, claim, status):
        self.actions.append(("rejected", status.error.code))
        return True

    async def apply_attention(self, claim, code):
        self.actions.append(("attention", code))
        return True

    async def release_transient(self, claim, code, next_poll_at):
        self.actions.append(("transient", code))
        return True


class _Client:
    def __init__(self, status, manifest_hash=None, *, tamper_item=False):
        self.status = status
        self.manifest_hash = manifest_hash
        self.tamper_item = tamper_item

    async def candidate_status(self, request_id):
        assert request_id == 42
        return self.status

    async def manifest(self, snapshot_id):
        item = {
            "product": "market",
            "instrument_id": 7,
            "source_batch_id": 5,
            "version_ref": "prices:2026-07-01:2026-07-31:22",
            "content_sha256": ("c" if self.tamper_item else "a") * 64,
            "max_observation_date": "2026-07-31",
        }
        payload = {
            "candidate_request_id": 42,
            "analysis_date": "2026-07-31",
            "items": [item],
        }
        calculated = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return SimpleNamespace(
            snapshot_id=snapshot_id,
            manifest_sha256=self.manifest_hash or calculated,
            candidate_request_id=42,
            analysis_date=datetime(2026, 7, 31).date(),
            items=(SimpleNamespace(**item),),
        )


async def test_waiting_progress_is_rescheduled_without_queueing():
    repository = _Repository(_claim())
    service = DataReadinessService(
        repository,
        _Client(_status()),
        worker_id="worker-1",
        poll_seconds=15,
    )

    assert await service.reconcile_one() is True
    assert repository.actions[-1] == ("waiting", "loading_market_history")


async def test_ready_manifest_is_verified_before_atomic_queue_transition():
    client = _Client(_status("ready"))
    manifest = await client.manifest("snap-1")
    client.status.manifest.manifest_sha256 = manifest.manifest_sha256
    repository = _Repository(_claim())
    service = DataReadinessService(
        repository,
        client,
        worker_id="worker-1",
        poll_seconds=15,
    )

    assert await service.reconcile_one() is True
    assert repository.actions[-1] == ("ready", "snap-1")


async def test_manifest_hash_mismatch_needs_attention_instead_of_queueing():
    repository = _Repository(_claim())
    service = DataReadinessService(
        repository,
        _Client(_status("ready"), manifest_hash="c" * 64),
        worker_id="worker-1",
        poll_seconds=15,
    )

    assert await service.reconcile_one() is True
    assert repository.actions[-1] == ("attention", "manifest_hash_mismatch")


async def test_manifest_item_tampering_needs_attention_instead_of_queueing():
    client = _Client(_status("ready"))
    original = await client.manifest("snap-1")
    client.status.manifest.manifest_sha256 = original.manifest_sha256
    client.tamper_item = True
    client.manifest_hash = original.manifest_sha256
    repository = _Repository(_claim())
    service = DataReadinessService(
        repository,
        client,
        worker_id="worker-1",
        poll_seconds=15,
    )

    assert await service.reconcile_one() is True
    assert repository.actions[-1] == ("attention", "manifest_content_hash_mismatch")


async def test_permanent_rejection_fails_only_the_waiting_run():
    repository = _Repository(_claim())
    service = DataReadinessService(
        repository,
        _Client(_status("rejected")),
        worker_id="worker-1",
        poll_seconds=15,
    )

    assert await service.reconcile_one() is True
    assert repository.actions[-1] == (
        "rejected",
        "historical_version_unavailable",
    )
