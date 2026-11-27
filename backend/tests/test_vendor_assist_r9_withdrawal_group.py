"""R9 추가 동결: 필드별 철회 행위 객체 및 공동 술어."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_intent import intent_clauses
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret
from app.vendor.assist_withdrawal import complete_withdrawal_body, withdrawal_object

POSITIVE = [
    ("dates", "작업 인원 증원 요청은 철회합니다", {"crew"}, ("증원 요청은",)),
    ("dates", "투입 인원 감원 요청을 취소합니다", {"crew"}, ("감원 요청을",)),
    ("dates", "작업 날짜 변경 신청은 철회합니다", {"days"}, ("변경 신청은",)),
    ("period", "종료일 연장 신청을 취소합니다", {"end"}, ("연장 신청을",)),
    (
        "quote",
        "견적 금액 변경과 공사 기간 수정 요청을 철회합니다",
        {"amount", "days"},
        ("변경", "수정 요청을"),
    ),
    (
        "period",
        "방문 시작일 조정과 종료일 연장 요청을 철회합니다",
        {"start", "end"},
        ("조정", "연장 요청을"),
    ),
    (
        "dates",
        "작업 날짜 재배치 및 인원 증원 신청을 취소합니다",
        {"days", "crew"},
        ("재배치", "증원 신청을"),
    ),
    (
        "extra",
        "항목명 기재 요청과 사유 수정안을 철회합니다",
        {"title", "reason"},
        ("기재 요청", "수정안을"),
    ),
    (
        "quote",
        "금액 조정과 공사 기간 단축 및 유효기간 연장 요청은 철회합니다",
        {"amount", "days", "valid"},
        ("조정", "단축", "연장 요청은"),
    ),
    (
        "period",
        "앞서 제안한 시작일 변경과 제외일 목록 수정 요청을 취소합니다",
        {"start", "excluded"},
        ("변경", "수정 요청을"),
    ),
]

NEGATIVE = [
    ("quote", "견적 금액 증원 요청은 철회합니다"),
    ("dates", "작업 날짜 감원 요청은 취소합니다"),
    ("period", "시작일 신청을 철회합니다"),
    ("dates", "작업 날짜 변경 신청서 검토를 철회합니다"),
    ("dates", "작업 날짜 변경 신청 취소 여부를 확인합니다"),
    ("dates", "인원 증원 요청은 철회하지 마세요"),
    ("dates", "인원 감원 요청은 철회할까요?"),
    ("dates", "가능하면 작업 날짜 변경 신청은 철회합니다"),
    ("period", "방문 시작일 조정 내역과 종료일 연장 요청을 철회합니다"),
    ("quote", "견적 금액 변경 보고와 공사 기간 수정 요청을 철회합니다"),
    ("quote", "견적 금액 변경 또는 공사 기간 수정 요청을 철회합니다"),
    ("quote", "견적 금액 변경과 공사 기간 수정 요청을 철회하지 마세요"),
    ("quote", "견적 금액 변경과 공사 기간 수정 요청은 없던 걸로"),
    ("quote", "가능하면 견적 금액 변경과 공사 기간 수정 요청을 철회합니다"),
    ("quote", "견적 금액 변경과 총액 수정 요청을 철회합니다"),
    ("quote", "현재 금액 변경과 이전 공사 기간 수정 요청을 철회합니다"),
    ("period", "제외일 목록 중 12월 9일 선택과 종료일 연장 요청을 철회합니다"),
    ("period", "방문 시작일 조정과 종료일 연장 가능 여부를 확인합니다"),
    ("period", "방문 시작일 조정과 종료일 연장 요청은 철회했다고 합니다"),
    ("extra", "항목명 변경과 사유 수정이 가능하면 철회합니다"),
    ("extra", "항목명 변경과 고객 계약 해지 요청을 철회합니다"),
    ("extra", "사유: '항목명 변경과 금액 조정 요청을 철회합니다'"),
    ("dates", "공사 계약 변경 신청을 철회합니다"),
    ("dates", "작업 인원 증원 요청과 작업 날짜 수정 여부를 확인합니다"),
    ("dates", "작업 인원 증원 요청과 작업 날짜 감원 요청을 철회합니다"),
    ("dates", "작업 인원 증원 보고와 작업 날짜 변경 요청을 철회합니다"),
]

BASE = {
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "quote": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    assert not settings.llm_live

    async def forbidden(*args, **kwargs):
        pytest.fail("external provider forbidden")

    monkeypatch.setattr(llm, "structured", forbidden)


@pytest.mark.parametrize("form,text,fields,objects", POSITIVE)
def test_individually_owned_operation_objects(form, text, fields, objects):
    clauses = intent_clauses(form, text)
    assert len(clauses) == 1 and clauses[0].fields == fields
    proof = withdrawal_object(form, text)
    assert proof is not None and proof.definite, proof
    assert proof.fields == fields
    assert not proof.requires_value_proof
    assert text[proof.start : proof.end] == text
    assert text[proof.predicate_start :].startswith(("철회", "취소"))
    assert tuple(text[x.start : x.end] for x in proof.operation_units) == objects
    assert frozenset(field for x in proof.operation_units for field in x.fields) == fields


@pytest.mark.parametrize("form,text", NEGATIVE)
def test_ambiguous_or_business_operation_never_has_named_proof(form, text):
    proof = withdrawal_object(form, text)
    assert proof is None or not proof.definite or not proof.fields, proof


@pytest.mark.parametrize(
    "body",
    [
        "증원 요청은 철회합니다",
        "감원 요청을 취소합니다",
        "변경 신청은 철회합니다",
        "신청을 철회합니다",
    ],
)
def test_new_unowned_operation_not_generic_withdrawal(body):
    assert not complete_withdrawal_body(body, owned=False)


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("form,text,fields,objects", POSITIVE)
def test_independent_operations_release_via_actual_service(
    monkeypatch, producer, form, text, fields, objects
):
    context = AssistContext(
        today=date(2026, 11, 25),
        base_month=date(2026, 12, 1),
        days_max=120,
        crew_max=6,
        required_days=2 if form == "dates" else None,
        vat_modes=["included", "excluded"],
    )
    candidates = [
        FieldIntent(
            field=field,
            action="keep",
            value=None,
            evidence=text,
            intent_kind="retain_current",
            value_source="current",
        )
        for field in fields
    ]
    request = AssistRequest(
        request_id="r9-group",
        form=form,
        resource_id=UUID(int=179),
        text=text + ".",
        current=BASE[form],
        previous_candidate=None,
        rule_fields=candidates if producer != "model" else [],
    )
    calls = []

    async def stub(*args, **kwargs):
        calls.append(1)
        return ModelInterpretation(fields=candidates)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    out = {
        f.field: f
        for f in asyncio.run(
            interpret(request, context, use_model=producer != "rule", trace=trace)
        ).fields
    }
    assert len(calls) == int(producer != "rule")
    for field in BASE[form]:
        item = out[field]
        if field in fields:
            assert (item.status, item.action, item.intent_kind, item.value_source) == (
                "valid",
                "keep",
                "retain_current",
                "current",
            ), item
        else:
            assert item.action == "keep" and item.intent_kind == "no_mention", item
        assert not item.alternatives and item.value is None
