"""R5 스트레스의 긴 공백에서 유지 구문 탐색과 원문 위치를 보존한다."""

import re
import time
from dataclasses import replace

import pytest

from app.vendor.assist_intent import intent_clauses, retention_scopes

_FORMS = [
    ("quote", "금액", "510만원으로 정합니다", "amount"),
    ("period", "종료일", "12월 20일로 정합니다", "end"),
    ("dates", "인원", "3명으로 정합니다", "crew"),
    ("extra", "항목명", "바닥 보수로 정합니다", "title"),
]


@pytest.mark.parametrize("form,label,assignment,field", _FORMS)
@pytest.mark.parametrize("length", [999, 1000])
def test_long_non_retention_assignment_does_not_backtrack(form, label, assignment, field, length):
    prefix = label + "은"
    text = prefix + " " * (length - len(prefix) - len(assignment)) + assignment
    assert len(text) == length
    start = time.perf_counter()
    assert retention_scopes(form, text) == ()
    parts = intent_clauses(form, text)
    # 원문의 직접 요청은 유지 요청으로 잘못 변환하지 않는다.
    assert len(parts) == 1 and parts[0].fields == {field}
    assert parts[0].start == 0 and parts[0].end == length
    # 동결 입력에서 기존 구현은 수초 이상 멈췄다. 넉넉한 상한으로
    # 기기 차이를 허용하되 같은 유형의 백트래킹은 검출한다.
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize("form,label,assignment,field", _FORMS)
@pytest.mark.parametrize("separator", [" ", "\t", "\n", " \n\t"])
def test_padded_retention_uses_original_offsets_and_newline_boundaries(
    form, label, assignment, field, separator
):
    first = label + "은 " + assignment
    lead = label + "은"
    tail = "이전 제안 유지"
    short = first + "\n" + lead + " " + tail
    old = retention_scopes(form, short)
    assert len(old) == 1
    gap = (separator * 1000)[: 1000 - len(first) - 1 - len(lead) - len(tail)]
    text = first + "\n" + lead + gap + tail
    assert len(text) == 1000
    scopes = retention_scopes(form, text)
    expected = replace(
        old[0],
        predicate_start=old[0].predicate_start + len(gap) - 1,
        end=old[0].end + len(gap) - 1,
    )
    assert scopes == (expected,)
    scope = scopes[0]
    assert scope.fields == {field} and scope.reference == "previous"
    assert text[scope.predicate_start : scope.end] == tail
    assert re.sub(r"\s+", " ", text[scope.start : scope.end]) == lead + " " + tail
    assert all(0 <= part.start < part.end <= len(text) for part in intent_clauses(form, text))
