"""업체 도우미: 규칙/모델 불일치와 정정 근거의 안전한 병합 회귀 시험."""

from datetime import date
from uuid import uuid4

import pytest

from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
)
from app.vendor.assist_validation import merge_candidates, review_fields


def context() -> AssistContext:
    return AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))


def request(
    form: str,
    text: str,
    *,
    current: dict | None = None,
    rule_fields: list[FieldIntent] | None = None,
) -> AssistRequest:
    return AssistRequest(
        request_id="merge-fix-check",
        resource_id=uuid4(),
        form=form,
        text=text,
        current=current or {},
        previous_candidate=None,
        rule_fields=rule_fields or [],
    )


def field(name: str, action: str, value, evidence: str, **kw) -> FieldIntent:
    return FieldIntent(field=name, action=action, value=value, evidence=evidence, **kw)


def choose(
    req: AssistRequest,
    *,
    rule: FieldIntent | None = None,
    model: FieldIntent | None = None,
) -> tuple[object, object | None, object | None]:
    rules, _ = review_fields([rule] if rule else [], "rule", req, context())
    models, _ = review_fields([model] if model else [], "llm", req, context())
    merged = {item.field: item for item in merge_candidates(req, context(), rules, models, None)}
    name = rule.field if rule else model.field
    return merged[name], rules.get(name), models.get(name)


@pytest.mark.parametrize(
    ("text", "wrong_title"),
    [
        ("추가 사유 칸을 비워 주세요.", "추가 사유 칸을 비워 주세요."),
        ("추가 금액은 고객 승인 후 청구하는 거죠?", "추가 금액은 고객 승인 후 청구하는 거죠?"),
        ("추가 금액 12만 원 아니고 18만 원으로 고칩니다.", "추가 금액"),
        ("현장 사진을 올리고 다시 안내하겠습니다.", "현장 사진을 올리고 다시 안내하겠습니다."),
        (
            "사유 칸에는 기존 벽체 손상이라고 적어주세요.",
            "사유 칸에는 기존 벽체 손상이라고 적어주세요.",
        ),
        ("부가세 포함 안 하는 건 아니고, 포함 맞습니다.", "포함 맞습니다."),
    ],
)
def test_model_no_mention_does_not_approve_unscoped_extra_title(text, wrong_title):
    req = request("extra", text)
    result, reviewed_rule, reviewed_model = choose(
        req,
        rule=field("title", "set", wrong_title, text),
        model=field("title", "keep", None, "", intent_kind="no_mention"),
    )
    assert reviewed_rule.status == "valid"
    assert reviewed_model.status == "valid"
    assert result.action == "uncertain"
    assert result.status == "uncertain"
    assert result.value is None


@pytest.mark.parametrize(
    ("form", "text", "name", "value", "current"),
    [
        (
            "period",
            "12/7~12/11 됩니다. 9일은 못 가는 건 아니에요.",
            "excluded",
            ["2026-12-09"],
            {"start": "2026-12-07", "end": "2026-12-11", "excluded": []},
        ),
        (
            "period",
            "실측 시작일만 12월 3일로 입력해 주세요.",
            "end",
            "2026-12-03",
            {"start": "2026-12-01", "end": "2026-12-11", "excluded": []},
        ),
        (
            "period",
            "종료일만 12월 11일로 늘려요. 원래 제외 날짜는 유지하고요.",
            "start",
            "2026-12-11",
            {"start": "2026-12-07", "end": "2026-12-20", "excluded": []},
        ),
    ],
)
def test_model_no_mention_blocks_unsafe_exclusion_or_wrong_boundary(
    form, text, name, value, current
):
    req = request(form, text, current=current)
    result, reviewed_rule, reviewed_model = choose(
        req,
        rule=field(name, "set", value, text),
        model=field(name, "keep", None, "", intent_kind="no_mention"),
    )
    assert reviewed_rule.status == "valid"
    assert reviewed_model.status == "valid"
    assert result.action == "uncertain"
    assert result.value is None


@pytest.mark.parametrize(
    ("form", "text", "name", "value", "current"),
    [
        ("extra", "항목: 누수 보수", "title", "누수 보수", {}),
        ("quote", "견적 금액은 555만원으로 수정", "amount", 5_550_000, {}),
        ("quote", "부가세 포함으로 해 주세요", "vat", "included", {}),
        ("quote", "공사 기간은 5일입니다", "days", 5, {}),
        ("dates", "작업 인원 3명", "crew", 3, {}),
        (
            "period",
            "12/7부터 12/11까지 가능합니다",
            "start",
            "2026-12-07",
            {"start": None, "end": "2026-12-11", "excluded": []},
        ),
        (
            "period",
            "12/7부터 12/11까지 가능합니다",
            "end",
            "2026-12-11",
            {"start": "2026-12-07", "end": None, "excluded": []},
        ),
        (
            "period",
            "12/9는 안 돼요",
            "excluded",
            ["2026-12-09"],
            {"start": "2026-12-07", "end": "2026-12-11", "excluded": []},
        ),
    ],
)
def test_independent_positive_rule_proof_still_works_with_model_no_mention(
    form, text, name, value, current
):
    req = request(form, text, current=current)
    got, checked_rule, _ = choose(
        req,
        rule=field(name, "set", value, text),
        model=field(name, "keep", None, "", intent_kind="no_mention"),
    )
    assert checked_rule.status == "valid"
    assert got.status == "valid"
    assert got.action == "set"
    assert got.value == value


def test_invalid_rule_is_not_returned_or_used_to_veto_independent_model_end():
    req = request(
        "period",
        "종료일만 12월 30일로 변경",
        current={"start": "2026-12-01", "end": "2026-12-10", "excluded": []},
    )
    rule, _ = review_fields([field("start", "set", "2026-12-30", req.text)], "rule", req, context())
    model, _ = review_fields(
        [
            field("start", "keep", None, "", intent_kind="no_mention"),
            field("end", "set", "2026-12-30", "12월 30일"),
        ],
        "llm",
        req,
        context(),
    )
    assert rule["start"].status == "invalid"
    assert model["end"].status == "valid"
    merged = {item.field: item for item in merge_candidates(req, context(), rule, model, None)}
    assert merged["start"].action == "keep"
    assert merged["start"].status == "valid"
    assert merged["end"].value == "2026-12-30"
    assert merged["end"].status == "valid"


@pytest.mark.parametrize(
    ("form", "text", "name", "old_value", "new_value", "old_evidence", "new_evidence"),
    [
        (
            "quote",
            "455만 원은 아니고 555만 원이에요",
            "amount",
            4_550_000,
            5_550_000,
            "455만 원은 아니고",
            "555만 원이에요",
        ),
        (
            "quote",
            "면세는 아니고 부가세 포함으로 처리해 주세요",
            "vat",
            "exempt",
            "included",
            "면세는 아니고",
            "부가세 포함",
        ),
        (
            "quote",
            "455만 원에서 555만 원으로 변경",
            "amount",
            4_550_000,
            5_550_000,
            "455만 원에서",
            "555만 원으로 변경",
        ),
        (
            "quote",
            "공사 3일 아니고 5일 걸립니다",
            "days",
            3,
            5,
            "3일 아니고",
            "5일 걸립니다",
        ),
        (
            "dates",
            "2명 아니고 3명입니다",
            "crew",
            2,
            3,
            "2명 아니고",
            "3명입니다",
        ),
        (
            "extra",
            "추가 금액 12만 원 아니고 18만 원으로 고칩니다",
            "amount",
            120_000,
            180_000,
            "12만 원 아니고",
            "18만 원으로 고칩니다",
        ),
        (
            "quote",
            "견적 455만원 아니고 555만원",
            "amount",
            4_550_000,
            5_550_000,
            "455만원",
            "아니고 555만원",
        ),
    ],
)
def test_verified_correction_includes_connectors_inside_either_evidence_span(
    form, text, name, old_value, new_value, old_evidence, new_evidence
):
    item = field(
        name,
        "set",
        new_value,
        text,
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=old_value,
            rejected_evidence=old_evidence,
            accepted_evidence=new_evidence,
        ),
    )
    req = request(form, text)
    reviewed, _ = review_fields([item], "llm", req, context())
    candidate = reviewed[name]
    assert candidate.status == "valid"
    assert candidate.value == new_value
    assert candidate.correction is not None


@pytest.mark.parametrize(
    ("text", "old", "new", "rejected", "accepted"),
    [
        ("455만원에서 555만원 사이로 생각 중", 4_550_000, 5_550_000, "455만원에서", "555만원"),
        ("455만원 또는 555만원, 아직 미정", 4_550_000, 5_550_000, "455만원", "555만원"),
        ("555만원 말고 455만원", 4_550_000, 5_550_000, "455만원", "555만원"),
        ("455만원 말고 555만원, 다시 455만원", 4_550_000, 5_550_000, "455만원", "555만원"),
        ("455만원 말고 555만원 아니고 655만원", 4_550_000, 5_550_000, "455만원", "555만원"),
        ("455만원 말고 555만원?", 4_550_000, 5_550_000, "455만원", "555만원"),
        ("455만원 말고 기간은 3일, 555만원", 4_550_000, 5_550_000, "455만원 말고", "555만원"),
        ("455만원이고 인원 3명 말고 555만원", 4_550_000, 5_550_000, "455만원", "555만원"),
        ("455만원 말고 555만원", 5_550_000, 5_550_000, "455만원", "555만원"),
    ],
)
def test_false_or_ambiguous_correction_never_authorizes_replacement(
    text, old, new, rejected, accepted
):
    req = request("quote", text)
    item = field(
        "amount",
        "set",
        new,
        text,
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=old,
            rejected_evidence=rejected,
            accepted_evidence=accepted,
        ),
    )
    reviewed, _ = review_fields([item], "llm", req, context())
    assert reviewed["amount"].status == "uncertain"
    assert reviewed["amount"].reason_code == "correction_unverified"


def test_unverified_optional_relation_does_not_erase_independent_same_value():
    text = "견적 금액 555만원입니다"
    rule = field("amount", "set", 5_550_000, text)
    model = field(
        "amount",
        "set",
        5_550_000,
        "555만원",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence="455만원",
            accepted_evidence="555만원",
        ),
    )
    req = request("quote", text, rule_fields=[rule])
    reviewed, _ = review_fields([model], "llm", req, context())
    assert reviewed["amount"].status == "valid"
    assert reviewed["amount"].correction is None
    result, _, _ = choose(req, rule=rule, model=model)
    assert result.status == "valid"
    assert result.value == 5_550_000


def test_unverified_relation_without_agreement_cannot_override_conflicting_set():
    text = "견적 455만원, 견적 555만원"
    rule = field("amount", "set", 4_550_000, "455만원")
    model = field(
        "amount",
        "set",
        5_550_000,
        "555만원",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence="455만원",
            accepted_evidence="555만원",
        ),
    )
    req = request("quote", text, rule_fields=[rule])
    result, _, reviewed_model = choose(req, rule=rule, model=model)
    assert reviewed_model.status == "uncertain"
    assert result.status == "uncertain"
    assert result.value is None


def test_validated_relation_suppresses_only_the_proven_rejected_value():
    text = "견적 455만원 아니고 555만원으로 바꿔요"
    rule = field("amount", "set", 4_550_000, "455만원")
    model = field(
        "amount",
        "set",
        5_550_000,
        "555만원",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence="455만원 아니고",
            accepted_evidence="555만원",
        ),
    )
    req = request("quote", text, rule_fields=[rule])
    result, _, checked_model = choose(req, rule=rule, model=model)
    assert checked_model.correction is not None
    assert result.status == "valid"
    assert result.value == 5_550_000
    assert result.alternatives == []


@pytest.mark.parametrize(
    "accepted_span",
    ["555만원 아니고", "555만원 아니고 655만원"],
)
def test_intermediate_value_cannot_use_rejection_inside_accepted_evidence(
    accepted_span,
):
    text = "455만원 말고 555만원 아니고 655만원"
    rule = field("amount", "set", 5_550_000, text)
    model = field(
        "amount",
        "set",
        5_550_000,
        accepted_span,
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence="455만원",
            accepted_evidence=accepted_span,
        ),
    )
    req = request("quote", text, rule_fields=[rule])
    candidate, _, reviewed = choose(req, rule=rule, model=model)
    assert reviewed.status == "uncertain"
    assert reviewed.reason_code == "correction_unverified"
    assert candidate.status == "uncertain"
    assert candidate.value is None


@pytest.mark.parametrize(
    "accepted_span",
    ["555만원 취소하고", "555만원은 잘못된 값이고"],
)
def test_cancelled_accepted_evidence_cannot_approve_intermediate_price(
    accepted_span,
):
    text = f"455만원 말고 {accepted_span} 655만원"
    rule = field("amount", "set", 5_550_000, text)
    model = field(
        "amount",
        "set",
        5_550_000,
        accepted_span,
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence="455만원",
            accepted_evidence=accepted_span,
        ),
    )
    req = request("quote", text, rule_fields=[rule])
    chosen, _, reviewed = choose(req, rule=rule, model=model)
    assert reviewed.status == "uncertain"
    assert reviewed.reason_code == "correction_unverified"
    assert chosen.status == "uncertain"
    assert chosen.value is None


@pytest.mark.parametrize(
    "text",
    ["사유는 지워서는 안 됩니다", "사유 삭제는 하지 않겠습니다"],
)
def test_negated_deletion_does_not_clear_reason_with_model_no_mention(text):
    req = request("extra", text)
    candidate, reviewed_rule, _ = choose(
        req,
        rule=field("reason", "clear", None, text),
        model=field("reason", "keep", None, "", intent_kind="no_mention"),
    )
    assert reviewed_rule.status == "invalid"
    assert candidate.status != "valid"
    assert candidate.action == "uncertain"


@pytest.mark.parametrize(
    "text",
    ["견적은 600만원 정도일까요?", "금액은 600만원 또는 700만원으로 고민 중"],
)
def test_speculative_rule_only_money_is_held_even_without_model(text):
    req = request(
        "quote",
        text,
        rule_fields=[field("amount", "set", 6_000_000, text)],
    )
    checked, _ = review_fields(req.rule_fields, "rule", req, context())
    selected = {item.field: item for item in merge_candidates(req, context(), checked, None, None)}[
        "amount"
    ]
    assert selected.status == "uncertain"
    assert selected.action == "uncertain"
    assert selected.reason_code == "ambiguous_intent"


@pytest.mark.parametrize(
    ("form", "text", "name", "value"),
    [
        ("quote", "부가세 포함하지 마세요", "vat", "included"),
        ("quote", "부가세 포함은 잘못된 값입니다", "vat", "included"),
        ("quote", "이전 견적 480만원은 취소해주세요", "amount", 4_800_000),
        ("quote", "견적 금액은 480만원이 아닙니다", "amount", 4_800_000),
        ("quote", "견적 금액은 480만원 말고", "amount", 4_800_000),
        ("quote", "견적 금액 480만원을 원하지 않아요", "amount", 4_800_000),
        ("quote", "지난 견적은 480만원이었습니다", "amount", 4_800_000),
        ("dates", "세 명은 보내지 마세요", "crew", 3),
        ("extra", "항목은 누수 보수로 변경한 게 아닙니다", "title", "누수 보수"),
    ],
)
def test_rule_field_proof_rejects_negated_or_cancelled_scalar_action(form, text, name, value):
    req = request(form, text)
    candidate, checked, _ = choose(
        req,
        rule=field(name, "set", value, text),
        model=field(name, "keep", None, "", intent_kind="no_mention"),
    )
    assert checked.status == "valid"
    assert candidate.status == "uncertain"
    assert candidate.value is None


def test_unrelated_ambiguity_does_not_cancel_independent_quoted_amount():
    text = "견적 금액은 480만원으로 정했습니다, 공사 기간은 미정"
    req = request("quote", text)
    candidate, checked, _ = choose(
        req,
        rule=field("amount", "set", 4_800_000, text),
        model=field("amount", "keep", None, "", intent_kind="no_mention"),
    )
    assert checked.status == "valid"
    assert candidate.status == "valid"
    assert candidate.value == 4_800_000
