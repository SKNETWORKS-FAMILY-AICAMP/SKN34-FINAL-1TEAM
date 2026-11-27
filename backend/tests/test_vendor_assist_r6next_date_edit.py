"""확정된 동일 목록 정정과 의향만 밝힌 요청을 구분한다."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_date_edit import (
    date_edit_confirmation_start,
    date_replacement_requested,
    validated_date_edit,
)
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

PERIOD_TEXT = "앞서 제외하기로 한 20일을 21일로 옮겨서 목록을 수정하겠습니다."
DAYS_TEXT = "앞서 제안한 공사일 중 5일 대신 8일로 바꾸려고 합니다. 그렇게 변경해 주세요."
PERIOD_PRIOR = ["2026-12-09", "2026-12-20"]
DAYS_PRIOR = ["2026-12-05", "2026-12-23"]
PERIOD_UPDATED = ["2026-12-09", "2026-12-21"]
DAYS_UPDATED = ["2026-12-08", "2026-12-23"]


@pytest.fixture
def ctx():
    return AssistContext(
        today=date(2026, 11, 25),
        base_month=date(2026, 12, 1),
        required_days=2,
    )


@pytest.mark.parametrize(
    "field,text,prior,updated",
    [
        ("excluded", PERIOD_TEXT, PERIOD_PRIOR, PERIOD_UPDATED),
        (
            "excluded",
            "이전에 빼기로 정한 20일만 21일로 바꿔서 제외일 목록을 변경해 주세요.",
            PERIOD_PRIOR,
            PERIOD_UPDATED,
        ),
        (
            "excluded",
            "아까 쉬기로 한 20일 대신 21일로 옮기고 해당 목록을 정정합니다.",
            PERIOD_PRIOR,
            PERIOD_UPDATED,
        ),
        ("days", DAYS_TEXT, DAYS_PRIOR, DAYS_UPDATED),
        (
            "days",
            "아까 정한 작업일 5일을 8일로 옮기려 합니다. 이대로 수정해 주세요.",
            DAYS_PRIOR,
            DAYS_UPDATED,
        ),
        (
            "days",
            "앞서 제시한 시공일 5일 대신 8일로 바꿔서 작업일 목록을 변경하겠습니다.",
            DAYS_PRIOR,
            DAYS_UPDATED,
        ),
        (
            "excluded",
            "앞서 정한 제외일 20일 대신 21일로 변경하려고 합니다. 그렇게 수정해 주세요.",
            PERIOD_PRIOR,
            PERIOD_UPDATED,
        ),
    ],
)
def test_same_list_confirmed_replacement(ctx, field, text, prior, updated):
    assert date_replacement_requested(field, text)
    assert date_edit_confirmation_start(field, text) is not None
    assert validated_date_edit(field, text, prior, ctx, require_affirmative=True) == updated


@pytest.mark.parametrize(
    "field,text,prior",
    [
        ("excluded", PERIOD_TEXT.replace("목록을", "작업일 목록을"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("목록을", "종료일 목록을"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("목록을", "인원 목록을"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("수정하겠습니다", "수정할까요"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("수정하겠습니다", "수정하지 않겠습니다"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT + " 취소합니다.", PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT + " 23일도 추가하겠습니다.", PERIOD_PRIOR),
        ("excluded", "만약 가능하다면 " + PERIOD_TEXT, PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("21일", "9일"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("20일", "19일"), PERIOD_PRIOR),
        ("excluded", PERIOD_TEXT.replace("제외하기로 한", "시작하기로 한"), PERIOD_PRIOR),
        ("days", DAYS_TEXT.split(".")[0] + ".", DAYS_PRIOR),
        ("days", DAYS_TEXT.replace("그렇게 변경해 주세요", "인원을 변경해 주세요"), DAYS_PRIOR),
        ("days", DAYS_TEXT.replace("그렇게 변경해 주세요", "그렇게 변경할까요"), DAYS_PRIOR),
        ("days", DAYS_TEXT.replace("그렇게 변경해 주세요", "그렇게 변경하지 마세요"), DAYS_PRIOR),
        ("days", DAYS_TEXT + " 취소합니다.", DAYS_PRIOR),
        ("days", DAYS_TEXT + " 인원도 변경해 주세요.", DAYS_PRIOR),
        ("days", DAYS_TEXT.replace("8일", "8일이나 10일"), DAYS_PRIOR),
        ("days", "조건이 맞으면 " + DAYS_TEXT, DAYS_PRIOR),
        ("days", DAYS_TEXT.replace("공사일", "제외일"), DAYS_PRIOR),
    ],
)
def test_new_confirmation_shapes_hold_for_wrong_scope_or_uncertainty(ctx, field, text, prior):
    assert validated_date_edit(field, text, prior, ctx, require_affirmative=True) is None
    # 기존 후보를 모델이 확신해도 의향만으로 적용할 수 없어야 한다.
    assert validated_date_edit(field, text, prior, ctx) is None


def _data(form, *, stale=False, text=None):
    if form == "period":
        field = "excluded"
        current = {"start": "2026-12-02", "end": "2026-12-24", "excluded": []}
        prior_text = "실측 못 하는 날을 12월 9일과 20일로 후보에 넣어주세요."
        prior_value = PERIOD_PRIOR
        evidence = prior_text.rstrip(".")
        source = "utterance"
        text = text or PERIOD_TEXT
    else:
        field = "days"
        current = {"days": ["2026-12-09", "2026-12-16"], "crew": 2}
        prior_text = "시공 일정 후보를 12월 5일과 23일로 잡아주세요."
        prior_value = DAYS_PRIOR
        evidence = "12월 5일과 23일"
        source = "derived"
        text = text or DAYS_TEXT
    previous = PreviousCandidate(
        request_id="previous",
        text=prior_text,
        fields=[
            FieldIntent(
                field=field,
                action="set",
                value=prior_value,
                evidence=evidence,
                intent_kind="explicit_change",
                value_source=source,
            )
        ],
        sources={field: "llm"},
        origins={field: "previous"},
        baselines={field: ["2026-12-02"] if stale else current[field]},
    )
    return AssistRequest(
        request_id="followup",
        form=form,
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=[],
    ), field


def _interpret(monkeypatch, data, field, ctx, use_model, *, malicious=False):
    async def fake_model(_data, _ctx):
        if field == "excluded" or malicious:
            model_field = FieldIntent(
                field=field,
                action="set",
                value=PERIOD_UPDATED if field == "excluded" else DAYS_UPDATED,
                evidence=data.text.rstrip("."),
                intent_kind="explicit_change",
                value_source="derived",
            )
        else:
            model_field = FieldIntent(
                field=field,
                action="uncertain",
                evidence=data.text.split(".")[0],
                intent_kind="ambiguous",
            )
        return ModelInterpretation(fields=[model_field])

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    trace = {}
    result = asyncio.run(interpret(data, ctx, use_model=use_model, trace=trace))
    return {item.field: item for item in result.fields}, trace


@pytest.mark.parametrize("form", ["period", "dates"])
@pytest.mark.parametrize("use_model", [False, True])
def test_executed_turn_rebuilt_from_verified_prior(monkeypatch, ctx, form, use_model):
    data, field = _data(form)
    result, trace = _interpret(monkeypatch, data, field, ctx, use_model)
    assert trace["previous_verified"] is not None, trace
    assert trace["date_edit_reviewed"], trace
    candidate = result[field]
    assert (candidate.status, candidate.action, candidate.value) == (
        "valid",
        "set",
        PERIOD_UPDATED if form == "period" else DAYS_UPDATED,
    ), trace
    assert candidate.correction.rejected_value == (PERIOD_PRIOR if form == "period" else DAYS_PRIOR)
    assert candidate.origin_request_id == "followup"
    assert all(item.action == "keep" for name, item in result.items() if name != field)


@pytest.mark.parametrize("form", ["period", "dates"])
@pytest.mark.parametrize("use_model", [False, True])
def test_stale_prior_cannot_confirm_even_with_model_agreement(monkeypatch, ctx, form, use_model):
    data, field = _data(form, stale=True)
    result, trace = _interpret(monkeypatch, data, field, ctx, use_model, malicious=True)
    assert trace["previous_rejected"].get(field) == "stale_context", trace
    assert not trace["date_edit_reviewed"]
    assert result[field].status != "valid"
    assert not result[field].alternatives


@pytest.mark.parametrize(
    "form,text",
    [
        ("period", PERIOD_TEXT.replace("목록을", "종료일 목록을")),
        ("period", PERIOD_TEXT + " 취소합니다."),
        ("dates", DAYS_TEXT.split(".")[0] + "."),
        ("dates", DAYS_TEXT + " 취소합니다."),
        ("dates", DAYS_TEXT.replace("그렇게 변경해 주세요", "인원을 변경해 주세요")),
    ],
)
@pytest.mark.parametrize("use_model", [False, True])
def test_unconfirmed_replacement_cannot_be_plain_list(monkeypatch, ctx, form, text, use_model):
    data, field = _data(form, text=text)
    result, trace = _interpret(monkeypatch, data, field, ctx, use_model, malicious=True)
    assert not trace["date_edit_reviewed"], trace
    assert result[field].status != "valid", trace
    assert not result[field].alternatives
