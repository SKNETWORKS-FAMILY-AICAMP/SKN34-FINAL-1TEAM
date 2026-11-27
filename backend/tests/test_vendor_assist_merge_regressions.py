"""#179 규칙·LLM 후보 병합의 적용 안전성과 정정 원문 근거 회귀 시험.

LLM은 항상 결정적 스텁으로 대체한다. 보류는 keep/uncertain/invalid 중 무엇이든 허용하지만
사용자가 확인하지 않은 실제 set/clear가 valid가 되는 것은 금지한다.
"""

import asyncio
import uuid
from datetime import date

import pytest

from app.agent import vendor_assist
from app.vendor import assist_service
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_validation import merge_candidates, review_fields


def ctx() -> AssistContext:
    return AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))


def intent(field: str, action: str, value, evidence: str, **options) -> FieldIntent:
    return FieldIntent(field=field, action=action, value=value, evidence=evidence, **options)


def req(form: str, text: str, *, current=None, rules=None, previous=None) -> AssistRequest:
    return AssistRequest(
        request_id="new-request",
        resource_id=uuid.uuid4(),
        form=form,
        text=text,
        current=current or {},
        rule_fields=rules or [],
        previous_candidate=previous,
    )


def modeled(monkeypatch, fields: list[FieldIntent]):
    calls = []

    async def fake(*_args, **_kwargs):
        calls.append(1)
        return ModelInterpretation(fields=fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake)
    return calls


def send(data: AssistRequest):
    return {
        field.field: field for field in asyncio.run(assist_service.interpret(data, ctx())).fields
    }


def assert_not_actionable(field):
    assert not (field.status == "valid" and field.action in ("set", "clear")), (
        f"unconfirmed change became actionable: {field.field}={field.value!r} "
        f"({field.source}/{field.status})"
    )


def previous_quote() -> PreviousCandidate:
    return PreviousCandidate(
        request_id="prior-quote",
        text="견적 500만원, 공사 3일",
        fields=[intent("amount", "set", 5_000_000, "500만원"), intent("days", "set", 3, "3일")],
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "origin-first", "days": "origin-second"},
        baselines={"amount": None, "days": None},
    )


def previous_period() -> PreviousCandidate:
    return PreviousCandidate(
        request_id="prior-period",
        text="12월 7일부터 12월 10일까지 가능",
        fields=[
            intent("start", "set", "2026-12-07", "12월 7일"),
            intent("end", "set", "2026-12-10", "12월 10일"),
        ],
        sources={"start": "llm", "end": "rule"},
        origins={"start": "origin-period-start", "end": "origin-period-end"},
        baselines={"start": None, "end": None},
    )


def reviewed_merge(data, rules, model):
    checked_rules, _ = review_fields(rules, "rule", data, ctx())
    checked_model, _ = review_fields(model, "llm", data, ctx())
    return {
        field.field: field
        for field in merge_candidates(data, ctx(), checked_rules, checked_model, None)
    }


@pytest.mark.parametrize(
    "text",
    [
        "추가 사유 칸을 비워 주세요.",
        "승인 후에 추가 금액을 청구해도 될까요?",
        "이유에는 벽면에 습기가 생겼다고 적어 주세요.",
        "부가세는 포함이 맞습니다. 항목은 건드리지 마세요.",
        "사유는 벽 안쪽에 곰팡이가 생겨서입니다.",
    ],
)
def test_no_mention_must_not_promote_unrelated_extra_title(monkeypatch, text):
    # 부정확한 파서가 질의/사유 문장 전체를 title로 반환하는 상황.
    data = req("extra", text, rules=[intent("title", "set", text, text)])
    calls = modeled(monkeypatch, [intent("title", "keep", None, "", intent_kind="no_mention")])
    result = send(data)
    assert calls == [1]
    assert_not_actionable(result["title"])


@pytest.mark.parametrize(
    ("text", "field", "value", "current"),
    [
        (
            "실측 시작일만 12월 3일로 입력해 주세요",
            "end",
            "2026-12-03",
            {"start": "2026-12-02", "end": "2026-12-06", "excluded": []},
        ),
        (
            "종료일만 12월 11일로 늘려 주세요",
            "start",
            "2026-12-11",
            {"start": "2026-12-07", "end": "2026-12-10", "excluded": []},
        ),
        (
            "9일은 못 가는 건 아니에요",
            "excluded",
            ["2026-12-09"],
            {"start": "2026-12-07", "end": "2026-12-10", "excluded": []},
        ),
        (
            "9일은 안 되는 건 아닙니다",
            "excluded",
            ["2026-12-09"],
            {"start": "2026-12-07", "end": "2026-12-10", "excluded": []},
        ),
    ],
)
def test_no_mention_cannot_promote_wrong_period_field(monkeypatch, text, field, value, current):
    data = req("period", text, current=current, rules=[intent(field, "set", value, text)])
    modeled(monkeypatch, [intent(field, "keep", None, "", intent_kind="no_mention")])
    assert_not_actionable(send(data)[field])


@pytest.mark.parametrize(
    ("text", "old_evidence", "new_evidence", "old_amount", "new_amount"),
    [
        (
            "455만 원은 아니고 555만 원이에요",
            "455만 원은 아니고",
            "555만 원이에요",
            4_550_000,
            5_550_000,
        ),
        ("320만원 말고 420만원으로 고쳐요", "320만원 말고", "420만원으로", 3_200_000, 4_200_000),
    ],
)
def test_agreeing_new_amount_is_not_erased_by_link_inside_evidence(
    monkeypatch, text, old_evidence, new_evidence, old_amount, new_amount
):
    data = req("quote", text, rules=[intent("amount", "set", new_amount, new_evidence)])
    modeled(
        monkeypatch,
        [
            intent(
                "amount",
                "set",
                new_amount,
                new_evidence,
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=old_amount,
                    rejected_evidence=old_evidence,
                    accepted_evidence=new_evidence,
                ),
            )
        ],
    )
    amount = send(data)["amount"]
    assert (amount.status, amount.action, amount.value) == ("valid", "set", new_amount)


@pytest.mark.parametrize(
    ("text", "field", "old_value", "new_value", "rejected", "accepted"),
    [
        (
            "면세는 아니고 부가세 포함이에요",
            "vat",
            "exempt",
            "included",
            "면세는 아니고",
            "부가세 포함이에요",
        ),
        (
            "공사 3일은 아니고 5일 걸려요",
            "days",
            3,
            5,
            "3일은 아니고",
            "5일 걸려요",
        ),
    ],
)
def test_agreeing_vat_and_duration_changes_survive_optional_link_disagreement(
    monkeypatch, text, field, old_value, new_value, rejected, accepted
):
    data = req("quote", text, rules=[intent(field, "set", new_value, accepted)])
    modeled(
        monkeypatch,
        [
            intent(
                field,
                "set",
                new_value,
                accepted,
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=old_value,
                    rejected_evidence=rejected,
                    accepted_evidence=accepted,
                ),
            ),
        ],
    )
    selected = send(data)[field]
    assert (selected.status, selected.action, selected.value) == ("valid", "set", new_value)


@pytest.mark.parametrize(
    ("form", "text", "field", "value", "current"),
    [
        ("extra", "추가 항목은 곰팡이 방지 처리로 적어 주세요", "title", "곰팡이 방지 처리", {}),
        ("extra", "항목을 누수 보수로 변경해 주세요", "title", "누수 보수", {}),
        ("quote", "견적 금액은 480만원으로 정했습니다", "amount", 4_800_000, {}),
        ("quote", "부가세 포함으로 입력해 주세요", "vat", "included", {}),
        (
            "period",
            "시작일만 12월 3일로 옮겨 주세요",
            "start",
            "2026-12-03",
            {"start": "2026-12-02", "end": "2026-12-08", "excluded": []},
        ),
        ("dates", "작업자 세 명 투입하겠습니다", "crew", 3, {}),
    ],
)
def test_independently_targeted_rule_change_survives_model_no_mention(
    monkeypatch, form, text, field, value, current
):
    # False-positive 방어가 정상적인 규칙만의 제안을 일괄 차단해서는 안 된다.
    evidence = "480만원" if field == "amount" else "세 명" if field == "crew" else text
    data = req(form, text, current=current, rules=[intent(field, "set", value, evidence)])
    modeled(monkeypatch, [intent(field, "keep", None, "", intent_kind="no_mention")])
    result = send(data)[field]
    assert (result.status, result.action, result.value) == ("valid", "set", value)


def test_invalid_rule_does_not_block_unrelated_verified_previous_field(monkeypatch):
    text = "공사 기간만 4일로 바꿔 주세요"
    data = req(
        "quote",
        text,
        current={"amount": None, "days": None},
        previous=previous_quote(),
        rules=[
            intent("amount", "set", 9_000_000, "4일"),  # 정수이지만 금액 근거가 아닌 규칙 후보
            intent("days", "set", 4, "4일"),
        ],
    )
    modeled(
        monkeypatch,
        [
            intent("amount", "keep", None, "", intent_kind="no_mention"),
            intent("days", "set", 4, "4일"),
        ],
    )
    result = send(data)
    amount, days = result["amount"], result["days"]
    assert (amount.status, amount.action, amount.value) == ("valid", "set", 5_000_000)
    assert (amount.source, amount.origin_request_id) == ("llm", "origin-first")
    assert (days.status, days.value) == ("valid", 4)


def test_invalid_rule_cannot_drop_verified_prior_start_and_valid_new_end(monkeypatch):
    # 새 종료일 11일이 이전 종료일 10일보다 뒤이므로 시작일을 11일로 변경하면 현재 조합은 invalid.
    # 모델이 start=no_mention이라면 이전 7일 시작을 유지해 종료일 11일을 검증할 수 있어야 한다.
    text = "종료일만 12월 11일로 늘려 주세요"
    data = req(
        "period",
        text,
        current={"start": None, "end": None, "excluded": []},
        previous=previous_period(),
        rules=[intent("start", "set", "2026-12-11", "12월 11일")],
    )
    modeled(
        monkeypatch,
        [
            intent("start", "keep", None, "", intent_kind="no_mention"),
            intent("end", "set", "2026-12-11", "12월 11일"),
        ],
    )
    result = send(data)
    assert (result["start"].status, result["start"].value) == ("valid", "2026-12-07")
    assert (result["start"].source, result["start"].origin_request_id) == (
        "llm",
        "origin-period-start",
    )
    assert (result["end"].status, result["end"].value) == ("valid", "2026-12-11")


def test_explicit_keep_previous_retains_origin_while_other_field_changes(monkeypatch):
    text = "금액 그대로 두고 공사 기간만 4일로 바꿔 주세요"
    data = req("quote", text, current={"amount": None, "days": None}, previous=previous_quote())
    modeled(
        monkeypatch,
        [
            intent(
                "amount",
                "keep",
                None,
                "금액 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            intent("days", "set", 4, "4일", intent_kind="explicit_change"),
        ],
    )
    result = send(data)
    assert (result["amount"].value, result["amount"].status) == (5_000_000, "valid")
    assert (result["amount"].source, result["amount"].origin_request_id) == ("llm", "origin-first")
    assert (result["days"].value, result["days"].status) == (4, "valid")


@pytest.mark.parametrize(
    ("form", "text", "field", "old", "new", "old_span", "new_span"),
    [
        (
            "quote",
            "455만원은 아니고 555만원이에요",
            "amount",
            4_550_000,
            5_550_000,
            "455만원은 아니고",
            "555만원이에요",
        ),
        (
            "quote",
            "455만원 말고 555만원으로 수정",
            "amount",
            4_550_000,
            5_550_000,
            "455만원",
            "말고 555만원으로",
        ),
        (
            "quote",
            "320만원 대신 420만원으로 정정",
            "amount",
            3_200_000,
            4_200_000,
            "320만원",
            "420만원으로",
        ),
        (
            "quote",
            "320만원에서 420만원으로 바꿉니다",
            "amount",
            3_200_000,
            4_200_000,
            "320만원",
            "420만원으로",
        ),
        (
            "extra",
            "실리콘 보수 말고 누수 보수로 바꿔요",
            "title",
            "실리콘 보수",
            "누수 보수",
            "실리콘 보수 말고",
            "누수 보수로",
        ),
        (
            "quote",
            "면세는 아니고 부가세 포함이에요",
            "vat",
            "exempt",
            "included",
            "면세는 아니고",
            "부가세 포함이에요",
        ),
    ],
)
def test_verifiable_replacements_discard_rejected_rule_value(
    form, text, field, old, new, old_span, new_span
):
    # 연결어가 old/new 근거 일부에 포함돼도 원문에서 검증 가능한 방향만 채택한다.
    data = req(form, text)
    merged = reviewed_merge(
        data,
        [intent(field, "set", old, old_span)],
        [
            intent(
                field,
                "set",
                new,
                new_span,
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=old,
                    rejected_evidence=old_span,
                    accepted_evidence=new_span,
                ),
            )
        ],
    )
    result = merged[field]
    assert (result.status, result.action, result.value) == ("valid", "set", new)
    assert result.alternatives == []


@pytest.mark.parametrize(
    "text",
    [
        "455만원 말고 555만원, 다시 455만원이라고 하네요",
        "455만원 말고 555만원, 어쩌면 555만원이 아닐 수도 있어요",
        "555만원 말고 455만원으로 정정",
        "455만원 또는 555만원, 아직 미정",
        "455만원에서 555만원 사이를 생각 중",
        "455만원, 부가세 별도 말고 포함, 555만원",
        "455만원, 철거는 포함 말고 별도, 555만원",
        "455만원 말고 555만원 아니고 655만원",
    ],
)
def test_spurious_reversed_repeated_range_correction_never_auto_applies(text):
    old, new = 4_550_000, 5_550_000
    data = req("quote", text)
    merged = reviewed_merge(
        data,
        [intent("amount", "set", old, "455만원")],
        [
            intent(
                "amount",
                "set",
                new,
                "555만원",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=old,
                    rejected_evidence="455만원",
                    accepted_evidence="555만원",
                ),
            )
        ],
    )
    assert_not_actionable(merged["amount"])


@pytest.mark.parametrize(
    ("text", "candidate", "model_action"),
    [
        ("견적은 600만원쯤 될까요?", 6_000_000, "uncertain"),
        ("금액이 600만원 또는 700만원인지 아직 몰라요", 6_000_000, "uncertain"),
        ("이전 500만원은 아닌 것 같아요", 5_000_000, "uncertain"),
        ("견적은 600만원 정도로 생각 중", 6_000_000, "keep"),
    ],
)
def test_unclear_new_change_never_restores_prior_or_promotes_guess(
    monkeypatch, text, candidate, model_action
):
    data = req(
        "quote",
        text,
        current={"amount": None, "days": None},
        previous=previous_quote(),
        rules=[intent("amount", "set", candidate, text)],
    )
    if model_action == "uncertain":
        answer = intent("amount", "uncertain", None, text, intent_kind="ambiguous")
    else:
        answer = intent("amount", "keep", None, "", intent_kind="no_mention")
    modeled(monkeypatch, [answer])
    result = send(data)["amount"]
    assert_not_actionable(result)
    assert not (result.status == "valid" and result.value == 5_000_000)


@pytest.mark.parametrize(
    "text",
    ["견적은 600만원 정도일까요?", "금액을 600만원 또는 700만원으로 아직 고민 중이에요"],
)
def test_rule_only_fallback_cannot_auto_apply_speculative_money(text):
    # LLM 장애 시에도 안전 검증을 통과하지 않은 규칙 숫자를 자동 적용하면 안 된다.
    data = req(
        "quote",
        text,
        current={"amount": None, "days": None},
        previous=previous_quote(),
        rules=[intent("amount", "set", 6_000_000, text)],
    )
    result = {
        field.field: field
        for field in asyncio.run(assist_service.interpret(data, ctx(), use_model=False)).fields
    }
    assert_not_actionable(result["amount"])
