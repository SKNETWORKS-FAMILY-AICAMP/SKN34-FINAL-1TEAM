"""Independent semantic contrasts through the actual offline service."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))
CURRENT = {
    "dates": {"days": ["2026-12-03", "2026-12-17"], "crew": 2},
    "quote": {"amount": 4500000, "vat": "included", "days": 5, "valid": "2026-12-31"},
    "period": {"start": "2026-12-01", "end": "2026-12-31", "excluded": []},
}


def run(monkeypatch, form, text, proposal, producer, *, prior=None, current=None):
    assert not settings.llm_live
    req = AssistRequest(
        request_id="r13-quality",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current if current is not None else CURRENT[form],
        previous_candidate=prior,
        rule_fields=[proposal] if producer in ("rule", "both") else [],
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("external provider forbidden")

    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=[proposal])

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", forbidden if producer == "rule" else stub)
    response = asyncio.run(interpret(req, CTX, use_model=producer != "rule"))
    assert response.source == ("rule" if producer == "rule" else "llm")
    return next(item for item in response.fields if item.field == proposal.field)


DAYS = ["2026-12-08", "2026-12-20"]
DAY_LIST = "12월 8일, 20일"
DATE_PREVIEW = "작업 날짜를 12월 8일과 20일로 변경하는 초안을"
CREW_PREVIEW = "작업 인원을 5명으로 바꿀 경우의 후보안을"


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,text,value,evidence,accepted",
    [
        # Full list ownership and exact complete value.
        ("dates", "days", f"공사 날짜를 {DAY_LIST}로 옮겨 주세요.", DAYS, DAY_LIST, True),
        ("dates", "days", f"공사 날짜를 {DAY_LIST}로 옮겨 주세요.", DAYS[1:], "20일", False),
        (
            "dates",
            "days",
            "공사 날짜를 12월 8일에서 20일로 옮겨 주세요.",
            DAYS,
            "12월 8일에서 20일",
            False,
        ),
        ("dates", "days", f"허가되면 공사 날짜를 {DAY_LIST}로 옮겨 주세요.", DAYS, DAY_LIST, False),
        ("dates", "days", f"공사 날짜를 {DAY_LIST}로 옮기지 마세요.", DAYS, DAY_LIST, False),
        (
            "dates",
            "days",
            f"공사 날짜를 {DAY_LIST}로 옮겨 주세요. 취소할게요.",
            DAYS,
            DAY_LIST,
            False,
        ),
        (
            "dates",
            "days",
            f"공사 날짜를 {DAY_LIST}로 옮겨 주세요. 작업일은 12월 22일로 변경해 주세요.",
            DAYS,
            DAY_LIST,
            False,
        ),
        # Proposal object is not an external hypothetical execution condition.
        ("dates", "days", f"{DATE_PREVIEW} 먼저 보여 주시겠어요?", DAYS, "12월 8일과 20일", True),
        (
            "dates",
            "days",
            f"허가가 나면 {DATE_PREVIEW} 보여 주세요.",
            DAYS,
            "12월 8일과 20일",
            False,
        ),
        ("dates", "days", f"{DATE_PREVIEW} 만들어도 되나요?", DAYS, "12월 8일과 20일", False),
        (
            "dates",
            "crew",
            f"{CREW_PREVIEW} 작성해 주세요.",
            5,
            "작업 인원을 5명으로 바꿀 경우의 후보안",
            True,
        ),
        (
            "dates",
            "crew",
            f"승인되면 {CREW_PREVIEW} 작성해 주세요.",
            5,
            "작업 인원을 5명으로 바꿀 경우의 후보안",
            False,
        ),
        (
            "dates",
            "crew",
            f"{CREW_PREVIEW} 작성하지 말아 주세요.",
            5,
            "작업 인원을 5명으로 바꿀 경우의 후보안",
            False,
        ),
        # Explicit final total is different from the increment.
        (
            "dates",
            "crew",
            "보조 인력을 두 명 추가해서 총 4명으로 수정해 주세요.",
            4,
            "총 4명으로 수정해 주세요",
            True,
        ),
        (
            "dates",
            "crew",
            "보조 인력을 두 명 추가해서 총 5명으로 수정해 주세요.",
            5,
            "총 5명으로 수정해 주세요",
            False,
        ),
        (
            "dates",
            "crew",
            "보조 인력을 두 명 추가해서 총 4명으로 수정해 주세요.",
            2,
            "두 명",
            False,
        ),
        (
            "dates",
            "crew",
            "승인되면 보조 인력을 두 명 추가해서 총 4명으로 수정해 주세요.",
            4,
            "총 4명으로 수정해 주세요",
            False,
        ),
        (
            "dates",
            "crew",
            "보조 인력을 두 명 추가해서 총 4명으로 수정하지 마세요.",
            4,
            "총 4명",
            False,
        ),
        # Noun+particle does not hide an actual approximate date.
        (
            "dates",
            "days",
            "일정도 협의 끝났어요. 12월 8일과 20일에 작업하도록 변경해 주세요.",
            DAYS,
            "12월 8일과 20일",
            True,
        ),
        (
            "dates",
            "days",
            "일정도 협의 끝났어요. 12월 8일과 20일 정도로 변경해 주세요.",
            DAYS,
            "12월 8일과 20일",
            False,
        ),
        # Generic quote noun vs explicit validity predicate.
        (
            "quote",
            "valid",
            "이 견적은 2027년 1월 15일까지 유효하도록 수정해 주세요.",
            "2027-01-15",
            "2027년 1월 15일까지 유효",
            True,
        ),
        (
            "quote",
            "valid",
            "이 견적은 2027년 1월 15일까지 유효하도록 수정하지 마세요.",
            "2027-01-15",
            "2027년 1월 15일까지 유효",
            False,
        ),
        (
            "quote",
            "valid",
            "공사 기간은 2027년 1월 15일까지 유효하도록 수정해 주세요.",
            "2027-01-15",
            "2027년 1월 15일까지 유효",
            False,
        ),
    ],
)
def test_owned_operation_contrasts(
    monkeypatch, producer, form, field, text, value, evidence, accepted
):
    proposal = FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )
    result = run(monkeypatch, form, text, proposal, producer)
    if accepted:
        assert (result.action, result.status, result.value) == ("set", "valid", value), result
    else:
        assert not (result.status == "valid" and result.action == "set"), result
        assert not result.alternatives, result


def prior_vat():
    return PreviousCandidate(
        request_id="seed",
        text="부가세는 별도로 확정해 주세요.",
        fields=[
            FieldIntent(
                field="vat",
                action="set",
                value="excluded",
                evidence="부가세는 별도로 확정해 주세요",
                intent_kind="explicit_change",
                value_source="utterance",
            )
        ],
        origins={"vat": "original-seed"},
        sources={"vat": "llm"},
        baselines={"vat": "included"},
    )


@pytest.mark.parametrize(
    "text,stale,accepted",
    [
        ("추가 질문은 다음에 드릴게요.", False, True),
        ("다른 상담은 추후에 할게요.", False, True),
        ("추가 질문은 다음에 드릴게요.", True, False),
        ("추가 질문은 다음에 드릴게요. 부가세는 미정입니다.", False, False),
        ("다른 질문은 나중에 드릴게요. 그 제안은 보류할게요.", False, False),
        ("부가세 변경은 나중에 할게요.", False, False),
        ("다른 질문은 나중에 드릴게요. 아까 부가세는 그대로 둘까요?", False, False),
    ],
)
def test_discussion_deferral_does_not_override_prior_controls(monkeypatch, text, stale, accepted):
    proposal = FieldIntent(
        field="vat",
        action="keep",
        value=None,
        evidence="",
        intent_kind="no_mention",
    )
    current = {**CURRENT["quote"], "vat": "exempt"} if stale else CURRENT["quote"]
    actual = run(monkeypatch, "quote", text, proposal, "model", prior=prior_vat(), current=current)
    if accepted:
        assert (
            actual.status,
            actual.action,
            actual.value,
            actual.intent_kind,
            actual.origin_request_id,
        ) == (
            "valid",
            "set",
            "excluded",
            "retain_previous",
            "original-seed",
        )
    else:
        assert not (actual.status == "valid" and actual.action == "set"), actual


def test_increment_cannot_select_ambiguous_prior_base(monkeypatch):
    old = FieldIntent(field="crew", action="set", value=3, evidence="인원을 3명으로 수정해 주세요")
    prior = PreviousCandidate(
        request_id="seed",
        text=old.evidence,
        fields=[old],
        baselines={"crew": 2},
    )
    proposal = FieldIntent(
        field="crew",
        action="set",
        value=4,
        evidence="총 4명으로 수정해 주세요",
        intent_kind="explicit_change",
        value_source="utterance",
    )
    actual = run(
        monkeypatch,
        "dates",
        "보조 인력을 두 명 추가해서 총 4명으로 수정해 주세요.",
        proposal,
        "both",
        prior=prior,
    )
    assert not (actual.status == "valid" and actual.action == "set" and actual.value == 4)
