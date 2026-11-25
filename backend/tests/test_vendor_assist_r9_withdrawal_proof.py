"""R9 동결 대조: 실제 대상과 철회할 행위 객체 및 값 증명 분리."""

import asyncio
import importlib
import importlib.util
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor import assist_validation
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
)
from app.vendor.assist_service import interpret

FORM = "app.vendor.assist_withdrawal"
LEXICAL = importlib.import_module(FORM) if importlib.util.find_spec(FORM) else None


@pytest.fixture(autouse=True)
def offline_and_isolated(monkeypatch):
    assert not settings.llm_live
    if root := os.environ.get("R9_WITHDRAWAL_ROOT"):
        backend = Path(root).resolve() / "backend"
        assert all(
            Path(module.__file__).resolve().is_relative_to(backend)
            for name, module in tuple(sys.modules.items())
            if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None)
        )

    async def forbidden(*args, **kwargs):
        pytest.fail("external provider forbidden")

    monkeypatch.setattr(llm, "structured", forbidden)


# Legacy grammar and the same execution family used by typed assignment:
# an operation is not a newly hardcoded full sentence.
@pytest.mark.parametrize(
    "body,expected",
    [
        ("변경은 취소합니다", True),
        ("수정 요청을 철회합니다", True),
        ("바꾸는 건 없던 걸로 할게요", True),
        ("앞서 제안은 취소합니다", True),
        ("선택은 취소해 주세요", True),
        ("후보는 모두 철회합니다", True),
        ("그 요청은 취소합니다", True),
        ("조정은 철회합니다", True),
        ("연장 요청은 취소합니다", True),
        ("단축하는 제안은 철회합니다", True),
        ("등록 요청은 취소합니다", True),
        ("교체하는 건 취소할게요", True),
        ("지정은 취소하고", True),
        ("조정 완료 보고를 취소합니다", False),
        ("조정이 가능하면 철회합니다", False),
        ("조정은 철회할까요?", False),
        ("조정은 철회하지 마세요", False),
        ("조정은 철회할지 고민합니다", False),
        ("조정 내역 조회를 취소합니다", False),
        ("조정 요청이라는 말을 취소합니다", False),
        ("조정 요청은 아직 철회하지 않았습니다", False),
        ("조정은 없던 걸로", False),
        ("계약 조정은 철회합니다", False),
    ],
)
def test_complete_withdrawal_body(body, expected):
    if LEXICAL is None:
        assert bool(assist_validation._CANCEL_REQUEST.fullmatch(body)) == expected
    else:
        assert LEXICAL.complete_withdrawal_body(body) == expected


@pytest.mark.parametrize(
    "form,text,fields,operation,reference",
    [
        ("period", "시작 날짜 조정은 철회합니다", {"start"}, "조정은", "pending"),
        ("period", "종료일 연장 요청은 취소합니다", {"end"}, "연장 요청은", "pending"),
        (
            "period",
            "실측 가능 기간과 제외일 목록 조정 요청은 철회합니다",
            {"start", "end", "excluded"},
            "조정 요청은",
            "pending",
        ),
        (
            "quote",
            "금액과 공사 기간 변경 요청은 취소합니다",
            {"amount", "days"},
            "변경 요청은",
            "pending",
        ),
        (
            "quote",
            "앞서 제안한 유효기간 단축 요청은 철회합니다",
            {"valid"},
            "단축 요청은",
            "previous",
        ),
        (
            "dates",
            "작업 날짜 지정은 취소합니다",
            {"days"},
            "지정은",
            "pending",
        ),
        (
            "dates",
            "투입 인원 수와 작업 날짜 변경 요청은 철회합니다",
            {"crew", "days"},
            "변경 요청은",
            "pending",
        ),
        (
            "extra",
            "추가 사유 등록 요청을 철회합니다",
            {"reason"},
            "등록 요청을",
            "pending",
        ),
        (
            "extra",
            "항목명과 사유 수정 요청은 취소합니다",
            {"title", "reason"},
            "수정 요청은",
            "pending",
        ),
        (
            "quote",
            "현재 금액 조정 요청은 취소합니다",
            {"amount"},
            "조정 요청은",
            "current",
        ),
    ],
)
def test_named_operation_object(form, text, fields, operation, reference):
    if LEXICAL is None:
        assert all(
            (result := assist_validation._pending_cancellation(form, name, text)) is not None
            and result[1]
            for name in fields
        )
        return
    proof = LEXICAL.withdrawal_object(form, text)
    assert proof is not None and proof.definite, proof
    assert proof.fields == fields
    assert proof.reference == reference
    assert text[proof.operation_start : proof.operation_end] == operation
    assert text[proof.start : proof.end] == text
    assert not proof.requires_value_proof and proof.value_span is None


@pytest.mark.parametrize(
    "form,text",
    [
        ("period", "시작 날짜 조정 내역 보고를 철회합니다"),
        ("period", "시작 날짜 조정은 철회하지 마세요"),
        ("period", "시작 날짜 조정은 철회할까요?"),
        ("period", "가능하면 시작 날짜 조정은 철회합니다"),
        ("period", "시작 날짜 조정이 완료되면 철회합니다"),
        ("period", "시작 날짜 조정은 없던 걸로"),
        ("period", "시작 날짜 조정은 철회할지 고민합니다"),
        ("extra", "사유: '항목명 조정 요청은 철회합니다'"),
        ("period", "시작 날짜 목록 조회는 철회합니다"),
        ("period", "제외일 목록 중 12월 9일 선택만 취소합니다"),
        ("period", "시작 날짜 또는 종료일 조정 요청은 철회합니다"),
        ("period", "시작 날짜 조정 요청의 가능 여부를 확인하고 철회합니다"),
    ],
)
def test_non_withdrawal_object_not_definite(form, text):
    if LEXICAL is None:
        from app.vendor.assist_schemas import FORM_FIELDS

        assert not any(
            (result := assist_validation._pending_cancellation(form, name, text)) is not None
            and result[1]
            for name in FORM_FIELDS[form]
        )
        return
    proof = LEXICAL.withdrawal_object(form, text)
    assert proof is None or not proof.definite, proof


@pytest.mark.parametrize(
    "form,text,field,value_text,mode",
    [
        ("quote", "부가세 미포함 선택은 취소합니다", "vat", None, "excluded"),
        ("quote", "면세 선택은 철회합니다", "vat", None, "exempt"),
        (
            "quote",
            "견적 금액을 471만원으로 조정하는 요청은 철회합니다",
            "amount",
            "471만원",
            None,
        ),
        (
            "period",
            "시작일을 2027년 2월 7일로 변경하는 요청은 취소합니다",
            "start",
            "2027년 2월 7일",
            None,
        ),
        (
            "dates",
            "인원을 4명으로 변경하는 요청은 철회합니다",
            "crew",
            "4명",
            None,
        ),
    ],
)
def test_explicit_value_is_not_unconditional_release(form, text, field, value_text, mode):
    if LEXICAL is None:
        # Baseline state verification, not absence of the new dataclass.
        proof = assist_validation._pending_cancellation(form, field, text, require_value_proof=True)
        assert proof is None or not proof[1], proof
        return
    proof = LEXICAL.withdrawal_object(form, text)
    assert proof is not None and proof.definite, proof
    assert proof.fields == {field} and proof.requires_value_proof
    if value_text is not None:
        assert text[proof.value_span[0] : proof.value_span[1]] == value_text
    else:
        assert proof.selected_modes == {mode}


@pytest.mark.parametrize("producer", ("rule", "model", "both"))
@pytest.mark.parametrize(
    "cancel,release",
    [
        ("시작 날짜 조정은 철회합니다", True),
        ("시작일 지정 요청은 취소합니다", True),
        ("시작 날짜 연장 요청은 철회합니다", True),
        ("시작 날짜 조정 내역 보고를 철회합니다", False),
        ("시작 날짜 조정은 철회하지 마세요", False),
        ("시작 날짜 조정은 철회할까요?", False),
        ("가능하면 시작 날짜 조정은 철회합니다", False),
    ],
)
def test_actual_service_withdrawal_keeps_independent_end(monkeypatch, producer, cancel, release):
    # The first phrase of the executed R8 T2 and independent authored
    # operation-family controls. No unseen corpus/gold is loaded.
    text = cancel + ". 실측 마감은 2027년 2월 26일로 설정해 주세요."
    current = {"start": "2027-02-03", "end": "2027-02-23", "excluded": []}
    context = AssistContext(
        today=date(2026, 11, 25),
        base_month=date(2027, 2, 1),
        days_max=120,
        crew_max=6,
        vat_modes=["included", "excluded"],
    )
    keep = FieldIntent(
        field="start",
        action="keep",
        value=None,
        evidence=cancel,
        intent_kind="retain_current",
        value_source="current",
    )
    end = FieldIntent(
        field="end",
        action="set",
        value="2027-02-26",
        evidence="실측 마감은 2027년 2월 26일",
        intent_kind="explicit_change",
        value_source="utterance",
    )
    request = AssistRequest(
        request_id="r9-follow",
        form="period",
        resource_id=UUID(int=179),
        text=text,
        current=current,
        previous_candidate=None,
        rule_fields=[keep, end] if producer != "model" else [],
    )
    calls = []

    async def stub(*args, **kwargs):
        calls.append(1)
        return ModelInterpretation(fields=[keep, end])

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    out = {
        f.field: f
        for f in asyncio.run(interpret(request, context, use_model=producer != "rule")).fields
    }
    assert len(calls) == int(producer != "rule")
    assert (out["end"].status, out["end"].action, out["end"].value) == (
        "valid",
        "set",
        "2027-02-26",
    ), out["end"]
    assert not out["start"].alternatives
    if release:
        assert (out["start"].status, out["start"].action, out["start"].intent_kind) == (
            "valid",
            "keep",
            "retain_current",
        ), out["start"]
        assert out["start"].value_source == "current"
    else:
        assert out["start"].action not in ("set", "clear")
        assert not (
            out["start"].status == "valid" and out["start"].intent_kind == "retain_current"
        ), out["start"]


# 추가 동결: 같은 명시 값이라도 이전 후보 자체와 설명한 변경행위를 구분한다.
@pytest.mark.parametrize(
    "form,text,field,value_text,kind",
    [
        (
            "quote",
            "앞서 제안한 견적 금액 410만원 후보를 철회합니다",
            "amount",
            "410만원",
            "candidate",
        ),
        (
            "period",
            "앞서 제안한 시작일 12월 7일 후보를 철회합니다",
            "start",
            "12월 7일",
            "candidate",
        ),
        ("dates", "앞서 제안한 인원 4명 후보를 취소합니다", "crew", "4명", "candidate"),
        (
            "extra",
            "앞서 제안한 항목명 문틀 보강 후보를 취소합니다",
            "title",
            "문틀 보강",
            "candidate",
        ),
        (
            "extra",
            "앞서 제안한 사유 '현장 보강 필요' 후보를 철회합니다",
            "reason",
            "'현장 보강 필요'",
            "candidate",
        ),
        (
            "quote",
            "견적 금액 410만원으로 변경하려던 요청은 철회합니다",
            "amount",
            "410만원",
            "operation",
        ),
    ],
)
def test_exact_value_object_kind(form, text, field, value_text, kind):
    if LEXICAL is None:
        old = assist_validation._pending_cancellation(form, field, text, require_value_proof=True)
        assert bool(old and old[1]) == (kind == "operation")
        return
    proof = LEXICAL.withdrawal_object(form, text)
    assert proof is not None and proof.definite and proof.requires_value_proof, proof
    assert proof.fields == {field} and proof.value_kind == kind
    assert text[slice(*proof.value_span)] == value_text
    assert proof.reference == ("previous" if kind == "candidate" else "pending")


@pytest.mark.parametrize(
    "form,text,field",
    [
        ("quote", "견적 금액 410만원 후보를 철회합니다", "amount"),
        ("quote", "현재 금액 410만원 후보를 철회합니다", "amount"),
        ("period", "앞서 제안한 시작일 12월 7일 후보를 철회할까요?", "start"),
        ("extra", "사유: '앞서 제안한 항목명 문틀 보강 후보를 취소합니다'", "title"),
    ],
)
def test_value_candidate_requires_actual_historical_owner(form, text, field):
    if LEXICAL is None:
        old = assist_validation._pending_cancellation(form, field, text, require_value_proof=True)
        assert old is None or not old[1]
        return
    proof = LEXICAL.withdrawal_object(form, text)
    assert proof is None or not proof.definite, proof


@pytest.mark.parametrize(
    "body",
    [
        "조정안을 철회합니다",
        "수정안은 취소합니다",
        "기재 요청은 철회합니다",
        "재배치 요청을 철회합니다",
        "증액 요청은 철회합니다",
    ],
)
def test_operation_family_nominal_composition(body):
    if LEXICAL is None:
        assert assist_validation._CANCEL_REQUEST.fullmatch(body)
    else:
        assert LEXICAL.complete_withdrawal_body(body)
