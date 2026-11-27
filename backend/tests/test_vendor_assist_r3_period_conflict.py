"""R3: 독립 생산자에서 유효했던 기간 대안의 최종 조합 검증."""

from datetime import date
from uuid import UUID

import pytest

from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent
from app.vendor.assist_validation import merge_candidates, review_fields


@pytest.mark.parametrize("start_day,conflict_valid", [(20, False), (5, True)])
def test_period_conflict_alternatives_must_fit_merged_projection(start_day, conflict_valid):
    context = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))
    current = {"start": "2026-12-02", "end": "2026-12-30", "excluded": []}
    request = AssistRequest(
        request_id="r3-conflict",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form="period",
        text=f"종료일 12월 10일 또는 12월 25일로 변경, 시작일 12월 {start_day}일",
        current=current,
        previous_candidate=None,
        rule_fields=[],
    )
    rule, _ = review_fields(
        [FieldIntent(field="end", action="set", value="2026-12-10", evidence="12월 10일")],
        "rule",
        request,
        context,
    )
    model, _ = review_fields(
        [
            FieldIntent(field="end", action="set", value="2026-12-25", evidence="12월 25일"),
            FieldIntent(
                field="start",
                action="set",
                value=f"2026-12-{start_day:02d}",
                evidence=f"12월 {start_day}일",
            ),
        ],
        "llm",
        request,
        context,
    )
    assert rule["end"].status == model["end"].status == model["start"].status == "valid"
    fields = {item.field: item for item in merge_candidates(request, context, rule, model, None)}
    assert fields["start"].value == f"2026-12-{start_day:02d}"
    assert fields["start"].status == "valid"
    end = fields["end"]
    if conflict_valid:
        assert end.status == "conflict"
        assert {alternative.value for alternative in end.alternatives} == {
            "2026-12-10",
            "2026-12-25",
        }
    else:
        assert end.status == "uncertain"
        assert end.reason_code == "combined_constraint"
        assert not end.alternatives and end.value is None
    assert request.current == current
