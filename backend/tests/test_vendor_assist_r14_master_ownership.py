"""Text candidates must belong to their own operation, not a later field's predicate."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_intent_safety import text_value_scope_ok
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret


@pytest.mark.parametrize(
    "field, text, value, allowed",
    [
        (
            "reason",
            "자재 수급 상황이 달라져서 추가 금액은 40만원으로 수정해 주세요.",
            "자재 수급 상황이 달라져서",
            False,
        ),
        (
            "reason",
            "작업 범위를 재검토했습니다. 금액만 40만원으로 변경해 주세요.",
            "작업 범위를 재검토했습니다",
            False,
        ),
        ("title", "현장 조건 때문에, 부가세는 별도로 변경해 주세요.", "현장 조건 때문에", False),
        (
            "reason",
            "외벽 보강에 시간이 걸려서 금액은 40만원으로 수정해 주세요.",
            "외벽 보강",
            False,
        ),
        (
            "reason",
            "사유는 자재 수급 지연, 금액은 40만원으로 수정해 주세요.",
            "자재 수급 지연",
            True,
        ),
        (
            "reason",
            "사유는 '작업 범위 재검토'로 지정해 주세요. 금액은 40만원입니다.",
            "작업 범위 재검토",
            True,
        ),
        ("title", "공사명은 외벽 보강으로, 금액은 40만원으로 지정해 주세요.", "외벽 보강", True),
        ("reason", "고객 요청으로 공정 변경", "고객 요청으로 공정 변경", True),
        ("title", "실리콘 보수 말고 누수 보수", "누수 보수", True),
    ],
)
def test_ownerless_text_cannot_borrow_a_later_field_command(field, text, value, allowed):
    assert text_value_scope_ok(field, value, text) is allowed


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text, evidence, value, field, allowed",
    [
        (
            "현장 자재가 바뀌어 추가 금액은 40만원으로 수정합니다.",
            "현장 자재가 바뀌어",
            "현장 자재가 바뀌어",
            "reason",
            False,
        ),
        (
            "작업이 지연되었습니다. 추가 금액은 40만원으로 바꿔 주세요.",
            "작업이 지연되었습니다",
            "작업이 지연되었습니다",
            "title",
            False,
        ),
        (
            "사유는 자재 수급 지연으로 수정하고 금액은 40만원으로 확정합니다.",
            "사유는 자재 수급 지연으로 수정하고",
            "자재 수급 지연",
            "reason",
            True,
        ),
        (
            "공사명은 외벽 보강으로 지정합니다. 금액은 40만원으로 수정합니다.",
            "공사명은 외벽 보강으로 지정합니다",
            "외벽 보강",
            "title",
            True,
        ),
    ],
)
def test_actual_service_text_field_ownership(
    monkeypatch, producer, text, evidence, value, field, allowed
):
    assert not settings.llm_live
    proposals = [
        FieldIntent(
            field=field,
            action="set",
            value=value,
            evidence=evidence,
            intent_kind="explicit_change",
            value_source="utterance",
        ),
        FieldIntent(
            field="amount",
            action="set",
            value=400000,
            evidence="40만원",
            intent_kind="explicit_change",
            value_source="utterance",
        ),
    ]
    calls = []

    async def stub(*args, **kwargs):
        calls.append(1)
        return ModelInterpretation(fields=proposals)

    async def forbidden(*args, **kwargs):
        raise AssertionError("external provider forbidden")

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub if producer != "rule" else forbidden)
    req = AssistRequest(
        request_id="r14-owner",
        resource_id=UUID(int=179),
        form="extra",
        text=text,
        current={"title": "초기 공사", "reason": "고객 요청", "amount": 250000, "vat": "excluded"},
        previous_candidate=None,
        rule_fields=proposals if producer in ("rule", "both") else [],
    )
    ctx = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))
    response = asyncio.run(interpret(req, ctx, use_model=producer != "rule"))
    actual = {f.field: f for f in response.fields}
    assert len(calls) == int(producer != "rule")
    assert actual["amount"].status == "valid" and actual["amount"].value == 400000
    if allowed:
        assert (
            actual[field].status == "valid"
            and actual[field].action == "set"
            and actual[field].value == value
        )
    else:
        assert actual[field].action not in ("set", "clear") and not actual[field].alternatives
