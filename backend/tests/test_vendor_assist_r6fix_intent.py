"""R6: 실제 필드 별칭, 요청 확정성, 독립 필드의 회귀 검증."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_intent import field_mentions, retention_scopes
from app.vendor.assist_intent_safety import (
    clause_is_inquiry,
    date_set_is_inquiry,
    date_value_clauses,
    text_set_is_inquiry,
)
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret

_CURRENT = {
    "quote": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
}
_DATE_FIELDS = {
    "quote": ("days", "valid"),
    "period": ("start", "end", "excluded"),
    "dates": ("days",),
}


@pytest.mark.parametrize(
    "form,label,fields",
    [
        ("quote", "견적 효력", {"valid"}),
        ("quote", "견적서 유효기한", {"valid"}),
        ("quote", "견적서 효력 날짜", {"valid"}),
        ("quote", "유효일", {"valid"}),
        ("quote", "만료 날짜", {"valid"}),
        ("quote", "시공 기간", {"days"}),
        ("quote", "공사 일수", {"days"}),
        ("quote", "시공", {"days"}),
        ("quote", "견적서 총액", {"amount"}),
        ("quote", "별도 세금 조건", {"vat"}),
        ("quote", "면세 조건", {"vat"}),
        ("period", "첫 방문 가능일", {"start"}),
        ("period", "첫 실측 날", {"start"}),
        ("period", "마지막 실측 날짜", {"end"}),
        ("period", "방문 접수 마감일", {"end"}),
        ("period", "휴무일", {"excluded"}),
        ("period", "방문 제외할 날", {"excluded"}),
        ("period", "못 가는 날", {"excluded"}),
        ("period", "방문 가능 기간", {"start", "end"}),
        ("dates", "시공일", {"days"}),
        ("dates", "시공할 날", {"days"}),
        ("dates", "작업 인원 수", {"crew"}),
        ("extra", "추가 작업명", {"title"}),
        ("extra", "추가 사유", {"reason"}),
        ("extra", "시공 비용", {"amount"}),
        ("extra", "별도 세금 방식", {"vat"}),
    ],
)
def test_shared_field_alias_is_complete(form, label, fields):
    text = label + ": 확정"
    mentions = field_mentions(form, text)
    assert len(mentions) == 1
    assert mentions[0].fields == fields
    assert text[mentions[0].start : mentions[0].end] == label
    if form in _DATE_FIELDS:
        assert date_value_clauses(form, text) == ((text, frozenset(fields)),)


@pytest.mark.parametrize(
    "form,text,held",
    [
        (
            "quote",
            "부가세 별도 처리로 확정할게요. 견적 효력을 내년 1월 8일까지 둬도 되나요?",
            {"valid"},
        ),
        (
            "quote",
            "공사기간은 5일로 확정하고 견적서 유효기한은 12월 30일까지 가능할까요?",
            {"valid"},
        ),
        (
            "quote",
            "시공은 닷새로 해도 될까요? 유효기간은 12월 30일로 확정해주세요",
            {"days"},
        ),
        ("quote", "유효기간은 12월 30일로 확정하고 닷새는 가능할까요?", {"days"}),
        ("quote", "닷새는 가능할까요? 견적 효력은 12월 30일로 확정합니다", {"days"}),
        (
            "quote",
            "공사 기간은 3일로 확정하고 만료 날짜를 12월 30일로 정해 주시겠어요?",
            set(),
        ),
        ("quote", "견적 효력을 12월 30일로 정해 주실래요?", set()),
        ("quote", "유효기간 12월 30일로 둬도 되는지 알려주세요", {"valid"}),
        ("quote", "유효일은 12월 30일로 해도 되는지 알려주세요", {"valid"}),
        (
            "quote",
            "금액을 500만원으로 하면 유효기간은 12월 30일로 확정하겠습니다",
            {"valid"},
        ),
        (
            "quote",
            "금액을 500만원으로 하면 가능할까요? 유효기간은 12월 30일로 확정합니다",
            set(),
        ),
        (
            "quote",
            "기간은 4일로 확정합니다. 부가세가 별도인지 확인해 주세요",
            set(),
        ),
        ("quote", "견적서 효력 날짜는 12월 30일로 두지 마세요", {"valid"}),
        ("quote", "견적 효력은 12월 30일로 확정합니다. 그 요청은 취소", {"valid"}),
        ("quote", "공사 기간은 대략 닷새로 잡아주세요", {"days"}),
        (
            "period",
            "첫 방문 가능일은 12월 5일로 해도 될까요? 마지막 실측 날짜는 12월 30일로 정합니다",
            {"start"},
        ),
        (
            "period",
            "마지막 실측 날짜는 12월 30일까지 해도 되나요? 첫 방문일은 12월 5일로 확정합니다",
            {"end"},
        ),
        (
            "period",
            "휴무일을 12월 12일로 확정하고 종료일을 12월 30일로 정해도 될까요?",
            {"end"},
        ),
        (
            "period",
            "실측 접수 마감일 12월 30일로 정해 주세요. 휴무일은 아직 미정입니다",
            {"excluded"},
        ),
        ("period", "방문 가능 기간은 12월 5일부터 30일까지로 잡아 주세요", set()),
        (
            "dates",
            "시공할 날은 12월 12일과 24일로 해도 될까요? 작업자 수는 3명으로 확정합니다",
            {"days"},
        ),
        ("dates", "시공일을 12월 12일과 24일로 확정하고 아홉 명은 가능할까요?", set()),
        ("dates", "작업일을 12월 12일로 확정합니다. 열두 명은 가능할까요?", set()),
        ("dates", "작업일을 12월 12일로 확정합니다. 서너 명은 가능할까요?", set()),
        ("dates", "작업일을 12월 12일로 확정합니다. 그 요청은 취소합니다", {"days"}),
        (
            "dates",
            "작업일 9일은 손대지 말고, 16일 대신 23일로 바꿔 주세요",
            set(),
        ),
        (
            "dates",
            "앞에서 제안한 작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다",
            set(),
        ),
    ],
)
def test_date_inquiry_scopes_full_request(form, text, held):
    assert {field for field in _DATE_FIELDS[form] if date_set_is_inquiry(form, field, text)} == held


@pytest.mark.parametrize(
    "form,text,expected",
    [
        (
            "quote",
            "시공 기간은 닷새로 확정하고 견적서 효력 날짜는 12월 30일로 정합니다",
            ({"days"}, {"valid"}),
        ),
        (
            "period",
            "첫 방문일은 12월 5일로 변경하고 마지막 실측 날짜는 12월 30일로 확정합니다",
            ({"start"}, {"end"}),
        ),
        (
            "period",
            "방문 제외일은 12월 10일, 16일, 23일로 지정합니다",
            ({"excluded"},),
        ),
        (
            "period",
            "종료일 12월 10일 또는 12월 25일로 변경, 시작일은 12월 5일로",
            ({"end"}, {"start"}),
        ),
        (
            "dates",
            "시공 날짜는 12월 11일하고 22일, 작업자 6명으로 확정합니다",
            ({"days"}, {"crew"}),
        ),
        (
            "quote",
            "유효기간은 12월 30일로 확정합니다. 유효기간은 12월 29일로 확정합니다",
            ({"valid"}, {"valid"}),
        ),
    ],
)
def test_date_value_clauses_preserve_list_and_competing_clause_scope(form, text, expected):
    clauses = date_value_clauses(form, text)
    assert tuple(fields for _, fields in clauses) == expected
    if form == "period" and "또는" in text:
        # 명시적 선택지 두 개는 미정이나 조건으로 일괄 거부하지 않는다.
        assert not clause_is_inquiry(clauses[0][0])
        assert "12월 10일 또는 12월 25일" in clauses[0][0]
    if "16일, 23일" in text:
        assert "12월 10일, 16일, 23일" in clauses[0][0]


def test_alias_group_retention_does_not_split_validity_into_duration():
    text = "견적서 효력 날짜와 시공 기간은 앞서 제안한 값으로 유지합니다"
    scopes = retention_scopes("quote", text)
    assert len(scopes) == 1
    assert scopes[0].fields == {"valid", "days"}
    assert scopes[0].reference == "previous" and scopes[0].compatible


@pytest.mark.parametrize(
    "field,value,text,risk",
    [
        ("title", "문틀 보강", "제목은 문틀 보강으로 넣어도 되나요?", True),
        ("reason", "고정 불량", "사유는 고정 불량으로 정할까요?", True),
        (
            "reason",
            "고정 불량",
            "사유는 고정 불량으로 확정합니다. 그 요청은 취소합니다",
            True,
        ),
        (
            "title",
            "문틀 보강",
            "현장이 가능하면 제목은 문틀 보강으로 확정합니다",
            True,
        ),
        (
            "title",
            "문틀 보강",
            "금액이 50만원이면 제목은 문틀 보강으로 확정합니다",
            True,
        ),
        ("title", "문틀 보강", "제목은 문틀 보강으로 지정하지 마세요", True),
        (
            "title",
            "문틀 보강으로 넣어도 되나요",
            "제목은 문틀 보강으로 넣어도 되나요",
            True,
        ),
        ("reason", "미정", "사유는 미정", True),
        ("reason", "고정 불량일까요", "사유는 고정 불량일까요", True),
        (
            "reason",
            "고정 불량인지 모르겠어요",
            "사유는 고정 불량인지 모르겠어요",
            True,
        ),
        (
            "title",
            "문틀 보강 넣지 마세요",
            "제목은 문틀 보강 넣지 마세요",
            True,
        ),
        (
            "title",
            "문틀 보강 취소합니다",
            "제목은 문틀 보강 취소합니다",
            True,
        ),
        ("reason", "고객 계약 취소", "사유는 고객 계약 취소", False),
        ("reason", "미정", '사유는 "미정"으로 적어주세요', False),
        ("reason", "고객 요청사항 미정", "사유는 고객 요청사항 미정", False),
        (
            "reason",
            "검토 중인 자재의 변경",
            "사유는 검토 중인 자재의 변경으로 지정해주세요",
            False,
        ),
        (
            "reason",
            "고객 요청으로 확정된 도면과 불일치",
            "사유는 고객 요청으로 확정된 도면과 불일치",
            False,
        ),
        ("title", "문틀 보강", "제목은 문틀 보강으로 지정해 주실래요?", False),
        (
            "title",
            "문틀 보강",
            "제목은 문틀 보강으로 확정합니다. 사유는 미정입니다",
            False,
        ),
        (
            "reason",
            "고정 불량",
            "제목은 문틀 보강으로 할까요? 사유는 고정 불량으로 지정합니다",
            False,
        ),
        (
            "reason",
            "고정 불량",
            "사유는 고정 불량으로 확정하고 부가세는 별도인지 확인해 주세요",
            False,
        ),
    ],
)
def test_text_literal_content_is_not_the_assignment_intent(field, value, text, risk):
    assert text_set_is_inquiry(field, value, text) is risk


def _set(field, value, evidence, *, derived=False):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="derived" if derived else "utterance",
    )


def _keep(field):
    return FieldIntent(
        field=field, action="keep", value=None, evidence="", intent_kind="no_mention"
    )


def _run(monkeypatch, form, text, rules, model, *, use_model):
    assert not settings.llm_live
    context = AssistContext(
        today=date(2026, 11, 24),
        base_month=date(2026, 12, 1),
        days_max=120,
        crew_max=6,
        required_days=2 if form == "dates" else None,
        vat_modes=["included", "excluded"]
        if form == "extra"
        else ["included", "excluded", "exempt"],
    )
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    request = AssistRequest(
        request_id="r6fix-intent",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=_CURRENT[form],
        previous_candidate=None,
        rule_fields=rules,
    )
    trace = {}
    response = asyncio.run(interpret(request, context, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    assert response.source == ("llm" if use_model else "rule")
    return {field.field: field for field in response.fields}, trace


def _held(field):
    assert field.status in ("invalid", "uncertain") and field.action == "uncertain"
    assert field.value is None and not field.alternatives


def _valid(field, value):
    assert field.status == "valid" and field.action == "set" and field.value == value
    assert not field.alternatives


def test_r6_stored_second_response_does_not_offer_validity_inquiry(monkeypatch):
    # R6에서 안전 중단된 두 번째 요청의 실제 원문/추출 값/근거.
    text = "부가세 별도 처리로 확정할게요. 견적 효력을 내년 1월 8일까지 둬도 되나요?"
    model = [
        _keep("amount"),
        _set("vat", "excluded", "부가세 별도 처리로 확정할게요"),
        _keep("days"),
        _set("valid", "2027-01-08", "견적 효력을 내년 1월 8일까지", derived=True),
    ]
    fields, _ = _run(
        monkeypatch,
        "quote",
        text,
        [_keep(f) for f in _CURRENT["quote"]],
        model,
        use_model=True,
    )
    _held(fields["valid"])
    _valid(fields["vat"], "excluded")


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "form,text,bad,good",
    [
        (
            "quote",
            "견적서 유효기한은 12월 30일까지 가능할까요? 공사 기간은 4일로 확정합니다",
            ("valid", "2026-12-30", "견적서 유효기한은 12월 30일까지"),
            ("days", 4, "공사 기간은 4일"),
        ),
        (
            "quote",
            "시공은 닷새로 해도 될까요? 견적 효력은 12월 30일까지로 정해 주세요",
            ("days", 5, "시공은 닷새"),
            ("valid", "2026-12-30", "견적 효력은 12월 30일까지"),
        ),
        (
            "period",
            "마지막 실측 날짜는 12월 30일까지 해도 되나요? 첫 방문일은 12월 5일로 확정합니다",
            ("end", "2026-12-30", "마지막 실측 날짜는 12월 30일까지"),
            ("start", "2026-12-05", "첫 방문일은 12월 5일"),
        ),
        (
            "dates",
            "시공할 날은 12월 12일과 24일로 해도 될까요? 작업자 수는 3명으로 확정합니다",
            ("days", ["2026-12-12", "2026-12-24"], "시공할 날은 12월 12일과 24일"),
            ("crew", 3, "작업자 수는 3명"),
        ),
        (
            "extra",
            "제목은 문틀 보강으로 넣어도 되나요? 사유는 고정 불량으로 지정합니다",
            ("title", "문틀 보강", "제목은 문틀 보강"),
            ("reason", "고정 불량", "사유는 고정 불량"),
        ),
        (
            "extra",
            "사유는 고정 불량으로 정할까요? 부가세는 별도로 확정합니다",
            ("reason", "고정 불량", "사유는 고정 불량"),
            ("vat", "excluded", "부가세는 별도로 확정합니다"),
        ),
    ],
)
def test_full_clause_rejects_agreeing_wrong_extractors_but_keeps_sibling(
    monkeypatch, use_model, form, text, bad, good
):
    candidates = [_set(*bad), _set(*good)]
    fields, _ = _run(monkeypatch, form, text, candidates, candidates, use_model=use_model)
    _held(fields[bad[0]])
    _valid(fields[good[0]], good[1])


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "field,value,text",
    [
        ("reason", "고객 요청사항 미정", "사유는 고객 요청사항 미정"),
        (
            "reason",
            "검토 중인 자재의 변경",
            "사유는 검토 중인 자재의 변경으로 지정해주세요",
        ),
        ("title", "문틀 보강", "제목은 문틀 보강으로 지정해 주실래요?"),
        ("reason", "미정", '사유는 "미정"으로 적어주세요'),
    ],
)
def test_actual_text_values_remain_selectable(monkeypatch, use_model, field, value, text):
    candidate = _set(field, value, text)
    fields, _ = _run(monkeypatch, "extra", text, [candidate], [candidate], use_model=use_model)
    _valid(fields[field], value)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "field,value,text",
    [
        ("reason", "고정 불량", "사유는 고정 불량으로 확정합니다. 그 요청은 취소합니다"),
        ("title", "문틀 보강", "현장이 가능하면 제목은 문틀 보강으로 확정합니다"),
        ("title", "문틀 보강", "제목은 문틀 보강으로 지정하지 마세요"),
    ],
)
def test_string_assignment_is_not_confirmed_by_clipped_value(
    monkeypatch, use_model, field, value, text
):
    candidate = _set(field, value, value)
    fields, _ = _run(monkeypatch, "extra", text, [candidate], [candidate], use_model=use_model)
    _held(fields[field])
