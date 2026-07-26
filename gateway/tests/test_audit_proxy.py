import json

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from codex_gateway.audit_proxy import create_audit_proxy, validate_loopback_host


async def test_proxy_forwards_body_query_and_authorization_but_audits_no_secret(tmp_path):
    upstream = FastAPI()

    @upstream.post("/v1/chat/completions")
    async def complete(request: Request):
        body = await request.body()
        assert request.url.query == "trace=yes"
        assert request.headers["authorization"] == "Bearer local-secret"
        return Response(body, media_type="application/json", headers={"x-upstream": "yes"})

    upstream_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
    )
    app = create_audit_proxy(
        upstream_url="http://upstream",
        audit_dir=tmp_path,
        client=upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://audit"
    ) as client:
        payload = {"model": "codex", "messages": [{"role": "user", "content": "SPCX"}]}
        response = await client.post(
            "/v1/chat/completions?trace=yes",
            json=payload,
            headers={"authorization": "Bearer local-secret"},
        )

    await upstream_client.aclose()
    assert response.status_code == 200
    assert response.json() == payload
    exchange_text = (tmp_path / "exchanges.jsonl").read_text()
    assert "local-secret" not in exchange_text
    exchange = json.loads(exchange_text)
    assert exchange["request"]["path"] == "/v1/chat/completions?trace=yes"
    assert exchange["request"]["body"] == payload
    assert exchange["response"]["body"] == payload


async def test_proxy_records_typed_upstream_failure(tmp_path):
    async def fail(_request):
        raise httpx.ConnectError("private upstream detail")

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    app = create_audit_proxy(
        upstream_url="http://upstream",
        audit_dir=tmp_path,
        client=upstream_client,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://audit"
    ) as caller:
        response = await caller.get("/healthz")

    await upstream_client.aclose()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "private upstream detail" not in response.text
    exchange = json.loads((tmp_path / "exchanges.jsonl").read_text())
    assert exchange["error"]["type"] == "ConnectError"


def test_validate_loopback_host_rejects_external_bind():
    assert validate_loopback_host("127.0.0.1") == "127.0.0.1"
    assert validate_loopback_host("::1") == "::1"
    with pytest.raises(ValueError, match="loopback"):
        validate_loopback_host("0.0.0.0")
