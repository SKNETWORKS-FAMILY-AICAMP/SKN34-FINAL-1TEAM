"""기간 업무 검증 계층의 독립성 및 실제 기준값 재투영."""

from copy import deepcopy
from datetime import date
from itertools import product

import pytest

from app.vendor.assist_period_constraints import (
    period_state,
    period_violations,
    resolve_period_candidates,
)
from app.vendor.assist_schemas import CandidateField

TODAY = date(2026, 12, 1)
CURRENT = {
    "start": "2027-01-06",
    "end": "2027-01-29",
    "excluded": ["2027-01-14", "2027-01-22"],
}


def candidate(field, value, *, retained=False, derived=False):
    return CandidateField(
        field=field,
        action="set" if value is not None else "clear",
        value=value,
        evidence="verified candidate",
        source="llm",
        status="valid",
        alternatives=[],
        origin_request_id="prior" if retained else "now",
        intent_kind="retain_previous" if retained else "explicit_change",
        value_source="derived" if derived else "previous" if retained else "utterance",
    )


@pytest.mark.parametrize(
    "start,end,survivor",
    [("2027-01-07", "2027-01-20", "start"), ("2027-01-16", "2027-01-28", "end")],
)
@pytest.mark.parametrize("strict", [False, True])
def test_actual_exclusion_only_blames_violating_boundary(start, end, survivor, strict):
    fields = {"start": candidate("start", start), "end": candidate("end", end)}
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY, strict_interior=strict)
    assert set(kept) == {survivor}
    assert rejected == set(fields) - {survivor}
    assert kept[survivor] is fields[survivor]
    assert not period_violations(period_state(CURRENT, kept), TODAY)


def test_boundary_equality_is_only_allowed_in_preliminary_review():
    fields = {
        "start": candidate("start", "2027-01-14"),
        "end": candidate("end", "2027-01-28"),
    }
    preview, rejected = resolve_period_candidates(CURRENT, fields, TODAY, strict_interior=False)
    assert preview == fields and not rejected
    final, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert set(final) == {"end"} and rejected == {"start"}


@pytest.mark.parametrize("derived", [False, True])
def test_verified_old_list_has_priority_over_incompatible_new_edge(derived):
    prior = candidate("excluded", ["2027-01-14", "2027-01-24"], retained=True, derived=derived)
    fields = {
        "start": candidate("start", "2027-01-07"),
        "end": candidate("end", "2027-01-23"),
        "excluded": prior,
    }
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert rejected == {"end"}
    assert kept["excluded"] is prior
    assert kept["start"] is fields["start"]


def test_reprojection_uses_actual_list_after_rejecting_new_list():
    fields = {
        "start": candidate("start", "2027-01-07"),
        "end": candidate("end", "2027-01-20"),
        "excluded": candidate("excluded", ["2027-01-15", "2027-01-15"]),
    }
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert set(kept) == {"start"}
    assert rejected == {"end", "excluded"}
    assert period_state(CURRENT, kept)["excluded"] == CURRENT["excluded"]


def test_competing_new_exclusion_and_boundary_never_arbitrarily_select_either():
    fields = {
        "start": candidate("start", "2027-01-07"),
        "end": candidate("end", "2027-01-25"),
        "excluded": candidate("excluded", ["2027-01-27"]),
    }
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert set(kept) == {"start"}
    assert rejected == {"end", "excluded"}


def test_valid_coupled_changes_are_not_rejected_against_each_old_boundary():
    fields = {
        "start": candidate("start", "2027-02-04"),
        "end": candidate("end", "2027-02-17"),
        "excluded": candidate("excluded", ["2027-02-09"]),
    }
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert kept == fields and not rejected


@pytest.mark.parametrize("edge,day", [("start", "2027-01-18"), ("end", "2027-01-18")])
def test_new_boundary_equal_to_new_exclusion_requires_clarification(edge, day):
    opposite = "end" if edge == "start" else "start"
    independent_value = "2027-01-28" if opposite == "end" else "2027-01-07"
    fields = {
        edge: candidate(edge, day),
        opposite: candidate(opposite, independent_value),
        "excluded": candidate("excluded", [day]),
    }
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert set(kept) == {opposite}
    assert rejected == {edge, "excluded"}


def test_invalid_edge_pair_rejected_before_independent_prior_list():
    prior = candidate("excluded", ["2027-01-12"], retained=True)
    fields = {
        "start": candidate("start", "2027-01-25"),
        "end": candidate("end", "2027-01-10"),
        "excluded": prior,
    }
    kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
    assert kept == {"excluded": prior}
    assert rejected == {"start", "end"}


def _strict_oracle(state):
    start, end = (date.fromisoformat(state[k]) if state.get(k) else None for k in ("start", "end"))
    days = state.get("excluded") or []
    if end and not start or start and start <= TODAY:
        return False
    if start and end and not 0 <= (end - start).days <= 62:
        return False
    if len(days) > 31 or len(days) != len(set(days)):
        return False
    return not days or bool(
        start and end and all(start < date.fromisoformat(d) < end for d in days)
    )


def test_constraint_resolution_is_order_independent_and_preserves_inputs():
    """유한 달력 대조 조합을 독립 oracle로 검사한다."""
    original = deepcopy(CURRENT)
    for start, end, days, old_days in product(
        ["2026-12-01", "2027-01-07", "2027-01-14", "2027-01-25", "2027-02-06"],
        ["2027-01-10", "2027-01-22", "2027-01-28", "2027-04-10"],
        [None, [], ["2027-01-14"], ["2027-01-27"], ["2027-01-14", "2027-01-14"]],
        [False, True],
    ):
        fields = {
            "start": candidate("start", start),
            "end": candidate("end", end),
            "excluded": candidate("excluded", days, retained=old_days),
        }
        before = deepcopy(fields)
        kept, rejected = resolve_period_candidates(CURRENT, fields, TODAY)
        assert (kept, rejected) == resolve_period_candidates(
            CURRENT, dict(reversed(tuple(fields.items()))), TODAY
        )
        assert set(kept) | rejected == set(fields)
        assert not set(kept) & rejected
        assert _strict_oracle(period_state(CURRENT, kept))
        assert all(value is fields[key] for key, value in kept.items())
        assert fields == before and CURRENT == original
