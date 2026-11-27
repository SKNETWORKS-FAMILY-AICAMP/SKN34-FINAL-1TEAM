"""필드 소유권, 완전한 수량 및 이전값 참조의 독립 대조."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_intent import intent_clauses, retention_scopes
from app.vendor.assist_intent_safety import date_set_is_inquiry, date_value_clauses
from app.vendor.assist_normalize import crew_tokens, duration_quantity_risk, duration_tokens
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret


@pytest.fixture(autouse=True)
def _isolated_app():
    root = os.environ.get("R6STRUCTURE_ROOT")
    if root:
        backend = Path(root).resolve() / "backend"
        assert all(
            Path(module.__file__).resolve().is_relative_to(backend)
            for name, module in tuple(sys.modules.items())
            if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None)
        )


@pytest.mark.parametrize("noun", ["일수", "공기", "기간", "소요일"])
@pytest.mark.parametrize("marker", ["앞서 제안한", "직전에 정한", "이전에 입력한"])
def test_quote_reference_has_no_new_quantity(noun, marker):
    text = f"공사 일수는 {marker} {noun} 그대로 반영해 주세요."
    assert duration_tokens(text) == []
    assert not duration_quantity_risk(text)
    scopes = retention_scopes("quote", text)
    assert len(scopes) == 1
    assert scopes[0].fields == {"days"} and scopes[0].compatible


@pytest.mark.parametrize(
    "text",
    [
        "한두 번 확인합니다.",
        "두세 차례 확인합니다.",
        "서너 곳 확인합니다.",
        "두세대가 입주합니다.",
        "대여섯 사람과 상의합니다.",
        "예닐곱 장을 준비합니다.",
    ],
)
def test_non_day_counter_approximation_is_not_duration_quantity(text):
    assert duration_tokens(text) == []
    assert not duration_quantity_risk(text)


@pytest.mark.parametrize(
    "text",
    [
        "공사 일수는 한두 날로 생각합니다.",
        "공사 기간은 두세 일로 잡습니다.",
        "공기는 서너 날로 정합니다.",
        "시공은 대여섯 날 걸립니다.",
        "공사 일수는 예닐곱 날입니다.",
        "공사는 하루이틀 걸립니다.",
        "공사 기간은 사나흘 정도입니다.",
        "공사 일수는 한두입니다.",
        "공사 일수는 두세 시간입니다.",
        "공사 일수는 서너 주입니다.",
        "공사 일수는 한두 정도입니다.",
        "공사 일수는 5~7일입니다.",
        "공사 일수는 3.5일입니다.",
    ],
)
def test_ambiguous_day_quantity_remains_risk(text):
    assert duration_quantity_risk(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("공사 일수는 열두 날로 지정합니다.", [12]),
        ("작업 기간은 11일로 확정합니다.", [11]),
        ("공사 기간은 5일입니다. 2027년 2월 10일까지 유효합니다.", [5]),
        ("견적 효력은 2027년 2월 10일까지입니다.", []),
    ],
)
def test_duration_exact_quantity_token_not_calendar_or_label(text, expected):
    assert [value for _, _, value in duration_tokens(text)] == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("인원은 열두 명으로 지정합니다.", [12]),
        ("인력은 네 인으로 정합니다.", [4]),
        ("인원은 두세 명으로 생각합니다.", []),
        ("현장 인력은 4명분 작업량입니다.", []),
        ("인원은 4인치 자재를 운반합니다.", []),
        ("인력은 전에 정한 인원 그대로입니다.", []),
    ],
)
def test_crew_complete_unit_and_historical_reference(text, expected):
    assert [value for _, _, value in crew_tokens(text)] == expected


@pytest.mark.parametrize(
    "form,text,expected",
    [
        (
            "quote",
            "공사 일수는 앞서 제안한 기간 그대로 반영합니다. 금액 변경은 철회합니다.",
            ({"days"}, {"amount"}),
        ),
        (
            "period",
            "시작일은 앞서 정한 날짜 그대로입니다. 2월 13일, 17일, 21일은 제외일로 지정합니다.",
            ({"start"}, {"excluded"}),
        ),
        (
            "dates",
            "작업일은 이전에 정한 일정 그대로 반영합니다. 인원은 4명으로 지정합니다.",
            ({"days"}, {"crew"}),
        ),
        (
            "extra",
            "제목은 앞서 정한 항목 그대로 반영합니다. 금액은 38만원으로 확정합니다.",
            ({"title"}, {"amount"}),
        ),
    ],
)
def test_typed_history_reference_does_not_claim_independent_sibling(form, text, expected):
    clauses = intent_clauses(form, text)
    assert tuple(part.fields for part in clauses) == expected
    scopes = retention_scopes(form, text)
    assert len(scopes) == 1
    assert scopes[0].compatible and scopes[0].fields == expected[0]


@pytest.mark.parametrize(
    "form,text,expected",
    [
        ("quote", "공사 일수는 6일로 확정합니다. 만료일은 2월 25일로 해도 될까요?", {"valid"}),
        ("quote", "견적 효력은 2월 25일로 지정합니다. 공사 일수는 두세 날이면 될까요?", {"days"}),
        (
            "period",
            "2월 9일, 13일, 19일은 제외일로 지정해 주실래요? 종료일은 2월 28일로 잡아도 될까요?",
            {"end"},
        ),
        (
            "period",
            "시작일은 2월 6일로 정합니다. 2월 9일, 13일, 19일은 제외일로 정해도 될까요?",
            {"excluded"},
        ),
        ("dates", "작업일은 2월 9일과 16일로 지정합니다. 인원은 네 명으로 해도 될까요?", set()),
        ("dates", "작업일은 2월 9일과 16일로 옮겨도 될까요? 인원은 네 명으로 정합니다.", {"days"}),
    ],
)
def test_date_inquiry_only_rejects_its_own_field(form, text, expected):
    names = {
        "quote": ("days", "valid"),
        "period": ("start", "end", "excluded"),
        "dates": ("days",),
    }[form]
    assert {field for field in names if date_set_is_inquiry(form, field, text)} == expected


@pytest.mark.parametrize(
    "text",
    [
        "2월 9일, 13일, 19일은 제외일로 정합니다.",
        "제외일은 2월 9일, 13일, 19일로 정합니다.",
        "2월 9일하고 13일, 19일은 제외일로 정합니다.",
    ],
)
def test_exclusion_list_has_one_owner_in_both_existing_paths(text):
    clauses = intent_clauses("period", text)
    assert tuple(part.fields for part in clauses) == ({"excluded"},)
    assert tuple(fields for _, fields in date_value_clauses("period", text)) == ({"excluded"},)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,expected",
    [
        ("공사 일수는 두세 차례의 검수를 포함한 6일로 확정합니다.", True),
        ("공사 일수는 서너 곳의 작업을 포함한 6일로 지정합니다.", True),
        ("공사 일수는 6일 또는 한두 날로 변경합니다.", False),
        ("공사 일수는 6일에 두세 시간 정도를 더 추가합니다.", False),
    ],
)
def test_quantity_dimension_in_actual_service(monkeypatch, use_model, text, expected):
    assert not settings.llm_live
    field = FieldIntent(
        field="days",
        action="set",
        value=6,
        evidence="6일",
        intent_kind="explicit_change",
        value_source="utterance",
    )
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("provider forbidden")

    async def stub(*args, **kwargs):
        calls.append(True)
        return ModelInterpretation(fields=[field])

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub)
    req = AssistRequest(
        request_id="quantity-dimension",
        resource_id=UUID(int=179),
        form="quote",
        text=text,
        current={"amount": 2000000, "vat": "included", "days": 4, "valid": "2027-02-27"},
        rule_fields=[field],
        previous_candidate=None,
    )
    response = asyncio.run(
        interpret(
            req,
            AssistContext(today=date(2026, 11, 25), base_month=date(2027, 2, 1)),
            use_model=use_model,
        )
    )
    assert response.source == ("llm" if use_model else "rule")
    assert len(calls) == int(use_model)
    actual = next(f for f in response.fields if f.field == "days")
    if expected:
        assert (actual.status, actual.action, actual.value) == ("valid", "set", 6), actual
    else:
        assert actual.action not in ("set", "clear") and not actual.alternatives, actual


@pytest.mark.parametrize(
    "form,field,prefix,suffix,reference",
    [
        ("quote", "days", "공사 일수는 직전 후보 그대로 두고", "6일로 변경합니다.", "previous"),
        ("quote", "amount", "금액은 이전 후보 유지하고", "327만원으로 변경합니다.", "previous"),
        ("dates", "crew", "인원은 직전 후보 그대로 두고", "5명으로 지정합니다.", "previous"),
        (
            "dates",
            "days",
            "작업일은 직전 후보 그대로 두고",
            "2월 14일과 21일로 변경합니다.",
            "previous",
        ),
        ("period", "start", "시작일은 직전 후보 그대로 두고", "2월 8일로 변경합니다.", "previous"),
        ("extra", "title", "제목은 직전 후보 그대로 두고", "문턱 보강으로 변경합니다.", "previous"),
        (
            "extra",
            "reason",
            "사유는 이전 후보 그대로 두고",
            "접착 불량으로 변경합니다.",
            "previous",
        ),
        ("quote", "days", "공사 일수는 현재 입력값 그대로 두고", "6일로 변경합니다.", "current"),
    ],
)
@pytest.mark.parametrize("gap", [" ", "   "])
def test_linked_retention_prefix_is_competition_evidence_not_approval(
    form, field, prefix, suffix, reference, gap
):
    text = prefix + gap + suffix
    assert retention_scopes(form, text) == ()
    scopes = retention_scopes(form, text, include_linked_prefixes=True)
    assert len(scopes) == 1
    scope = scopes[0]
    assert scope.fields == {field} and scope.compatible and scope.linked_prefix
    assert scope.reference == reference
    assert text[scope.start : scope.end].rstrip() == prefix
    assert text[scope.end :].strip() == suffix


@pytest.mark.parametrize(
    "form,text,field",
    [
        ("quote", "공사 일수는 직전 후보 그대로 두고 금액은 325만원으로 변경합니다.", "days"),
        ("period", "시작일은 이전 후보 그대로 두고 종료일은 2월 25일로 지정합니다.", "start"),
        ("dates", "작업일은 직전 후보 그대로 두고 인원은 4명으로 정합니다.", "days"),
        ("extra", "제목은 이전 후보 그대로 두고 사유는 연결부 손상으로 정합니다.", "title"),
        ("quote", "공사 일수는 직전 후보 그대로 둡니다.", "days"),
    ],
)
def test_named_independent_continuation_stays_complete_retention(form, text, field):
    normal = retention_scopes(form, text)
    expanded = retention_scopes(form, text, include_linked_prefixes=True)
    assert normal == expanded
    assert len(normal) == 1 and normal[0].fields == {field}
    assert not normal[0].linked_prefix


@pytest.mark.parametrize(
    "text",
    [
        "공사 일수는 직전 후보 9일에서 6일로 변경합니다.",
        "공사 일수는 직전 후보 그대로 두지 말고 6일로 변경합니다.",
        "공사 일수는 직전 후보를 참고해 6일로 변경합니다.",
    ],
)
def test_reference_or_verified_change_is_not_affirmative_linked_keep(text):
    assert retention_scopes("quote", text, include_linked_prefixes=True) == ()
