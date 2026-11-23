"""업체 도우미 API의 로그인·승인·소유권·요청 횟수·응답 모양을 검증한다."""

import asyncio
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.agent import vendor_assist
from app.auth.deps import current_user_optional
from app.db import get_session
from app.shared.errors import (
    AppError,
    app_error_handler,
    validation_error_handler,
)
from app.vendor import assist_context, assist_service, deps, router
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    AssistResponse,
    CandidateField,
    FieldIntent,
    ModelInterpretation,
)


def body(form: str = "quote", text: str = "견적 555만원") -> dict:
    return {
        "request_id": "request-1",
        "form": form,
        "resource_id": str(uuid.uuid4()),
        "text": text,
        "current": {},
        "previous_candidate": None,
        "rule_fields": [],
    }


class Session:
    def __init__(self) -> None:
        self.open = False
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.open = False
        self.rollbacks += 1


@pytest.fixture
def route_client(monkeypatch):
    state = {
        "user": SimpleNamespace(id=uuid.uuid4(), role="vendor"),
        "review": "approved",
        "loaded": [],
        "rates": [],
        "model": [],
        "resource_error": None,
        "rate_error": None,
    }
    session = Session()

    async def review_of(s, user_id):
        assert s is session
        assert user_id == state["user"].id
        return (state["review"],) if state["review"] is not None else None

    async def load_context(s, vendor, parsed):
        assert s is session
        assert vendor.id == state["user"].id
        state["loaded"].append(parsed)
        if state["resource_error"]:
            raise state["resource_error"]
        session.open = True
        return AssistContext(today=date(2026, 11, 23))

    async def interpret(parsed, context):
        assert not session.open, "DB transaction must be closed before model"
        state["model"].append((parsed, context))
        return AssistResponse(
            request_id=parsed.request_id,
            form=parsed.form,
            source="llm",
            fields=[
                CandidateField(
                    field=field,
                    action="keep",
                    value=None,
                    evidence="",
                    source="llm",
                    status="valid",
                    alternatives=[],
                )
                for field in {
                    "quote": ("amount", "vat", "days", "valid"),
                    "period": ("start", "end", "excluded"),
                    "dates": ("days", "crew"),
                    "extra": ("title", "reason", "amount", "vat"),
                }[parsed.form]
            ],
            warnings=[],
        )

    async def hit(key, limit, window_s, message, *, fallback):
        state["rates"].append((key, limit, window_s, fallback))
        if state["rate_error"]:
            raise state["rate_error"]

    app = FastAPI()
    app.include_router(router.router)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user_optional] = lambda: state["user"]
    monkeypatch.setattr(deps.repo, "review_of", review_of)
    monkeypatch.setattr(router, "load_context", load_context)
    monkeypatch.setattr(assist_service, "interpret", interpret)
    monkeypatch.setattr(router.ratelimit, "hit", hit)
    with TestClient(app) as client:
        yield state, session, client


def test_http_uuid_string_is_accepted_and_db_released(route_client):
    state, session, client = route_client
    request = body()
    assert isinstance(request["resource_id"], str)
    got = client.post("/vendor/assist/interpret", json=request)
    assert got.status_code == 200, got.text
    response = got.json()
    assert response["request_id"] == request["request_id"]
    assert response["form"] == "quote"
    assert response["source"] == "llm"
    assert [item["field"] for item in response["fields"]] == ["amount", "vat", "days", "valid"]
    assert state["loaded"][0].resource_id == uuid.UUID(request["resource_id"])
    assert session.rollbacks == 1
    assert len(state["model"]) == 1
    assert [(x[1], x[2], x[3]) for x in state["rates"]] == [
        (20, 60, "local"),
        (200, 86400, "local"),
    ]


def test_anonymous_user_requires_login(route_client):
    state, session, client = route_client
    state["user"] = None
    got = client.post("/vendor/assist/interpret", json=body())
    assert got.status_code == 401
    assert got.json()["error"]["code"] == "login_required"
    assert not state["model"]
    assert not state["rates"]
    assert session.rollbacks == 0


def test_wrong_role_rejected(route_client):
    state, _, client = route_client
    state["user"] = SimpleNamespace(id=uuid.uuid4(), role="customer")
    got = client.post("/vendor/assist/interpret", json=body())
    assert got.status_code == 403
    assert got.json()["error"]["code"] == "forbidden"
    assert not state["model"]


@pytest.mark.parametrize("review", [None, "pending", "rejected"])
def test_vendor_must_be_approved(route_client, review):
    state, _, client = route_client
    state["review"] = review
    got = client.post("/vendor/assist/interpret", json=body())
    assert got.status_code == 403
    assert got.json()["error"]["code"] == "vendor_not_approved"
    assert not state["model"]
    assert not state["rates"]


def test_resource_404_does_not_reveal_other_vendor_data(route_client):
    state, session, client = route_client
    state["resource_error"] = AppError(404, "not_found", "업무를 찾을 수 없어요.")
    got = client.post("/vendor/assist/interpret", json=body())
    assert got.status_code == 404
    assert got.json()["error"]["code"] == "not_found"
    assert session.rollbacks == 1
    assert not state["model"]


def test_rate_limit_prevents_db_and_model_usage(route_client):
    state, session, client = route_client
    state["rate_error"] = AppError(
        429, "too_many_attempts", "요청이 너무 많아요.", headers={"Retry-After": "31"}
    )
    got = client.post("/vendor/assist/interpret", json=body())
    assert got.status_code == 429
    assert got.headers.get("Retry-After") == "31"
    assert not state["loaded"]
    assert not state["model"]
    assert session.rollbacks == 0


@pytest.mark.parametrize(
    "change",
    [
        {"resource_id": "not-a-uuid"},
        {"text": " "},
        {"current": {"amount": True}},
        {"current": {"amount": 1.5}},
        {"current": {"foreign_field": 1}},
        {"rule_fields": [{"field": "vat", "action": "set", "value": "maybe", "evidence": "x"}]},
        {"form": "missing"},
        {"text": "a" * 1001},
        {"request_id": "r", "extra": "unexpected"},
    ],
)
def test_http_schema_is_strict(route_client, change):
    state, _, client = route_client
    got = client.post("/vendor/assist/interpret", json={**body(), **change})
    assert got.status_code == 422, got.text
    assert got.json()["error"]["code"] == "invalid"
    assert not state["model"]


def test_full_text_rule_evidence_over_200_chars_accepted(route_client):
    _, _, client = route_client
    text = "견적 555만원 " + "추가 설명입니다. " * 20
    request = body(text=text)
    request["rule_fields"] = [
        {"field": "amount", "action": "set", "value": 5_550_000, "evidence": text}
    ]
    assert len(text) > 200
    got = client.post("/vendor/assist/interpret", json=request)
    assert got.status_code == 200, got.text


def test_context_loader_rejects_foreign_visits_and_deals():
    vendor = SimpleNamespace(id=uuid.uuid4())

    class Foreign:
        async def get(self, model, key):
            return SimpleNamespace(id=key, vendor_id=uuid.uuid4(), status="requested")

    for form in ("period", "quote", "dates", "extra"):
        req = AssistRequest.model_validate(body(form=form))
        with pytest.raises(AppError) as info:
            asyncio.run(assist_context.load_context(Foreign(), vendor, req))
        assert info.value.status == 404


def test_model_failure_falls_back_to_grounded_rule(monkeypatch):
    async def fail_model(request, context):
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(vendor_assist, "interpret", fail_model)
    request = AssistRequest.model_validate(
        {
            **body(),
            "rule_fields": [
                {"field": "amount", "action": "set", "value": 5_550_000, "evidence": "555만원"}
            ],
        }
    )
    got = asyncio.run(assist_service.interpret(request, AssistContext(today=date(2026, 11, 23))))
    assert got.source == "rule"
    assert {field.field: field for field in got.fields}["amount"].status == "valid"
    assert got.warnings


def test_model_hallucination_is_never_approved(monkeypatch):
    async def fake_model(request, context):
        return ModelInterpretation(
            fields=[FieldIntent(field="amount", action="set", value=9_999_999, evidence="555만원")]
        )

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    request = AssistRequest.model_validate(body())
    got = asyncio.run(assist_service.interpret(request, AssistContext(today=date(2026, 11, 23))))
    assert got.source == "llm"
    assert {field.field: field for field in got.fields}["amount"].status == "invalid"


@pytest.mark.parametrize(
    ("form", "state"),
    [
        ("period", "offered"),
        ("quote", "requested"),
        ("extra", "confirmed"),
        ("dates", "reselected"),
    ],
)
def test_context_rejects_invalid_workflow_states(form, state):
    vendor = SimpleNamespace(id=uuid.uuid4())

    class ContextDB:
        async def get(self, model, key):
            return SimpleNamespace(
                id=key,
                vendor_id=vendor.id,
                status=state,
                wish_start=date(2026, 12, 1),
                reply_due_at=None,
            )

    request = AssistRequest.model_validate(body(form=form))
    with pytest.raises(AppError) as error:
        asyncio.run(assist_context.load_context(ContextDB(), vendor, request))
    assert error.value.status == 409


def test_context_uses_quote_days_and_current_weekend_rule():
    from app.calc.schedule import JOB_MAX_DAYS
    from app.project.models import Project
    from app.quote.models import VendorQuote
    from app.schedule.models import CREW_MAX, Deal

    vendor = SimpleNamespace(id=uuid.uuid4())

    class ContextDB:
        def __init__(self) -> None:
            self.deal = SimpleNamespace(
                vendor_id=vendor.id,
                status="confirmed",
                quote_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                start_date=None,
            )

        async def get(self, model, key):
            if model is Deal:
                return self.deal
            if model is VendorQuote:
                return SimpleNamespace(days=5)
            if model is Project:
                return SimpleNamespace(rev=3)
            raise AssertionError("unexpected database read")

        async def scalar(self, query):
            return SimpleNamespace(project_rev=3, weekend_work=True)

        async def commit(self):
            raise AssertionError("assist context must never write")

    request = AssistRequest.model_validate(body(form="dates"))
    context = asyncio.run(assist_context.load_context(ContextDB(), vendor, request))
    assert context.required_days == 5
    assert context.days_max == JOB_MAX_DAYS
    assert context.crew_max == CREW_MAX
    assert context.no_weekend is False


def test_context_quote_exempt_vendor_forces_exempt_vat(monkeypatch):
    vendor = SimpleNamespace(id=uuid.uuid4())

    class QuoteDB:
        async def get(self, model, key):
            return SimpleNamespace(
                id=key, vendor_id=vendor.id, status="booked", wish_start=date(2026, 12, 1)
            )

    async def tax_kind_of(s, vendor_id):
        assert vendor_id == vendor.id
        return "exempt"

    monkeypatch.setattr(assist_context.vendor_service, "tax_kind_of", tax_kind_of)
    request = AssistRequest.model_validate(body(form="quote"))
    context = asyncio.run(assist_context.load_context(QuoteDB(), vendor, request))
    assert context.vat_modes == ["exempt"]
    assert context.days_max == 120
