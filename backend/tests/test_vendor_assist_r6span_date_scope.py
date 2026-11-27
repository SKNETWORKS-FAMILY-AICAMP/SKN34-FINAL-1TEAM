"""쉼표 날짜 목록의 소유권과 정중한 날짜 교체 요청의 독립 회귀."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CONTEXT = AssistContext(
    today=date(2026, 11, 25),
    base_month=date(2026, 12, 1),
    crew_max=6,
    days_max=120,
    vat_modes=["included", "excluded", "exempt"],
)
CURRENT = {
    "period": {"start": "2026-12-02", "end": "2026-12-29", "excluded": ["2026-12-11"]},
    "dates": {"days": ["2026-12-04", "2026-12-18"], "crew": 2},
    "quote": {"amount": 4550000, "vat": "included", "days": 4, "valid": "2026-12-20"},
    "extra": {"title": "기존 공사", "reason": "기존 사유", "amount": 100000, "vat": "included"},
}


def day(n):
    return f"2026-12-{n:02d}"


def candidate(field, value, evidence, *, source="utterance"):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source=source,
    )


def no_mention(field):
    return FieldIntent(
        field=field, action="keep", value=None, evidence="", intent_kind="no_mention"
    )


def ambiguous(field, evidence):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=evidence, intent_kind="ambiguous"
    )


def verify_imports():
    assert not settings.llm_live
    root = os.environ.get("R6SPAN_WORKER19_ROOT")
    if root:
        backend = Path(root).resolve() / "backend"
        for name, module in tuple(sys.modules.items()):
            if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None):
                assert Path(module.__file__).resolve().is_relative_to(backend), name


def run(mp, form, text, rules=(), model=(), *, mode="rule", prior=None, current=None, rid="new"):
    verify_imports()
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("provider access forbidden")

    async def stub(data, context):
        calls.append(data.request_id)
        supplied = {x.field: x for x in model}
        return ModelInterpretation(
            fields=[supplied.get(field, no_mention(field)) for field in FORM_FIELDS[form]]
        )

    mp.setattr(llm, "structured", forbidden)
    mp.setattr(vendor_assist, "interpret", stub)
    request = AssistRequest(
        request_id=rid,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=CURRENT[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    original = request.model_dump(mode="json")
    trace = {}
    response = asyncio.run(interpret(request, CONTEXT, use_model=mode != "rule", trace=trace))
    assert calls == ([] if mode == "rule" else [rid])
    assert response.source == ("rule" if mode == "rule" else "llm")
    assert request.model_dump(mode="json") == original
    verify_imports()
    return {x.field: x for x in response.fields}, trace


def exact(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert not field.alternatives


def no_offer(field):
    assert field.action not in ("set", "clear") and not field.alternatives, field
    assert field.value is None


def actual_prior(mp, form, text, entries, mode):
    current = CURRENT[form]
    fields, _ = run(mp, form, text, entries, entries, mode=mode, rid="seed")
    for entry in entries:
        exact(fields[entry.field], entry.value)
    selected = [fields[entry.field] for entry in entries]
    return PreviousCandidate(
        request_id="seed",
        text=text,
        fields=[
            FieldIntent.model_validate(
                f.model_dump(
                    include={
                        "field",
                        "action",
                        "value",
                        "evidence",
                        "intent_kind",
                        "value_source",
                        "correction",
                    }
                )
            )
            for f in selected
        ],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: current[f.field] for f in selected},
    )


def carried(field, prior):
    previous = next(f for f in prior.fields if f.field == field.field)
    exact(field, previous.value)
    assert (field.intent_kind, field.value_source) == ("retain_previous", "previous"), field
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field]
    assert field.evidence == previous.evidence


def period_prior(mp, mode):
    start = "시작일은 12월 3일로 확정합니다."
    end = "종료일은 12월 27일로 확정합니다."
    return actual_prior(
        mp,
        "period",
        start + " " + end,
        [candidate("start", day(3), start), candidate("end", day(27), end)],
        mode,
    )


# 사전에 작성한 날짜와 기대값. 실제 호출 원문은 별도 보존 자료에서 재생한다.
LISTS = [
    "12월 7일, 13일, 21일은 제외일로 지정합니다.",
    "12월 7일, 12월 13일, 12월 21일은 방문 제외일로 정합니다.",
    "12/7, 12/13, 12/21은 제외일로 등록합니다.",
    "12월 7일, 13일과 21일은 제외일로 설정합니다.",
    "12월 7일과 13일, 21일은 제외일로 확정합니다.",
    "12월 7일과 13일과 21일은 제외일로 지정합니다.",
    "제외일은 12월 7일, 13일, 21일로 정합니다.",
]
LIST_VALUE = [day(7), day(13), day(21)]


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("text", LISTS)
def test_trailing_or_leading_list_preserves_both_pending_boundaries(monkeypatch, mode, text):
    prior = period_prior(monkeypatch, mode)
    good = candidate("excluded", LIST_VALUE, text)
    fields, trace = run(monkeypatch, "period", text, [good], [good], mode=mode, prior=prior)
    assert trace["previous_verified"] is not None
    exact(fields["excluded"], LIST_VALUE)
    carried(fields["start"], prior)
    carried(fields["end"], prior)


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("text", LISTS[:5])
def test_list_members_do_not_ground_boundary_offers(monkeypatch, mode, text):
    offered = [
        candidate("excluded", LIST_VALUE, text),
        candidate("start", day(7), text),
        candidate("end", day(13), text),
    ]
    fields, _ = run(monkeypatch, "period", text, offered, offered, mode=mode)
    exact(fields["excluded"], LIST_VALUE)
    no_offer(fields["start"])
    no_offer(fields["end"])


MIXED = [
    (
        "시작일은 12월 5일로 지정합니다, 12월 9일, 16일은 제외일로 정합니다.",
        {"start": day(5), "excluded": [day(9), day(16)]},
        {"end"},
    ),
    (
        "12월 9일, 16일은 제외일로 정합니다, 종료일은 12월 25일로 지정합니다.",
        {"end": day(25), "excluded": [day(9), day(16)]},
        {"start"},
    ),
    (
        "시작일은 12월 5일, 종료일은 12월 25일, 12월 9일, 16일은 제외일로 지정합니다.",
        {"start": day(5), "end": day(25), "excluded": [day(9), day(16)]},
        set(),
    ),
    (
        "방문 가능 기간은 12월 5일부터 25일까지입니다. 12월 9일, 16일은 제외일로 정합니다.",
        {"start": day(5), "end": day(25), "excluded": [day(9), day(16)]},
        set(),
    ),
]


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("text,expected,retained", MIXED)
def test_independent_boundary_range_and_list_ownership(monkeypatch, mode, text, expected, retained):
    prior = period_prior(monkeypatch, mode)
    proposals = [candidate(name, value, text) for name, value in expected.items()]
    fields, _ = run(monkeypatch, "period", text, proposals, proposals, mode=mode, prior=prior)
    for name, value in expected.items():
        exact(fields[name], value)
    for name in retained:
        carried(fields[name], prior)


UNSAFE_LISTS = [
    "12월 7일, 13일, 21일은 제외일로 지정해도 될까요?",
    "가능하면 12월 7일, 13일, 21일은 제외일로 정해 주세요.",
    "12월 7일, 13일, 21일은 제외일로 정하지 마세요.",
    "12월 7일, 13일, 21일은 제외일로 지정합니다. 제외일 변경은 취소합니다.",
    "12월 7일, 13일, 21일 중 아직 고민 중입니다. 제외일은 미정입니다.",
    "12월 7일, 13일, 32일은 제외일로 지정합니다.",
    "12월 7일, 13일, 13일은 제외일로 지정합니다.",
]


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("text", UNSAFE_LISTS)
def test_unsafe_list_does_not_offer_values(monkeypatch, mode, text):
    offered = [
        candidate("excluded", LIST_VALUE, text),
        candidate("start", day(7), text),
        candidate("end", day(13), text),
    ]
    fields, _ = run(monkeypatch, "period", text, offered, offered, mode=mode)
    for field in fields.values():
        no_offer(field)


EDIT = {
    "dates": {
        "field": "days",
        "seed": "작업일은 12월 6일과 19일로 지정합니다. 인원은 3명으로 확정합니다.",
        "seed_parts": [
            ("days", [day(6), day(19)], "작업일은 12월 6일과 19일로 지정합니다."),
            ("crew", 3, "인원은 3명으로 확정합니다."),
        ],
        "prefix": "앞서 정한 작업일 중",
        "sibling": "crew",
        "other": ("crew", 4, "인원은 4명으로 확정합니다."),
    },
    "period": {
        "field": "excluded",
        "seed": "제외일은 12월 6일과 19일로 지정합니다. 종료일은 12월 27일로 확정합니다.",
        "seed_parts": [
            ("excluded", [day(6), day(19)], "제외일은 12월 6일과 19일로 지정합니다."),
            ("end", day(27), "종료일은 12월 27일로 확정합니다."),
        ],
        "prefix": "앞서 정한 제외일 중",
        "sibling": "end",
        "other": ("end", day(26), "종료일은 12월 26일로 확정합니다."),
    },
}
EDIT_VALUE = [day(6), day(22)]
DIRECT = [
    "19일을 22일로 옮겨 주실래요?",
    "19일을 22일로 옮겨 주시겠어요?",
    "19일을 22일로 바꿔 주실래요?",
    "19일을 22일로 변경해 주시겠어요?",
    "19일을 22일로 옮겨 주세요.",
]


def edit_prior(mp, form, mode):
    config = EDIT[form]
    return actual_prior(
        mp, form, config["seed"], [candidate(*args) for args in config["seed_parts"]], mode
    )


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("form", EDIT)
@pytest.mark.parametrize("suffix", DIRECT)
def test_polite_direct_replacement_derives_full_verified_list(monkeypatch, mode, form, suffix):
    prior = edit_prior(monkeypatch, form, mode)
    config = EDIT[form]
    text = config["prefix"] + " " + suffix
    field = config["field"]
    fields, trace = run(
        monkeypatch, form, text, [], [ambiguous(field, text)], mode=mode, prior=prior
    )
    exact(fields[field], EDIT_VALUE)
    assert fields[field].origin_request_id == "new"
    assert fields[field].correction is not None
    assert fields[field].correction.rejected_value == [day(6), day(19)]
    assert trace["date_edit_reviewed"], trace["decisions"]
    carried(fields[config["sibling"]], prior)


EDIT_UNSAFE = [
    "19일을 22일로 옮겨도 될까요?",
    "19일을 22일로 옮길 수 있을까요?",
    "19일을 22일로 옮겨 주실 수 있나요?",
    "19일을 22일로 옮기지 말아 주세요.",
    "19일을 22일로 옮겨 주실래요? 변경 요청은 취소합니다.",
    "가능하면 19일을 22일로 옮겨 주실래요?",
    "22일을 19일로 옮겨 주실래요?",
    "19일을 6일로 옮겨 주실래요?",
]


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("form", EDIT)
@pytest.mark.parametrize("suffix", EDIT_UNSAFE)
def test_unverified_edit_cannot_become_plain_list(monkeypatch, mode, form, suffix):
    prior = edit_prior(monkeypatch, form, mode)
    config = EDIT[form]
    text = config["prefix"] + " " + suffix
    field = config["field"]
    forged = candidate(field, EDIT_VALUE, text, source="derived")
    fields, trace = run(monkeypatch, form, text, [forged], [forged], mode=mode, prior=prior)
    no_offer(fields[field])
    assert not trace["date_edit_reviewed"]


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("form", EDIT)
@pytest.mark.parametrize("state", ["missing", "stale"])
def test_polite_replacement_requires_prior_and_baseline(monkeypatch, mode, form, state):
    config = EDIT[form]
    field = config["field"]
    prior = None
    if state == "stale":
        prior = edit_prior(monkeypatch, form, mode)
        prior = prior.model_copy(update={"baselines": {**prior.baselines, field: [day(8)]}})
    text = config["prefix"] + " " + DIRECT[0]
    forged = candidate(field, EDIT_VALUE, text, source="derived")
    fields, trace = run(monkeypatch, form, text, [forged], [forged], mode=mode, prior=prior)
    no_offer(fields[field])
    assert not trace["date_edit_reviewed"]
    if state == "stale":
        assert trace["previous_rejected"].get(field) == "stale_context"


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("form", EDIT)
@pytest.mark.parametrize("direct", [True, False])
def test_date_edit_and_independent_sibling_request(monkeypatch, mode, form, direct):
    config = EDIT[form]
    prior = edit_prior(monkeypatch, form, mode)
    field = config["field"]
    clause = config["prefix"] + " " + (DIRECT[0] if direct else EDIT_UNSAFE[0])
    other = candidate(*config["other"])
    text = clause + " " + other.evidence
    proposed = candidate(field, EDIT_VALUE, clause, source="derived")
    fields, _ = run(
        monkeypatch, form, text, [proposed, other], [proposed, other], mode=mode, prior=prior
    )
    if direct:
        exact(fields[field], EDIT_VALUE)
    else:
        no_offer(fields[field])
    exact(fields[other.field], other.value)


COMMON = [
    ("quote", "valid", day(26), "유효기간을 12월 26일로 정해", "유효기간을 12월 26일로 정해도"),
    (
        "quote",
        "amount",
        5200000,
        "견적 금액을 520만 원으로 변경해",
        "견적 금액을 520만 원으로 변경해도",
    ),
    (
        "extra",
        "title",
        "현관 문턱 보강",
        "추가 작업 제목을 현관 문턱 보강으로 적어",
        "추가 작업 제목을 현관 문턱 보강으로 적어도",
    ),
    (
        "extra",
        "amount",
        380000,
        "추가 금액을 38만 원으로 변경해",
        "추가 금액을 38만 원으로 변경해도",
    ),
    ("dates", "crew", 4, "작업 인원을 4명으로 정해", "작업 인원을 4명으로 정해도"),
    ("period", "end", day(26), "종료일을 12월 26일로 정해", "종료일을 12월 26일로 정해도"),
]


@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("form,field,value,request_clause,inquiry", COMMON)
@pytest.mark.parametrize("direct", [True, False])
def test_common_direct_request_vs_permission_question(
    monkeypatch, mode, form, field, value, request_clause, inquiry, direct
):
    text = request_clause + " 주실래요?" if direct else inquiry + " 될까요?"
    proposal = candidate(field, value, text)
    fields, _ = run(monkeypatch, form, text, [proposal], [proposal], mode=mode)
    if direct:
        exact(fields[field], value)
    else:
        no_offer(fields[field])


# Additional frozen integration control: an explicit quantity belongs to
# the complete list but is never another calendar member.
@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize(
    "count,accepted", [("사흘", True), ("나흘", False), ("세 날짜", True), ("네 날짜", False)]
)
def test_counted_exclusion_list_scope(monkeypatch, mode, count, accepted):
    prior = period_prior(monkeypatch, mode)
    text = f"제외일은 12월 7일, 13일, 21일 {count}로 확정합니다."
    proposal = candidate("excluded", LIST_VALUE, text)
    fields, _ = run(monkeypatch, "period", text, [proposal], [proposal], mode=mode, prior=prior)
    carried(fields["start"], prior)
    carried(fields["end"], prior)
    if accepted:
        exact(fields["excluded"], LIST_VALUE)
    else:
        no_offer(fields["excluded"])


# The list owner is independent of the terminal edit verb. Unlike a single
# member replacement, enumerating a new full list must not infer an old member.
@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize(
    "ending,accepted",
    [
        ("바꿔 주세요.", True),
        ("고치겠습니다.", True),
        ("변경해 주실래요?", True),
        ("바꿔도 될까요?", False),
        ("고치지 마세요.", False),
        ("변경할지 고민 중입니다.", False),
    ],
)
def test_full_list_terminal_edit_ownership(monkeypatch, mode, ending, accepted):
    prior = period_prior(monkeypatch, mode)
    text = f"제외일은 7일, 13일, 21일로 {ending}"
    proposal = candidate("excluded", LIST_VALUE, text)
    fields, _ = run(monkeypatch, "period", text, [proposal], [proposal], mode=mode, prior=prior)
    carried(fields["start"], prior)
    carried(fields["end"], prior)
    if accepted:
        exact(fields["excluded"], LIST_VALUE)
    else:
        no_offer(fields["excluded"])
