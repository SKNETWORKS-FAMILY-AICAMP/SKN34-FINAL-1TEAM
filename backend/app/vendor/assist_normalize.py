"""업체 도우미 숫자·날짜 근거 정규화. 추출 토큰은 원문 위치를 보존한다.

날짜 목록은 열거된 날짜만, 범위는 두 끝날이 확인된 경우에만 확장한다.
모호한 월·연도와 기간 일수를 날짜로 추정하지 않는다.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from app.calc.schedule import JOB_MAX_DAYS

if TYPE_CHECKING:
    from app.vendor.assist_schemas import AssistContext


_FULL = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[-/.])\s*(\d{1,2})\s*"
    r"(?:월|[-/.])\s*(\d{1,2})\s*일?(?!\d)"
)
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*(?:월|[-/.])\s*(\d{1,2})\s*일?(?!\d)")
_BARE_DAY = re.compile(
    r"(?<!\d)([1-9]|[12]\d|3[01])\s*일"
    r"(?!\s*(?:간|동안|정도|째|걸|소요|후|전|차|뒤))"
)
# 단독 숫자는 앞선 명시적 월로 이어진 날짜 목록의 *중간* 항목으로만 인정한다.
_SHORT_DAY = re.compile(r"(?<![\d/.월\-])([1-9]|[12]\d|3[01])(?![\d/.\-가-힣])")
_LIST_GAP = re.compile(r"^\s*(?:까지\s*)?(?:(?:[,，、·]|과|와|랑|및|하고|그리고)\s*)+$")
_RANGE_GAP = re.compile(r"^\s*(?:부터|에서|~|∼|〜|－|—|–|-|\.{2}|to)\s*$", re.IGNORECASE)
_CREW = re.compile(r"(?<![가-힣\d])(?:(\d{1,2})|(한|두|세|네))\s*(?:명|인)(?!\d)")
_CREW_RANGE = re.compile(
    r"(?:\d{1,2}|한|두|세|네)\s*(?:명|인)\s*[~\-–∼]\s*"
    r"(?:\d{1,2}|한|두|세|네)\s*(?:명|인)"
    r"|(?:\d{1,2}|한|두|세|네)\s*[~\-–∼]\s*(?:\d{1,2}|한|두|세|네)\s*(?:명|인)"
    r"|(?:한|두|세|네)\s*(?:한|두|세|네)\s*(?:명|인)"
)
_NATIVE_CREW = {"한": 1, "두": 2, "세": 3, "네": 4}


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    day: int
    value: date | None
    kind: str  # 명시적 날짜, 'N일', 목록 중간의 숫자


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _infer_year(month: int, day: int, context: "AssistContext") -> int:
    """기존 검증기의 연도 추정 정책을 그대로 사용한다."""
    anchor = context.base_month or context.today
    if context.base_month is None:
        candidate = _date(anchor.year, month, day)
        return (
            anchor.year if candidate is not None and candidate >= context.today else anchor.year + 1
        )
    candidates = [
        found
        for year in (anchor.year - 1, anchor.year, anchor.year + 1)
        if (found := _date(year, month, day)) is not None
    ]
    return (
        min(candidates, key=lambda value: abs((value - anchor).days)).year
        if candidates
        else anchor.year
    )


def _link(gap: str) -> str | None:
    if _LIST_GAP.fullmatch(gap):
        return "list"
    if _RANGE_GAP.fullmatch(gap):
        return "range"
    return None


def date_tokens(
    evidence: str, context: "AssistContext", *, allow_bare: bool = True
) -> list[tuple[int, int, date]]:
    """원문 [start:end]에 대응하는 날짜 토큰을 반환한다.

    생략한 월은 연결된 *앞의 명시적 날짜*에서만 상속한다.
    양쪽 명시 월이 다르면 해당 생략 날짜는 보류한다. 독립적인 'N일'은
    기존 계약처럼 base_month가 있는 경우에만 해석한다.
    """
    tokens: list[_Token] = []
    occupied: list[tuple[int, int]] = []

    def add(start: int, end: int, day: int, value: date | None, kind: str) -> None:
        if any(start < b and end > a for a, b in occupied):
            return
        occupied.append((start, end))  # 잘못된 명시 날짜도 부분 토큰으로 다시 읽지 않는다.
        tokens.append(_Token(start, end, day, value, kind))

    for match in _FULL.finditer(evidence):
        y, m, d = (int(group) for group in match.groups())
        add(match.start(), match.end(), d, _date(y, m, d), "explicit")
    for match in _MONTH_DAY.finditer(evidence):
        m, d = (int(group) for group in match.groups())
        add(match.start(), match.end(), d, _date(_infer_year(m, d, context), m, d), "explicit")

    if allow_bare:
        for match in _BARE_DAY.finditer(evidence):
            add(match.start(), match.end(), int(match[1]), None, "bare")
        for match in _SHORT_DAY.finditer(evidence):
            add(match.start(), match.end(), int(match[1]), None, "short")

    tokens.sort(key=lambda token: token.start)
    links = [_link(evidence[left.end : right.start]) for left, right in pairwise(tokens)]

    def nearest_explicit(index: int, direction: int) -> _Token | None:
        cursor = index
        while 0 <= cursor + direction < len(tokens):
            edge = cursor if direction > 0 else cursor - 1
            if links[edge] is None:
                break
            cursor += direction
            if tokens[cursor].kind == "explicit":
                return tokens[cursor]
        return None

    found: list[tuple[int, int, date]] = []
    for i, token in enumerate(tokens):
        if token.kind == "explicit":
            if token.value is not None:
                found.append((token.start, token.end, token.value))
            continue

        left = nearest_explicit(i, -1)
        right = nearest_explicit(i, 1)
        # 단독 숫자는 목록 중간에서만 사용한다. 인원·금액 등의 숫자를 날짜로 빌리지 않는다.
        if token.kind == "short" and (
            left is None
            or i == 0
            or i == len(tokens) - 1
            or links[i - 1] != "list"
            or links[i] != "list"
        ):
            continue
        if left and left.value is None or right and right.value is None:
            continue
        if (
            left
            and right
            and left.value
            and right.value
            and (left.value.year, left.value.month) != (right.value.year, right.value.month)
        ):
            continue

        # '12/8부터 3일'은 기간일 수 있다. '~9일'은 명시적 범위다.
        if (
            token.kind == "bare"
            and i > 0
            and evidence[tokens[i - 1].end : token.start].strip() in ("부터", "에서")
            and not evidence[token.end :].lstrip().startswith("까지")
        ):
            continue

        anchor = left.value if left else None
        if anchor is None and token.kind == "bare" and context.base_month is not None:
            # 같은 원문에 명시 월이 있는데 문맥 연결을 확인 못한 날은
            # 기준월로 덮어 해석하지 않는다('12/11,32,13일' 등).
            if any(item.kind == "explicit" for item in tokens):
                continue
            base = context.base_month
            anchor = date(base.year, base.month, 1)
        if anchor is None:
            continue
        value = _date(anchor.year, anchor.month, token.day)
        if value is not None:
            found.append((token.start, token.end, value))
    return found


def date_mentions(
    evidence: str, context: "AssistContext", *, expand: bool, allow_bare: bool = True
) -> set[date]:
    """원문 날짜 집합. 목록은 열거만, 명시적 범위만 최대 JOB_MAX_DAYS일 확장한다."""
    tokens = date_tokens(evidence, context, allow_bare=allow_bare)
    found = {value for _, _, value in tokens}
    if expand:
        for left, right in pairwise(tokens):
            if _link(evidence[left[1] : right[0]]) != "range":
                continue
            span = (right[2] - left[2]).days
            if 0 <= span <= JOB_MAX_DAYS:
                found.update(left[2] + timedelta(days=n) for n in range(span + 1))
    return found


def crew_mentions(evidence: str) -> set[int]:
    """'네 명' 등 인원 단위가 있는 수량만 반환한다. 범위·'두세 명'은 보류한다."""
    uncertain = [(m.start(), m.end()) for m in _CREW_RANGE.finditer(evidence)]
    values: set[int] = set()
    for match in _CREW.finditer(evidence):
        if any(match.start() < b and match.end() > a for a, b in uncertain):
            continue
        values.add(int(match[1]) if match[1] else _NATIVE_CREW[match[2]])
    return values
