"""업체 도우미 실제 HTTP 계약과 LLM 대기 전 거래 종료를 확인한다."""

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.agent import vendor_assist
from app.db import get_session
from app.main import app
from app.vendor import router
from app.vendor.assist_schemas import AssistContext, FieldIntent, ModelInterpretation
from app.vendor.deps import approved_vendor


@pytest.fixture
def boundary(monkeypatch):
    events = []

    class Session:
        async def rollback(self):
            events.append("rollback")

    async def user():
        return SimpleNamespace(id=UUID(int=1))

    async def session():
        yield Session()

    async def context(*args):
        events.append("context")
        return AssistContext(today=date(2026, 11, 23))

    async def limit(*args, **kwargs):
        return None

    async def model(*args, **kwargs):
        events.append("model")
        return ModelInterpretation(
            fields=[FieldIntent(field="amount", action="set", value=5550000, evidence="555만원")]
        )

    before = dict(app.dependency_overrides)
    app.dependency_overrides[approved_vendor] = user
    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(router, "load_context", context)
    monkeypatch.setattr(router.ratelimit, "hit", limit)
    monkeypatch.setattr(vendor_assist, "interpret", model)
    try:
        with TestClient(app) as client:
            yield client, events
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(before)


def payload():
    return {
        "request_id": "browser-request-1",
        "form": "quote",
        "resource_id": str(UUID(int=2)),
        "text": "견적 555만원",
        "current": {},
        "previous_candidate": None,
        "rule_fields": [],
    }


def test_browser_json_uuid_is_accepted_and_transaction_ends_before_model(boundary):
    client, events = boundary
    response = client.post("/vendor/assist/interpret", json=payload())
    assert response.status_code == 200, response.text
    assert events == ["context", "rollback", "model"]
    fields = {item["field"]: item for item in response.json()["fields"]}
    assert fields["amount"]["value"] == 5550000
    assert fields["amount"]["status"] == "valid"


@pytest.mark.parametrize("amount", [True, "5550000", 555.5], ids=["bool", "string", "fraction"])
def test_uuid_parsing_does_not_relax_money_types(boundary, amount):
    client, events = boundary
    body = payload()
    body["current"] = {"amount": amount}
    response = client.post("/vendor/assist/interpret", json=body)
    assert response.status_code == 422
    assert "model" not in events


def test_long_valid_message_can_include_full_rule_evidence(boundary):
    client, _ = boundary
    body = payload()
    body["text"] = "공사 안내 " * 40 + "견적 555만원"
    body["rule_fields"] = [
        {"field": "amount", "action": "set", "value": 5550000, "evidence": body["text"]}
    ]
    response = client.post("/vendor/assist/interpret", json=body)
    assert response.status_code == 200, response.text
