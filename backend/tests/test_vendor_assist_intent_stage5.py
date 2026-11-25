"""5단계 독립 QA에서 확인한 현재값 유지와 문자열 지시문 대조 시험."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 24), base_month=date(2026, 12, 1), crew_max=6)
CURRENT = {"title": "보강", "reason": "현장 추가", "amount": 1_000_000, "vat": "included"}


def change(field, value, evidence):
    return FieldIntent(field=field, action="set", value=value, evidence=evidence)


def keep(field, evidence, source):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind="retain_current" if source == "current" else "retain_previous",
        value_source=source,
    )


def run(monkeypatch, text, *, model=(), rules=(), prior=None, mode="model"):
    request = AssistRequest(
        request_id="new",
        resource_id=UUID(int=179),
        form="extra",
        text=text,
        current=CURRENT,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    calls = []

    async def stub(data, context):
        calls.append(data)
        if mode == "fallback":
            raise RuntimeError("offline fixture")
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    result = asyncio.run(interpret(request, CTX, use_model=mode != "rules"))
    assert len(calls) == int(mode != "rules")
    return {field.field: field for field in result.fields}


def prior_extra():
    return PreviousCandidate(
        request_id="prior",
        text="사유는 벽체 손상 발견, 추가 금액은 51만 원으로 제안해 주세요.",
        fields=[
            change("reason", "벽체 손상 발견", "사유는 벽체 손상 발견"),
            change("amount", 510_000, "추가 금액은 51만 원"),
        ],
        baselines={"reason": CURRENT["reason"], "amount": CURRENT["amount"]},
        sources={"reason": "llm", "amount": "llm"},
        origins={"reason": "old-reason", "amount": "old-amount"},
    )


@pytest.mark.parametrize(
    "reference",
    ["현재 입력된 값대로", "지금 입력한 값으로", "현재 적힌 값대로"],
)
@pytest.mark.parametrize("truncated", [False, True])
def test_direct_current_reason_reference(monkeypatch, reference, truncated):
    clause = f"사유는 {reference}"
    text = clause + " 두겠습니다. 추가 금액은 앞서 제안한 값 그대로 유지해 주세요."
    fields = run(
        monkeypatch,
        text,
        prior=prior_extra(),
        model=[
            keep("reason", clause if truncated else clause + " 두겠습니다", "current"),
            keep("amount", "추가 금액은 앞서 제안한 값 그대로 유지", "previous"),
        ],
    )
    reason = fields["reason"]
    assert (reason.status, reason.action, reason.value_source, reason.origin_request_id) == (
        "valid",
        "keep",
        "current",
        "new",
    )
    assert reason.value is None and not reason.alternatives
    amount = fields["amount"]
    assert (amount.status, amount.value, amount.value_source, amount.origin_request_id) == (
        "valid",
        510_000,
        "previous",
        "old-amount",
    )


@pytest.mark.parametrize(
    "text",
    [
        "사유는 현재 입력된 값대로 둘까요?",
        "사유는 현재 입력된 값대로 두지 마세요.",
        "사유는 현재 입력된 값대로 두겠습니다. 사유는 아직 미정입니다.",
        "사유는 현재 입력된 값대로 두겠습니다. 사유 변경을 취소합니다.",
        "금액은 현재 입력된 값대로 두겠습니다.",
        "사유는 현재 입력된 값대로 두겠습니다만 아직 고민 중입니다.",
    ],
)
def test_current_reason_requires_positive_same_field_confirmation(monkeypatch, text):
    evidence = "현재 입력된 값대로"
    fields = run(
        monkeypatch, text, prior=prior_extra(), model=[keep("reason", evidence, "current")]
    )
    assert fields["reason"].status in ("invalid", "uncertain")
    assert fields["reason"].value is None and not fields["reason"].alternatives


@pytest.mark.parametrize(
    "field,label,value,command",
    [
        ("title", "항목명", "현관 보강", "으로 확정해 주세요"),
        ("title", "공사 제목", "문틀 보수", "로 기재하겠습니다"),
        ("reason", "사유", "기존 판재 균열", "로 입력합니다"),
        ("reason", "이유", "지지대 손상", "으로 하겠습니다"),
    ],
)
def test_assignment_command_is_not_a_selectable_text_value(
    monkeypatch, field, label, value, command
):
    text = f"{label}은 {value}{command}. 추가 비용이 45만 원까지 가능한지 확인 부탁드려요."
    fields = run(
        monkeypatch,
        text,
        rules=[change(field, value + command, text)],
        model=[change(field, value, f"{label}은 {value}{command}")],
    )
    result = fields[field]
    assert (result.status, result.action, result.value) == ("valid", "set", value), result
    assert not result.alternatives


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_unparsed_assignment_directive_is_never_actionable(monkeypatch, mode):
    text = "항목명은 현관 보강으로 확정해 주세요."
    wrong = change("title", "현관 보강으로 확정해 주세요", text)
    fields = run(monkeypatch, text, rules=[wrong], model=[wrong], mode=mode)
    assert fields["title"].status in ("invalid", "uncertain")
    assert fields["title"].value is None and not fields["title"].alternatives


def test_text_value_can_contain_ordinary_assignment_related_nouns(monkeypatch):
    text = "사유는 최종 확정 도면과 현장 불일치"
    value = "최종 확정 도면과 현장 불일치"
    fields = run(
        monkeypatch,
        text,
        rules=[change("reason", value, text)],
        model=[change("reason", value, text)],
    )
    assert (fields["reason"].status, fields["reason"].value) == ("valid", value)
