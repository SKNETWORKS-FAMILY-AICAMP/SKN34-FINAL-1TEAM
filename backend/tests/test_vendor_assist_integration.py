"""업체 도우미 실제 HTTP 계약과 LLM 대기 전 거래 종료를 확인한다."""

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.agent import vendor_assist
from app.db import get_session
from app.main import app
from app.vendor import assist_service, router
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
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
    client = TestClient(app)
    try:
        # 순수 HTTP 경계 시험은 공용 앱 lifespan을 다시 열거나 닫지 않는다.
        # 세션 공용 client의 Redis 연결을 다른 이벤트 루프에서 종료하면 안 된다.
        yield client, events
    finally:
        client.close()
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


def test_retained_candidates_keep_source_and_ground_later_period_change(monkeypatch):
    import asyncio

    async def model(*args, **kwargs):
        return ModelInterpretation(
            fields=[
                FieldIntent(field="excluded", action="set", value=["2026-12-04"], evidence="12/4"),
                FieldIntent(
                    field="start",
                    action="keep",
                    value=None,
                    evidence="기간 그대로",
                    intent_kind="retain_previous",
                    value_source="previous",
                ),
                FieldIntent(
                    field="end",
                    action="keep",
                    value=None,
                    evidence="기간 그대로",
                    intent_kind="retain_previous",
                    value_source="previous",
                ),
            ]
        )

    monkeypatch.setattr(vendor_assist, "interpret", model)
    data = AssistRequest.model_validate(
        {
            **payload(),
            "form": "period",
            "text": "기간 그대로, 12/4만 제외해 주세요",
            "current": {"start": None, "end": None, "excluded": []},
            "previous_candidate": {
                "request_id": "prior-message",
                "text": "12/2부터 12/7까지 가능해요",
                "fields": [
                    {"field": "start", "action": "set", "value": "2026-12-02", "evidence": "12/2"},
                    {"field": "end", "action": "set", "value": "2026-12-07", "evidence": "12/7"},
                ],
                "sources": {"start": "llm", "end": "rule"},
                "origins": {"start": "first-message", "end": "prior-message"},
                "baselines": {"start": None, "end": None},
            },
        }
    )
    response = asyncio.run(
        assist_service.interpret(
            data, AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))
        )
    )
    fields = {item.field: item for item in response.fields}
    assert fields["excluded"].status == "valid"
    assert fields["excluded"].value == ["2026-12-04"]
    assert fields["start"].source == "llm"
    assert fields["start"].origin_request_id == "first-message"
    assert fields["end"].source == "rule"
    assert fields["excluded"].origin_request_id == data.request_id
