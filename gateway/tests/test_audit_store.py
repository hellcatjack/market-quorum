import asyncio
import hashlib
import json

from codex_gateway.audit_store import AuditStore


async def test_store_writes_paired_exchange_without_secrets(tmp_path):
    store = AuditStore(tmp_path)
    request_body = b'{"model":"codex","messages":[{"role":"user","content":"SPCX"}]}'
    pending = await store.begin(
        method="POST",
        path="/v1/chat/completions",
        headers={
            "authorization": "Bearer secret",
            "content-type": "application/json",
            "x-stainless-runtime": "CPython",
        },
        body=request_body,
    )

    request_file = tmp_path / "exchanges" / "0001-request.json"
    assert request_file.is_file()
    assert "secret" not in request_file.read_text()

    response_body = b'{"choices":[],"usage":{"total_tokens":3}}'
    await store.complete(
        pending,
        status_code=200,
        headers={"content-type": "application/json", "set-cookie": "private"},
        body=response_body,
    )

    request_record = json.loads(request_file.read_text())
    response_record = json.loads((tmp_path / "exchanges" / "0001-response.json").read_text())
    assert request_record["body_sha256"] == hashlib.sha256(request_body).hexdigest()
    assert request_record["headers"] == {
        "content-type": "application/json",
        "x-stainless-runtime": "CPython",
    }
    assert "private" not in json.dumps(response_record)

    lines = (tmp_path / "exchanges.jsonl").read_text().splitlines()
    assert len(lines) == 1
    exchange = json.loads(lines[0])
    assert exchange["sequence"] == 1
    assert exchange["response"]["status_code"] == 200
    assert exchange["request"] == request_record
    assert exchange["response"] == response_record


async def test_store_allocates_unique_sequences_concurrently(tmp_path):
    store = AuditStore(tmp_path)

    async def begin_one(index):
        return await store.begin(
            method="POST",
            path="/v1/chat/completions",
            headers={"content-type": "application/json"},
            body=json.dumps({"index": index}).encode(),
        )

    pending = await asyncio.gather(*(begin_one(index) for index in range(20)))

    assert sorted(item.sequence for item in pending) == list(range(1, 21))
    assert len(list((tmp_path / "exchanges").glob("*-request.json"))) == 20


async def test_store_records_typed_failure_without_exception_text(tmp_path):
    store = AuditStore(tmp_path)
    pending = await store.begin(
        method="GET",
        path="/healthz",
        headers={},
        body=b"",
    )

    await store.fail(pending, error_type="ConnectError")

    error_file = tmp_path / "exchanges" / "0001-error.json"
    error_record = json.loads(error_file.read_text())
    terminal = json.loads((tmp_path / "exchanges.jsonl").read_text())
    assert error_record == {"type": "ConnectError"}
    assert terminal["error"] == error_record
    assert "response" not in terminal
