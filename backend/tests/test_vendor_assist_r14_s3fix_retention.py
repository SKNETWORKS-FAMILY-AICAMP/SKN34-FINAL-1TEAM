"""Developer controls for reported speech versus live operations."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text,evidence,field,kind,expected",
    [
        (
            "현재 금액은 그대로 두세요.",
            "현재 금액은 그대로 두세요",
            "amount",
            "retain_current",
            "keep",
        ),
        (
            "현재 금액은 그대로 둔다고 말했습니다.",
            "현재 금액은 그대로",
            "amount",
            "retain_current",
            "hold",
        ),
        (
            "아까 제안한 금액은 그대로 두겠다고 설명했습니다.",
            "아까 제안한 금액은 그대로",
            "amount",
            "retain_previous",
            "hold",
        ),
        (
            "금액은 그대로 둔다고 말했습니다. 현재 금액은 그대로 두세요.",
            "현재 금액은 그대로 두세요",
            "amount",
            "retain_current",
            "keep",
        ),
        (
            "부가세는 지금 값으로 고정한다고 전달했습니다.",
            "부가세는 지금 값으로 고정",
            "vat",
            "retain_current",
            "hold",
        ),
        (
            "아까 제안한 금액은 그대로 두세요.",
            "아까 제안한 금액은 그대로 두세요",
            "amount",
            "retain_previous",
            "carry",
        ),
    ],
)
def test_retention_is_a_present_operation(
    monkeypatch, producer, text, evidence, field, kind, expected
):
    current = {"amount": 2700000, "vat": "included", "days": 3, "valid": "2026-12-30"}
    prior = PreviousCandidate(
        request_id="seed",
        text="견적 금액은 310만원으로 수정해 주세요. 작업 기간은 6일로 변경해 주세요.",
        fields=[
            FieldIntent(field="amount", action="set", value=3100000, evidence="310만원"),
            FieldIntent(field="days", action="set", value=6, evidence="6일"),
        ],
        baselines={"amount": 2700000, "days": 3},
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "amount-origin", "days": "days-origin"},
    )
    item = FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source="current" if kind == "retain_current" else "previous",
    )
    full = [
        item
        if name == field
        else FieldIntent(
            field=name, action="keep", value=None, evidence="", intent_kind="no_mention"
        )
        for name in FORM_FIELDS["quote"]
    ]

    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=full)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    req = AssistRequest(
        request_id="followup",
        resource_id=UUID(int=179),
        form="quote",
        text=text,
        current=current,
        previous_candidate=prior if field == "amount" else None,
        rule_fields=[item] if producer != "model" else [],
    )
    result = asyncio.run(
        interpret(req, AssistContext(today=date(2026, 11, 23)), use_model=producer != "rule")
    )
    fields = {candidate.field: candidate for candidate in result.fields}
    actual = fields[field]
    if expected == "hold":
        assert actual.action == "uncertain"
        assert actual.status in ("uncertain", "invalid")
        assert not actual.alternatives
    elif expected == "keep":
        assert (actual.action, actual.status, actual.intent_kind) == (
            "keep",
            "valid",
            "retain_current",
        )
    else:
        assert (actual.action, actual.status, actual.value, actual.origin_request_id) == (
            "set",
            "valid",
            3100000,
            "amount-origin",
        )
    if field == "amount":
        assert (fields["days"].action, fields["days"].value, fields["days"].origin_request_id) == (
            "set",
            6,
            "days-origin",
        )


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text,evidence,expected",
    [
        (
            "현장 반입이 어려워서, 추가 금액은 28만원으로 수정해 주세요.",
            "현장 반입이 어려워서",
            False,
        ),
        (
            "추가 금액은 28만원으로 수정해 주세요. 현장 반입이 어려워서요.",
            "현장 반입이 어려워서",
            False,
        ),
        ("현장 반입이 어려워서요.", "현장 반입이 어려워서", False),
        (
            "사유는 '현장 반입이 어려워서'로 적어 주세요. 추가 금액은 28만원으로 수정해 주세요.",
            "현장 반입이 어려워서",
            True,
        ),
    ],
)
def test_causal_description_requires_ownership(monkeypatch, producer, text, evidence, expected):
    items = [
        FieldIntent(
            field="reason",
            action="set",
            value=evidence,
            evidence=evidence,
            intent_kind="explicit_change",
            value_source="utterance",
        )
    ]
    if "28만원" in text:
        items.append(
            FieldIntent(
                field="amount",
                action="set",
                value=280000,
                evidence="28만원",
                intent_kind="explicit_change",
                value_source="utterance",
            )
        )
    full = [
        next(
            (f for f in items if f.field == name),
            FieldIntent(
                field=name, action="keep", value=None, evidence="", intent_kind="no_mention"
            ),
        )
        for name in FORM_FIELDS["extra"]
    ]

    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=full)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    req = AssistRequest(
        request_id="new",
        resource_id=UUID(int=179),
        form="extra",
        text=text,
        current={"title": "몰딩 정리", "reason": "기존 요청", "amount": 180000, "vat": "included"},
        previous_candidate=None,
        rule_fields=items if producer != "model" else [],
    )
    result = asyncio.run(
        interpret(req, AssistContext(today=date(2026, 11, 23)), use_model=producer != "rule")
    )
    fields = {candidate.field: candidate for candidate in result.fields}
    if "28만원" in text:
        assert (fields["amount"].action, fields["amount"].value) == ("set", 280000)
    if expected:
        assert (fields["reason"].action, fields["reason"].status, fields["reason"].value) == (
            "set",
            "valid",
            evidence,
        )
    else:
        assert fields["reason"].action not in ("set", "clear") and not fields["reason"].alternatives
