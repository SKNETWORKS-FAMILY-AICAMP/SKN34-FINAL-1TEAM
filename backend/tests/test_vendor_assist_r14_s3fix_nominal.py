"""Developer controls for independently bounded text items and candidate continuity."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_intent_safety import text_value_scope_ok
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret


@pytest.mark.parametrize(
    "text,value,field,expected",
    [
        ("현관 바닥 보수, 추가 금액 18만원, 부가세 별도", "현관 바닥 보수", "title", True),
        ("외벽 균열 보강，금액 24만원", "외벽 균열 보강", "title", True),
        ("창틀 단열 보완; 금액 31만원", "창틀 단열 보완", "title", True),
        ("'배관 점검, 보수', 금액 31만원", "배관 점검, 보수", "title", True),
        ("현장 사정 때문에, 금액만 24만원으로 변경", "현장 사정 때문에", "title", False),
        ("자재가 부족해서, 추가 금액은 31만원으로 변경", "자재가 부족해서", "title", False),
        ("현장 확인이 늦어져, 금액은 18만원으로 변경", "현장 확인이 늦어져", "title", False),
        ("작업 범위를 확인했습니다, 금액은 18만원", "작업 범위를 확인했습니다", "title", False),
        ("현관 바닥 보수, 금액은 18만원", "바닥 보수", "title", False),
        ("현관 바닥 보수, 금액은 18만원", "현관 바닥 보수", "reason", False),
        ("사유는 자재 지연, 금액은 18만원", "자재 지연", "reason", True),
        ("공사명은 현장 사정 때문에, 금액은 18만원", "현장 사정 때문에", "title", True),
        ("공사명은 현관 바닥 보수, 사유는 자재 지연", "자재 지연", "title", False),
    ],
)
def test_complete_nominal_item_scope(text, value, field, expected):
    assert text_value_scope_ok(field, value, text) is expected


def test_complete_standalone_reason_shorthand_remains_available():
    value = "현장 확인 중 틈이 벌어져서"
    assert text_value_scope_ok("reason", value, value)


@pytest.mark.parametrize("stale,foreign", [(False, False), (True, False), (False, True)])
def test_independent_amount_change_preserves_only_verified_prior(monkeypatch, stale, foreign):
    title = "현장 사정 때문에" if foreign else "현관 바닥 보수"
    old = PreviousCandidate(
        request_id="prior",
        text=f"{title}, 추가 금액 18만원, 부가세 별도",
        fields=[
            FieldIntent(field="title", action="set", value=title, evidence=title),
            FieldIntent(field="amount", action="set", value=180000, evidence="18만원"),
            FieldIntent(field="vat", action="set", value="excluded", evidence="부가세 별도"),
        ],
        baselines={"title": None, "amount": None, "vat": None},
        sources={"title": "rule", "amount": "llm", "vat": "rule"},
        origins={"title": "title-origin", "amount": "amount-origin", "vat": "vat-origin"},
    )
    text = "추가 금액은 21만원으로 변경해 주세요."
    request = AssistRequest(
        request_id="new",
        resource_id=UUID(int=179),
        form="extra",
        text=text,
        current={"title": "수동 변경" if stale else None, "amount": None, "vat": None},
        previous_candidate=old,
        rule_fields=[],
    )

    async def stub(*args, **kwargs):
        return ModelInterpretation(
            fields=[
                FieldIntent(
                    field="amount",
                    action="set",
                    value=210000,
                    evidence="21만원",
                    intent_kind="explicit_change",
                    value_source="utterance",
                ),
                FieldIntent(
                    field="title", action="keep", value=None, evidence="", intent_kind="no_mention"
                ),
                FieldIntent(
                    field="vat", action="keep", value=None, evidence="", intent_kind="no_mention"
                ),
            ],
        )

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    result = asyncio.run(interpret(request, AssistContext(today=date(2026, 11, 26))))
    fields = {item.field: item for item in result.fields}
    assert (fields["amount"].action, fields["amount"].value) == ("set", 210000)
    assert (fields["vat"].action, fields["vat"].value, fields["vat"].origin_request_id) == (
        "set",
        "excluded",
        "vat-origin",
    )
    if stale or foreign:
        assert fields["title"].action not in ("set", "clear")
        assert not fields["title"].alternatives
    else:
        assert (fields["title"].action, fields["title"].value, fields["title"].source) == (
            "set",
            title,
            "rule",
        )
        assert fields["title"].origin_request_id == "title-origin"
