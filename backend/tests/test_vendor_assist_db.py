"""실제 로그인 쿠키·업무 소유권과 해석 전후 업무 데이터 불변을 확인한다."""

from app.agent import vendor_assist
from app.vendor.assist_schemas import FieldIntent, ModelInterpretation
from tests.conftest import query
from tests.test_visit_inbox import login, visit_of
from tests.test_visit_request import home, listed, send


def business_counts():
    return dict(
        query(
            "select (select count(*) from visits) visits,"
            " (select count(*) from visit_events) events,"
            " (select count(*) from vendor_quotes) quotes,"
            " (select count(*) from notifications) notifications,"
            " (select count(*) from jobs) jobs"
        )[0]
    )


def test_real_vendor_session_interprets_only_owned_visit_without_writes(api, monkeypatch):
    owner = listed(api, "assist-owner@mitjip.test", "도우미 시험 업체")
    listed(api, "assist-other@mitjip.test", "다른 시험 업체")
    _, project = home(api)
    sent = send(api, project["id"], [owner])
    assert sent.status_code == 201, sent.text
    visit_id = visit_of(owner)
    calls = []

    async def model(data, context):
        calls.append(data.resource_id)
        return ModelInterpretation(
            fields=[FieldIntent(field="amount", action="set", value=5550000, evidence="555만원")]
        )

    monkeypatch.setattr(vendor_assist, "interpret", model)
    body = {
        "request_id": "real-cookie-request",
        "form": "quote",
        "resource_id": visit_id,
        "text": "견적 555만원",
        "current": {},
        "previous_candidate": None,
        "rule_fields": [],
    }
    assert api.post("/vendor/assist/interpret", json=body).status_code == 403
    login(api, "assist-owner@mitjip.test")
    before = business_counts()
    response = api.post("/vendor/assist/interpret", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["fields"][0]["value"] == 5550000
    assert business_counts() == before
    assert len(calls) == 1
    login(api, "assist-other@mitjip.test")
    assert api.post("/vendor/assist/interpret", json=body).status_code == 404
    api.cookies.clear()
    assert api.post("/vendor/assist/interpret", json=body).status_code == 401
    assert len(calls) == 1
