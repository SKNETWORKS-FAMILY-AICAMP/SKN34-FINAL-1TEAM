"""R3: 실제 날짜 교체 체인의 장기 계승과 무효 이력의 재검증."""

import asyncio
from datetime import date
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret
from app.vendor.assist_validation import review_previous

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")


def _day(n):
    return f"2026-12-{n:02d}"


def _setup(form):
    if form == "dates":
        return "days", {"days": [_day(8), _day(9), _day(16)], "crew": 2}, "작업일", "crew"
    return (
        "excluded",
        {"start": _day(2), "end": _day(28), "excluded": [_day(9), _day(20)]},
        "제외일",
        "end",
    )


def _context(form):
    return AssistContext(
        today=date(2026, 11, 23),
        base_month=date(2026, 12, 1),
        required_days=3 if form == "dates" else None,
    )


def _request(form, current, text, *, previous=None, rules=(), number=1):
    return AssistRequest(
        request_id=f"r3-history-{number}",
        form=form,
        resource_id=RESOURCE,
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=list(rules),
    )


def _run(request):
    trace = {}
    response = asyncio.run(interpret(request, _context(request.form), use_model=False, trace=trace))
    return {item.field: item for item in response.fields}, trace


def _carry(request, item):
    old = request.previous_candidate
    history = f"{old.text}\n{request.text}" if old else request.text
    assert len(history) <= 1000
    baseline = (
        old.baselines[item.field]
        if old and item.field in old.baselines
        else request.current[item.field]
    )
    return PreviousCandidate(
        request_id=request.request_id,
        text=history,
        fields=[
            FieldIntent(
                field=item.field,
                action=item.action,
                value=item.value,
                evidence=item.evidence,
                intent_kind="explicit_change",
                value_source="derived" if item.value_source == "derived" else None,
                correction=item.correction if item.value_source == "derived" else None,
            )
        ],
        baselines={item.field: baseline},
        sources={item.field: item.source},
        origins={item.field: item.origin_request_id or request.request_id},
    )


def _move(form, current, name, old, new, *, previous=None, number=1):
    ref = "앞에서 말한" if previous else "현재 폼의"
    text = f"{ref} {name} {old}일 대신 {new}일로 변경해 주세요."
    request = _request(form, current, text, previous=previous, number=number)
    fields, trace = _run(request)
    field = "days" if form == "dates" else "excluded"
    result = fields[field]
    assert result.status == "valid" and result.action == "set", trace
    assert result.source == "rule"
    assert result.origin_request_id == request.request_id
    assert result.value_source == "derived"
    assert result.correction is not None
    assert result.correction.kind == "replace"
    before = previous.fields[0].value if previous else current[field]
    assert result.correction.rejected_value == before
    assert result.value == [_day(new) if value == _day(old) else value for value in before]
    assert result.correction.rejected_evidence in text
    assert result.correction.accepted_evidence in text
    return request, result


@pytest.mark.parametrize("form", ["dates", "period"])
@pytest.mark.parametrize("seed", ["current", "proposed"])
def test_derived_chain_survives_eighteen_real_service_turns(form, seed):
    field, current, name, sibling = _setup(form)
    actual = list(current[field])
    previous = None
    number = 1
    if seed == "proposed":
        values = [_day(8), _day(9), _day(17)] if form == "dates" else [_day(9), _day(16)]
        text = f"{name}은 12월 " + ", ".join(f"{int(v[-2:])}일" for v in values)
        request = _request(
            form,
            current,
            text,
            rules=[FieldIntent(field=field, action="set", value=values, evidence=text)],
            number=number,
        )
        result, trace = _run(request)
        assert (result[field].status, result[field].value) == ("valid", values), trace
        previous = _carry(request, result[field])
        number += 1
    request, edited = _move(form, current, name, 9, 10, previous=previous, number=number)
    previous = _carry(request, edited)
    expected = edited
    for turn in range(number + 1, 19):
        if turn in (8, 15):
            old, new = (10, 11) if turn == 8 else (11, 12)
            request, expected = _move(form, current, name, old, new, previous=previous, number=turn)
            result = expected
        else:
            if turn % 2:
                text = f"{name}은 앞서 제안대로 유지해 주세요."
                rules = []
            else:
                text = "인원 3명" if form == "dates" else "종료일은 12월 27일로 변경해 주세요."
                value = 3 if form == "dates" else _day(27)
                rules = [FieldIntent(field=sibling, action="set", value=value, evidence=text)]
            request = _request(form, current, text, previous=previous, rules=rules, number=turn)
            fields, trace = _run(request)
            assert field not in trace["previous_rejected"], (turn, trace)
            result = fields[field]
            assert (result.status, result.action) == ("valid", "set"), (turn, trace)
            assert result.value == expected.value
            assert result.evidence == expected.evidence
            assert result.source == expected.source
            assert result.origin_request_id == expected.origin_request_id
            assert result.value_source == "derived"
            assert result.correction == expected.correction
            if rules:
                assert (fields[sibling].status, fields[sibling].value) == ("valid", value)
        previous = _carry(request, result)
        assert previous.baselines[field] == actual
        assert current[field] == actual
    assert len(previous.text.splitlines()) == 18
    assert len(previous.text) <= 1000
    assert previous.fields[0].value == [
        _day(12) if value == _day(9) else value
        for value in (values if seed == "proposed" else actual)
    ]
    # 마지막 요청의 이전 후보까지 실제로 재검증한다.
    final = _request(form, current, f"{name}은 그대로", previous=previous, number=19)
    checked, rejected = review_previous(final, _context(form))
    assert checked is not None, rejected
    assert checked.fields[0].correction == expected.correction
    assert checked.fields[0].value == expected.value
    assert checked.sources[field] == expected.source
    assert checked.origins[field] == expected.origin_request_id
    # 동일한 장기 원문에 뒤늦은 철회·미정·실패한 교체가 있으면 이전 후보를 되살리지 않는다.
    for suffix in (
        f"{name} 변경은 취소합니다.",
        f"{name}은 미정입니다.",
        f"{name} 12일 대신 32일로 변경해 주세요.",
    ):
        poisoned = previous.model_copy(update={"text": previous.text + "\n" + suffix})
        assert len(poisoned.text) <= 1000
        request = _request(form, current, f"{name}은 그대로", previous=poisoned, number=20)
        rejected_candidate, reasons = review_previous(request, _context(form))
        assert rejected_candidate is None and field in reasons, suffix
        fields, _ = _run(request)
        assert not (fields[field].status == "valid" and fields[field].action == "set"), suffix


def _valid_history(form):
    field, current, name, _ = _setup(form)
    first, candidate = _move(form, current, name, 9, 10)
    previous = _carry(first, candidate)
    second, candidate = _move(form, current, name, 10, 11, previous=previous, number=2)
    previous = _carry(second, candidate)
    request = _request(form, current, f"{name}은 그대로", previous=previous, number=3)
    checked, rejected = review_previous(request, _context(form))
    assert checked is not None and not rejected, rejected
    return field, current, name, previous


@pytest.mark.parametrize("form", ["dates", "period"])
@pytest.mark.parametrize(
    "suffix",
    [
        "{name} 변경은 취소합니다.",
        "그 요청은 취소할게요.",
        "{name}은 현재 폼의 값 그대로 유지해 주세요.",
        "{name}은 미정입니다.",
        "{name} 11일 대신 12일로 변경할까요?",
        "{name} 11일 대신 32일로 변경해 주세요.",
        "{name} 11일 대신 12일로 변경해 주세요.",
    ],
)
def test_later_cancel_or_edit_cannot_revive_old_derived_candidate(form, suffix):
    field, current, name, previous = _valid_history(form)
    poisoned = previous.model_copy(update={"text": previous.text + "\n" + suffix.format(name=name)})
    data = _request(form, current, f"{name}은 그대로", previous=poisoned, number=4)
    checked, rejected = review_previous(data, _context(form))
    assert checked is None and field in rejected
    result, _ = _run(data)
    assert not (result[field].status == "valid" and result[field].action == "set")


@pytest.mark.parametrize("form", ["dates", "period"])
@pytest.mark.parametrize(
    "damage",
    [
        "stale",
        "missing_relation",
        "wrong_old",
        "wrong_accepted",
        "wrong_kind",
        "missing_seed",
        "wrong_value",
    ],
)
def test_derived_history_rejects_invalid_proof(form, damage):
    field, current, name, previous = _valid_history(form)
    original = previous.fields[0]
    if damage == "stale":
        current = {**current, field: [_day(13), *current[field][1:]]}
    elif damage == "missing_relation":
        original = original.model_copy(update={"correction": None})
    elif damage == "wrong_old":
        original = original.model_copy(
            update={
                "correction": original.correction.model_copy(
                    update={"rejected_value": current[field]}
                )
            }
        )
    elif damage == "wrong_accepted":
        original = original.model_copy(
            update={
                "correction": original.correction.model_copy(update={"accepted_evidence": "31일"})
            }
        )
    elif damage == "wrong_kind":
        original = original.model_copy(
            update={"correction": original.correction.model_copy(update={"kind": "ambiguous"})}
        )
    elif damage == "missing_seed":
        previous = previous.model_copy(update={"text": previous.text.splitlines()[-1]})
    else:
        original = original.model_copy(update={"value": [_day(14), *original.value[1:]]})
    previous = previous.model_copy(update={"fields": [original]})
    request = _request(form, current, f"{name}은 그대로", previous=previous, number=4)
    checked, rejected = review_previous(request, _context(form))
    assert checked is None and field in rejected
    if damage == "stale":
        assert rejected[field] == "stale_context"
    result, _ = _run(request)
    assert not (result[field].status == "valid" and result[field].action == "set")


@pytest.mark.parametrize("form", ["dates", "period"])
def test_malformed_history_wire_contract_is_rejected(form):
    field, current, name, previous = _valid_history(form)
    request = _request(form, current, f"{name}은 그대로", previous=previous).model_dump()
    for updates in (
        {"text": " "},
        {"text": "가" * 1001},
        {"origins": {"not_a_field": "r3-history-2"}},
        {"baselines": {field: ["2026-02-31"]}},
    ):
        broken = {**request, "previous_candidate": {**request["previous_candidate"], **updates}}
        with pytest.raises(ValidationError):
            AssistRequest.model_validate(broken)
