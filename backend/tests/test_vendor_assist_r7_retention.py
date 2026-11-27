"""R7: grouped historical retention, real source spans and per-field recovery."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor import assist_intent
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

BASE = {
    "quote": {"amount": 2600000, "days": 3, "vat": "included", "valid": "2027-02-26"},
    "period": {"start": "2027-02-01", "end": "2027-02-26", "excluded": []},
    "dates": {"days": ["2027-02-09", "2027-02-16"], "crew": 2},
    "extra": {"title": "점검", "reason": "현장 확인", "amount": 120000, "vat": "included"},
}
PROPOSALS = {
    "quote": {
        "amount": (3710000, "총액은 371만원"),
        "days": (7, "공사 일수는 7일"),
        "vat": ("excluded", "세금은 별도"),
    },
    "period": {
        "start": ("2027-02-03", "시작일은 2월 3일"),
        "end": ("2027-02-24", "종료일은 2월 24일"),
        "excluded": (["2027-02-11"], "제외일은 2월 11일"),
    },
    "dates": {
        "days": (["2027-02-10", "2027-02-17"], "작업 날짜는 2월 10일과 17일"),
        "crew": (4, "인원은 4명"),
    },
    "extra": {
        "title": ("문틀 보강", "항목명은 문틀 보강"),
        "reason": ("고정 불량", "사유는 고정 불량"),
        "amount": (530000, "추가 금액은 53만원"),
    },
}
SUBJECTS = {
    "quote": "총액과 공사 일수, 세금 조건",
    "period": "시작일과 종료일, 제외일",
    "dates": "작업 날짜와 인원",
    "extra": "항목명과 사유, 추가 금액",
}


@pytest.fixture(autouse=True)
def isolate_and_forbid_provider(monkeypatch):
    assert not settings.llm_live
    if root := os.environ.get("R7_RETENTION_ROOT"):
        backend = Path(root).resolve() / "backend"
        assert all(
            Path(module.__file__).resolve().is_relative_to(backend)
            for name, module in tuple(sys.modules.items())
            if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None)
        )

    async def forbidden(*args, **kwargs):
        pytest.fail("external model forbidden")

    monkeypatch.setattr(llm, "structured", forbidden)


@pytest.mark.parametrize("form", list(SUBJECTS))
@pytest.mark.parametrize(
    "prefix,reference,first",
    [
        ("앞에 제시한", "previous", False),
        ("앞에서 정했던", "previous", False),
        ("처음에 제안한", "previous", True),
        ("지금", "current", False),
    ],
)
def test_group_scope_owns_actual_prefix_and_list(form, prefix, reference, first):
    text = f"{prefix} {SUBJECTS[form]}을 그대로 적용해 주세요."
    scopes = assist_intent.retention_scopes(form, text)
    assert len(scopes) == 1
    scope = scopes[0]
    assert scope.start == 0 and scope.end == len(text) - 1
    assert scope.fields == set(PROPOSALS[form])
    assert scope.compatible and scope.reference == reference and scope.first is first
    assert tuple(part.fields for part in assist_intent.intent_clauses(form, text)) == (
        set(PROPOSALS[form]),
    )


@pytest.mark.parametrize(
    "form,text,fields",
    [
        (
            "quote",
            "앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요.",
            {"amount", "days", "vat"},
        ),
        ("quote", "금액이랑 일수는 직전에 정한 값 그대로 유지해 주세요.", {"amount", "days"}),
        (
            "period",
            "앞에서 정했던 기간 및 제외일을 그대로 적용해 주세요.",
            {"start", "end", "excluded"},
        ),
        ("dates", "직전에 제시한 날짜와 인원을 그대로 유지해 주세요.", {"days", "crew"}),
        ("extra", "최초에 제안한 항목명과 사유를 그대로 반영해 주세요.", {"title", "reason"}),
    ],
)
def test_independent_group_witness_is_contiguous(form, text, fields):
    helper = getattr(assist_intent, "explicit_group_retention_scopes", None)
    assert helper is not None
    for field in FORM_FIELDS[form]:
        scopes = helper(form, text, field)
        if field in fields:
            assert len(scopes) == 1
            assert scopes[0].fields == fields
            assert text[scopes[0].start : scopes[0].end] == text.rstrip(".")
        else:
            assert not scopes


@pytest.mark.parametrize(
    "form,text,field",
    [
        ("quote", "총액과 공사 일수를 그대로 유지해 주세요.", "amount"),
        ("quote", "앞에 정한 총액과 공사 일수는 그대로 적용할까요?", "amount"),
        ("quote", "앞에 정한 금액과 일수를 그대로 적용하지 마세요.", "days"),
        ("quote", "앞에 정한 금액과 일수", "amount"),
        ("quote", "앞에 정한 금액만 그대로 적용해 주세요.", "amount"),
        ("quote", "앞에 정한 금액과 일수는 날짜 그대로 유지해 주세요.", "amount"),
        ("quote", "전체를 앞서 제시한 그대로 유지해 주세요.", "amount"),
        (
            "extra",
            "사유는 '앞에 제시한 항목명과 금액을 그대로 유지해 주세요'로 입력합니다.",
            "amount",
        ),
        ("extra", "사유는 “처음에 정한 항목명과 금액을 그대로 유지해 주세요”입니다.", "title"),
    ],
)
def test_no_independent_group_witness_for_wrong_scope(form, text, field):
    helper = getattr(assist_intent, "explicit_group_retention_scopes", None)
    assert helper is not None
    assert not helper(form, text, field)


@pytest.mark.parametrize(
    "label,field",
    [
        ("방문 가능한 마감일", "end"),
        ("실측 가능한 종료일", "end"),
        ("방문 가능 시작일", "start"),
        ("실측 가능한 첫 방문 날짜", "start"),
    ],
)
def test_visit_qualifier_is_part_of_period_owner(label, field):
    text = f"{label}은 2월 24일로 정합니다."
    mentions = assist_intent.field_mentions("period", text)
    assert len(mentions) == 1
    assert mentions[0].fields == {field}
    assert text[mentions[0].start : mentions[0].end] == label


@pytest.mark.parametrize(
    "label,fields,value",
    [
        ("가능 기간 종료일", {"end"}, "2월 24일"),
        ("가능 기간 시작일", {"start"}, "2월 3일"),
        ("실측 가능 기간의 시작일", {"start"}, "2월 3일"),
        ("방문 가능 기간 마감일", {"end"}, "2월 24일"),
        ("방문 가능한 기간의 종료일", {"end"}, "2월 24일"),
        ("가능 기간", {"start", "end"}, "2월 3일부터 24일까지"),
        ("방문 가능 기간", {"start", "end"}, "2월 3일부터 24일까지"),
    ],
)
def test_period_boundary_qualifier_does_not_become_whole_period(label, fields, value):
    text = f"{label}은 {value}로 지정해 주세요."
    mentions = assist_intent.field_mentions("period", text)
    assert len(mentions) == 1
    assert mentions[0].fields == fields
    assert text[mentions[0].start : mentions[0].end] == label
    assert tuple(part.fields for part in assist_intent.intent_clauses("period", text)) == (fields,)


@pytest.mark.parametrize("form", ["quote", "extra"])
@pytest.mark.parametrize(
    "text,fields",
    [
        ("부가세는 금액과 별개로 청구하는 방식으로 지정해 주세요.", ({"vat"},)),
        (
            "부가세는 견적 금액과 별개로 청구해 주세요. 총액은 370만원입니다.",
            ({"vat"}, {"amount"}),
        ),
        (
            "총액은 370만원입니다. 부가세는 공사비에서 별개로 청구합니다.",
            ({"amount"}, {"vat"}),
        ),
        ("금액과 세금 조건은 앞서 정한 값 그대로 적용해 주세요.", ({"amount"}, {"vat"})),
        ("별도 비용은 미정입니다.", ({"amount"},)),
        ("부가가치세 조건은 포함입니다.", ({"vat"},)),
        ("VAT 조건은 별도입니다.", ({"vat"},)),
    ],
)
def test_vat_value_reference_is_not_an_independent_amount_owner(form, text, fields):
    mentions = assist_intent.field_mentions(form, text)
    assert tuple(mention.fields for mention in mentions) == fields


@pytest.mark.parametrize(
    "text,mode",
    [
        ("부가세 미포함 선택은 취소합니다.", "excluded"),
        ("세금은 금액과 별개로 청구하는 방식 후보를 철회합니다.", "excluded"),
        ("부가가치세 조건을 포함하지 않는 방식 후보는 취소합니다.", "excluded"),
        ("면세 조건은 철회합니다.", "exempt"),
    ],
)
def test_vat_withdrawal_owns_scoped_selected_mode(text, mode):
    mentions = assist_intent.field_mentions("quote", text)
    assert len(mentions) == 1 and mentions[0].fields == {"vat"}
    assert mentions[0].selected_mode == mode
    assert mentions[0].start == 0
    assert any(word in text[: mentions[0].end] for word in ("후보", "선택", "조건"))


def _ctx(form):
    return AssistContext(
        today=date(2026, 11, 25),
        base_month=date(2027, 2, 1),
        days_max=120,
        crew_max=6,
        required_days=2 if form == "dates" else None,
        vat_modes=["included", "excluded"]
        if form == "extra"
        else ["included", "excluded", "exempt"],
    )


def _field(name, action="keep", value=None, evidence="", intent=None, source=None):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
    )


def _run(
    monkeypatch,
    form,
    text,
    *,
    request_id,
    model,
    rules=(),
    prior=None,
    current=None,
    use_model=True,
):
    req = AssistRequest(
        request_id=request_id,
        form=form,
        resource_id=UUID(int=179),
        text=text,
        current=BASE[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    before = req.model_dump(mode="json")
    calls = []

    async def stub(*args, **kwargs):
        calls.append(True)
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(req, _ctx(form), use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    assert req.model_dump(mode="json") == before
    return {field.field: field for field in response.fields}, trace


def _seed(monkeypatch, form, use_model):
    values = PROPOSALS[form]
    text = ". ".join(evidence + "입니다" for _, evidence in values.values()) + "."
    intents = [
        _field(name, "set", value, evidence, "explicit_change", "utterance")
        for name, (value, evidence) in values.items()
    ]
    out, _ = _run(
        monkeypatch,
        form,
        text,
        request_id="r7-seed",
        model=intents,
        rules=intents,
        use_model=use_model,
    )
    for name, (value, _) in values.items():
        assert (out[name].status, out[name].action, out[name].value) == ("valid", "set", value), out
    return PreviousCandidate(
        request_id="r7-seed",
        text=text,
        fields=[
            _field(name, out[name].action, out[name].value, out[name].evidence) for name in values
        ],
        origins={name: out[name].origin_request_id for name in values},
        sources={name: out[name].source for name in values},
        baselines={name: BASE[form][name] for name in values},
    )


def _model_reconstructed(form, kind="retain_previous"):
    # Each sentence intentionally reconstructs text that does NOT occur contiguously.
    aliases = {
        "amount": "금액",
        "days": "일수" if form == "quote" else "날짜",
        "vat": "세금 조건",
        "start": "시작일",
        "end": "종료일",
        "excluded": "제외일",
        "crew": "인원",
        "title": "항목명",
        "reason": "사유",
    }
    return [
        _field(
            name,
            evidence=f"앞에 제시한 {aliases[name]}을 그대로 적용해 주세요",
            intent=kind,
            source="previous" if kind == "retain_previous" else "current",
        )
        for name in PROPOSALS[form]
    ]


def _assert_carry(result, prior, field):
    old = next(item for item in prior.fields if item.field == field)
    assert (result.status, result.action, result.value) == ("valid", "set", old.value), result
    assert (result.intent_kind, result.value_source) == ("retain_previous", "previous"), result
    assert result.origin_request_id == prior.origins[field]
    assert result.source == prior.sources[field]
    assert result.evidence == old.evidence


@pytest.mark.parametrize("form", list(SUBJECTS))
@pytest.mark.parametrize("use_model", [False, True])
def test_group_recovery_in_actual_service(monkeypatch, form, use_model):
    prior = _seed(monkeypatch, form, use_model)
    text = f"앞에 제시한 {SUBJECTS[form]}을 그대로 적용해 주세요."
    model = _model_reconstructed(form)
    assert all(item.evidence not in text for item in model)
    out, _ = _run(
        monkeypatch,
        form,
        text,
        request_id="r7-follow",
        model=model,
        prior=prior,
        use_model=use_model,
    )
    for name in PROPOSALS[form]:
        _assert_carry(out[name], prior, name)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,blocked",
    [
        (
            "조건이 맞으면 앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요.",
            {"amount", "days", "vat"},
        ),
        ("앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용할까요?", {"amount", "days", "vat"}),
        (
            "앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요. 금액 변경은 취소합니다.",
            {"amount"},
        ),
        (
            "앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요. 공사 일수는 현재 값 그대로 유지해 주세요.",
            {"days"},
        ),
        (
            "앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요. 유효기간은 아직 미정입니다.",
            set(),
        ),
    ],
)
def test_group_recovery_respects_independent_and_competing_intent(
    monkeypatch, use_model, text, blocked
):
    form = "quote"
    prior = _seed(monkeypatch, form, use_model)
    out, _ = _run(
        monkeypatch,
        form,
        text,
        request_id="r7-follow",
        model=_model_reconstructed(form),
        prior=prior,
        use_model=use_model,
    )
    for name in PROPOSALS[form]:
        if name in blocked:
            assert not (out[name].status == "valid" and out[name].action == "set"), out[name]
            assert not out[name].alternatives
        else:
            _assert_carry(out[name], prior, name)


@pytest.mark.parametrize("use_model", [False, True])
def test_stale_group_member_never_recovered(monkeypatch, use_model):
    prior = _seed(monkeypatch, "quote", use_model)
    text = "앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요."
    out, _ = _run(
        monkeypatch,
        "quote",
        text,
        request_id="r7-stale",
        model=_model_reconstructed("quote"),
        prior=prior,
        current={**BASE["quote"], "amount": 2800000},
        use_model=use_model,
    )
    assert not (out["amount"].status == "valid" and out["amount"].action == "set")
    for name in ("days", "vat"):
        _assert_carry(out[name], prior, name)


# Supplement: group quantifiers belong to the joint subject, never to the
# last member alone. The fixture is frozen before changing the grammar.
@pytest.mark.parametrize(
    "form,text,fields,first,reference",
    [
        (
            "quote",
            "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요.",
            {"amount", "days"},
            False,
            "previous",
        ),
        (
            "quote",
            "최초에 제시한 금액과 작업기간 두 항목 모두 그대로 반영해 주세요.",
            {"amount", "days"},
            True,
            "previous",
        ),
        (
            "quote",
            "금액과 일수, 세금 조건은 셋 다 직전에 정한 값으로 유지해 주세요.",
            {"amount", "days", "vat"},
            False,
            "previous",
        ),
        (
            "period",
            "앞에서 정한 시작일과 종료일 둘 다 그대로 유지해 주세요.",
            {"start", "end"},
            False,
            "previous",
        ),
        (
            "period",
            "앞에서 정한 기간과 제외일 두 항목 모두 그대로 반영해 주세요.",
            {"start", "end", "excluded"},
            False,
            "previous",
        ),
        (
            "dates",
            "지금 작업 날짜와 인원 둘 다 그대로 유지해 주세요.",
            {"days", "crew"},
            False,
            "current",
        ),
        (
            "extra",
            "앞서 제안한 항목명과 사유를 둘다 그대로 적용해 주세요.",
            {"title", "reason"},
            False,
            "previous",
        ),
        (
            "extra",
            "이전에 정한 항목명, 사유, 금액 및 세금 네 항목 모두 그대로 유지해 주세요.",
            {"title", "reason", "amount", "vat"},
            False,
            "previous",
        ),
    ],
)
def test_quantified_group_owns_complete_typed_reference(form, text, fields, first, reference):
    scopes = assist_intent.retention_scopes(form, text)
    assert len(scopes) == 1
    scope = scopes[0]
    assert (scope.fields, scope.reference, scope.first, scope.compatible) == (
        fields,
        reference,
        first,
        True,
    )
    assert text[scope.start : scope.end] == text.rstrip(".")
    assert tuple(part.fields for part in assist_intent.intent_clauses(form, text)) == (fields,)
    for name in FORM_FIELDS[form]:
        witnesses = assist_intent.explicit_group_retention_scopes(form, text, name)
        assert witnesses == ((scope,) if name in fields else ())


@pytest.mark.parametrize(
    "text",
    [
        "직전에 제안한 금액을 둘 다 그대로 유지해 주세요.",
        "직전에 제안한 금액과 작업기간을 셋 다 그대로 유지해 주세요.",
        "직전에 제안한 금액과 총액을 둘 다 그대로 유지해 주세요.",
        "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지할까요?",
        "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지하지 마세요.",
        "직전에 제안한 금액과 작업기간을 둘 다",
        "직전에 제안한 금액과 작업기간을 둘 다 날짜 그대로 유지해 주세요.",
        "‘직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요.’",
    ],
)
def test_quantified_group_witness_rejects_non_matching_count_or_directive(text):
    for name in ("amount", "days"):
        assert not assist_intent.explicit_group_retention_scopes("quote", text, name)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,carried",
    [
        ("직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요.", {"amount", "days"}),
        (
            "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요. 부가세는 미정입니다.",
            {"amount", "days"},
        ),
        (
            "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요. 금액 변경은 취소합니다.",
            {"days"},
        ),
        (
            "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요. 작업기간은 현재 값 그대로 유지해 주세요.",
            {"amount"},
        ),
        ("승인되면 직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요.", set()),
        ("직전에 제안한 금액과 작업기간을 둘 다 그대로 유지할까요?", set()),
        ("직전에 제안한 금액과 작업기간을 둘 다 그대로 유지하지 마세요.", set()),
        ("직전에 제안한 금액과 작업기간을 셋 다 그대로 유지해 주세요.", set()),
        (
            "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요. 그 요청은 취소합니다.",
            set(),
        ),
    ],
)
def test_quantified_group_actual_service_recovery(monkeypatch, use_model, text, carried):
    form = "quote"
    prior = _seed(monkeypatch, form, use_model)
    # Both independent producers have no reliable value/evidence for the
    # current request. Recovery must come from the real joint instruction.
    model = [
        _field(
            name,
            evidence=f"직전에 제안한 {label}을 그대로 유지",
            intent="retain_previous",
            source="previous",
        )
        for name, label in (("amount", "금액"), ("days", "작업기간"))
    ]
    assert all(item.evidence not in text for item in model)
    out, _ = _run(
        monkeypatch,
        form,
        text,
        request_id="r7-group-last",
        model=model,
        prior=prior,
        use_model=use_model,
    )
    for name in ("amount", "days"):
        if name in carried:
            _assert_carry(out[name], prior, name)
        else:
            assert not (out[name].status == "valid" and out[name].action == "set"), out[name]
            assert not out[name].alternatives


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "lead,carried",
    [
        ("고객 승인되면 ", set()),
        ("고객 승인되면, ", set()),
        ("부가세가 확정되면 ", set()),
        ("부가세가 승인되면 포함으로 정해 주세요. ", {"amount", "days"}),
    ],
)
def test_quantified_group_condition_owner(monkeypatch, use_model, lead, carried):
    form = "quote"
    prior = _seed(monkeypatch, form, use_model)
    group = "직전에 제안한 금액과 작업기간을 둘 다 그대로 유지해 주세요."
    # Even a contiguous model evidence span is not sufficient if the full
    # instruction conditions this group. An independent earlier clause is
    # not the group's condition.
    candidates = [
        _field(name, evidence=group.rstrip("."), intent="retain_previous", source="previous")
        for name in ("amount", "days")
    ]
    out, _ = _run(
        monkeypatch,
        form,
        lead + group,
        request_id="r7-group-condition",
        model=candidates,
        prior=prior,
        use_model=use_model,
    )
    for name in ("amount", "days"):
        if name in carried:
            _assert_carry(out[name], prior, name)
        else:
            assert not (out[name].status == "valid" and out[name].action == "set"), out[name]
            assert not out[name].alternatives


# The list noun modifies an actual exclusion-day owner, rather than adding
# a field or widening the instruction to every field in the form.
@pytest.mark.parametrize(
    "text,labels,fields,reference",
    [
        (
            "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요",
            ("실측 가능 기간", "제외일 목록"),
            {"start", "end", "excluded"},
            "previous",
        ),
        (
            "앞서 제안한 시작일과 방문 제외 날짜 목록을 둘 다 그대로 유지해 주세요.",
            ("시작일", "방문 제외 날짜 목록"),
            {"start", "excluded"},
            "previous",
        ),
        (
            "종료일과 휴무 날짜 목록을 이전 제안대로 반영해 주세요.",
            ("종료일", "휴무 날짜 목록"),
            {"end", "excluded"},
            "previous",
        ),
        (
            "현재 시작일과 불가일 목록 둘 다 그대로 유지해 주세요.",
            ("시작일", "불가일 목록"),
            {"start", "excluded"},
            "current",
        ),
    ],
)
def test_exclusion_list_noun_is_part_of_named_group_owner(text, labels, fields, reference):
    form = "period"
    mentions = assist_intent.field_mentions(form, text)
    assert tuple(text[m.start : m.end] for m in mentions) == labels
    scopes = assist_intent.retention_scopes(form, text)
    assert len(scopes) == 1
    scope = scopes[0]
    assert scope.fields == fields and scope.reference == reference and scope.compatible
    assert text[scope.start : scope.end] == text.rstrip(".")
    assert tuple(part.fields for part in assist_intent.intent_clauses(form, text)) == (fields,)
    for name in FORM_FIELDS[form]:
        assert assist_intent.explicit_group_retention_scopes(form, text, name) == (
            (scope,) if name in fields else ()
        )


@pytest.mark.parametrize(
    "text",
    [
        "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행할까요?",
        "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행하지 마세요.",
        "승인되면 실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요.",
        "실측 가능 기간과 제외일 목록 모두 앞서 정한 목록으로 유지해 주세요.",
        "실측 가능 기간과 제외일 목록표 모두 기존 제안대로 진행해 주세요.",
        "실측 가능 기간과 목록 모두 기존 제안대로 진행해 주세요.",
        "앞서 정한 제외일 목록과 휴무 목록을 둘 다 그대로 유지해 주세요.",
        "‘실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요’",
    ],
)
def test_exclusion_list_owner_does_not_expand_incompatible_group(text):
    for name in FORM_FIELDS["period"]:
        assert not assist_intent.explicit_group_retention_scopes("period", text, name)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,carried",
    [
        (
            "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요",
            {"start", "end", "excluded"},
        ),
        (
            "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요. 제외일 변경 요청은 취소합니다.",
            {"start", "end"},
        ),
        (
            "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요. 시작일은 현재 값 그대로 유지해 주세요.",
            {"end", "excluded"},
        ),
        (
            "승인되면 실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행해 주세요.",
            set(),
        ),
        (
            "실측 가능 기간과 제외일 목록 모두 기존 제안대로 진행할까요?",
            set(),
        ),
    ],
)
def test_exclusion_list_group_actual_service_recovery(monkeypatch, use_model, text, carried):
    form = "period"
    prior = _seed(monkeypatch, form, use_model)
    # A model's ellipsis/reconstructed evidence is not an independent witness.
    model = [
        _field(
            name,
            evidence="실측 가능 기간 ... 기존 제안대로 진행해 주세요"
            if name != "excluded"
            else "제외일 목록 ... 기존 제안대로 진행해 주세요",
            intent="retain_previous",
            source="previous",
        )
        for name in FORM_FIELDS[form]
    ]
    out, _ = _run(
        monkeypatch,
        form,
        text,
        request_id="r7-exclusion-list-group",
        model=model,
        prior=prior,
        use_model=use_model,
    )
    for name in FORM_FIELDS[form]:
        if name in carried:
            _assert_carry(out[name], prior, name)
        else:
            assert not (out[name].status == "valid" and out[name].action == "set"), out[name]
            assert not out[name].alternatives
