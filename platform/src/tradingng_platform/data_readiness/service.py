from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from tradingng_platform.vendors.stocklean import StockLeanClientError


def calculate_manifest_sha256(manifest) -> str | None:
    if manifest.candidate_request_id is None:
        return None
    items = [
        {
            "product": item.product,
            "instrument_id": item.instrument_id,
            "source_batch_id": item.source_batch_id,
            "version_ref": item.version_ref,
            "content_sha256": item.content_sha256,
            "max_observation_date": (
                item.max_observation_date.isoformat()
                if hasattr(item.max_observation_date, "isoformat")
                else item.max_observation_date
            ),
        }
        for item in manifest.items
    ]
    items.sort(
        key=lambda item: (
            item["product"],
            item["instrument_id"] or 0,
            item["version_ref"],
        )
    )
    payload = {
        "candidate_request_id": manifest.candidate_request_id,
        "analysis_date": (
            manifest.analysis_date.isoformat()
            if hasattr(manifest.analysis_date, "isoformat")
            else manifest.analysis_date
        ),
        "items": items,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DataReadinessService:
    def __init__(
        self,
        repository,
        stocklean_client,
        *,
        worker_id: str,
        poll_seconds: float = 15.0,
        lease_seconds: int = 60,
    ):
        self.repository = repository
        self.stocklean_client = stocklean_client
        self.worker_id = worker_id
        self.poll_seconds = max(2.0, float(poll_seconds))
        self.lease_seconds = max(15, int(lease_seconds))

    async def reconcile_one(self) -> bool:
        claim = await self.repository.claim_due(self.worker_id, self.lease_seconds)
        if claim is None:
            return False
        next_poll = datetime.now(timezone.utc) + timedelta(seconds=self.poll_seconds)
        try:
            status = await self.stocklean_client.candidate_status(int(claim.provider_request_id))
            if status.readiness == "waiting":
                await self.repository.apply_waiting(claim, status, next_poll)
                return True
            if status.readiness == "rejected":
                if status.error is not None and status.error.retryable:
                    retry_at = status.error.retry_after or next_poll
                    await self.repository.release_transient(claim, status.error.code, retry_at)
                else:
                    await self.repository.apply_rejected(claim, status)
                return True
            if status.manifest is None:
                await self.repository.apply_attention(claim, "manifest_reference_missing")
                return True
            manifest = await self.stocklean_client.manifest(status.manifest.snapshot_id)
            if manifest.manifest_sha256 != status.manifest.manifest_sha256:
                await self.repository.apply_attention(claim, "manifest_hash_mismatch")
                return True
            if calculate_manifest_sha256(manifest) != manifest.manifest_sha256:
                await self.repository.apply_attention(claim, "manifest_content_hash_mismatch")
                return True
            await self.repository.apply_ready(claim, status, manifest)
            return True
        except StockLeanClientError as exc:
            await self.repository.release_transient(claim, exc.code, next_poll)
            return True
