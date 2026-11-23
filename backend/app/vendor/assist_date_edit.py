"""검증된 이전 날짜 목록에서 명시된 하루만 교체한다.

이 모듈은 후보의 값만 만든다. ``prior``가 원문·기준값 검사를 통과했는지,
출처와 원요청 ID가 무엇인지는 호출자가 판단한다. 기간 경계와의 결합 검사는
기존 최종 후보 검증기에 맡긴다.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.calc.schedule import JOB_MAX_DAYS
from app.vendor.assist_schemas import AssistContext

_ISO = re.compile(r"20\d{2}-\d{2}-\d{2}\Z")
_FULL = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[-/.])\s*(\d{1,2})\s*"
    r"(?:월|[-/.])\s*(\d{1,2})\s*일?(?!\d)"
)
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*(?:월|[-/.])\s*(\d{1,2})\s*일?(?!\d)")
_BARE_DAY = re.compile(
    r"(?<![\d/.-])([1-9]|[12]\d|3[01])\s*일"
    r"(?!\s*(?:간|동안|정도|째|걸|소요|후|전|차|뒤))"
)
_REPLACE_GAP = re.compile(r"^(?:은|는|이|가|을|를)?(?:대신|말고)$")
_EDIT_GAP = re.compile(r"^(?:을|를|에서)$")
_EDIT_SUFFIX = re.compile(
    r"^\s*(?:로|으로)\s*(?:바꾸|바꿔|바꿀|고치|고쳐|고칠|변경|수정|정정|교체|대체|옮기)"
)
_DIRECTION = frozenset(("->", "→"))
_UNCERTAIN = re.compile(
    r"[?？]|아마|혹시|미정|보류|불확실|모르겠|어쩌면|일단|"
    r"(?:될|할)\s*것\s*같|(?:수도|지도|정도|쯤)|"
    r"(?:인가|일까|될까|할까)요?|가능하면|생각\s*중|검토\s*중|"
    r"(?:잠정|임시|아직|예정|듯(?:해|합니다|한데))"
)
_NEGATED_EDIT = re.compile(
    r"(?:바꾸|고치|수정|변경|정정|교체|대체|옮기)"
    r"(?:지(?:는)?\s*(?:않|말|마|못)|는\s*(?:게|건|것)\s*아니|\s*안\s*(?:할|해|하))"
    r"|(?:안\s*(?:돼|되|됨|바꾸|바꿔|바꿀|고치|수정|변경|교체)|"
    r"아니에요|아니고|아니라|아닌데|아닌가요|취소|하지\s*(?:마|않|말)|안\s*(?:갈|가)|철회)"
)
_EXCLUDED_SCOPE = re.compile(
    r"제외\s*(?:(?:할|하는)\s*)?(?:일|날짜?|할\s*날)|"
    r"빼\s*(?:는|야\s*하는|야\s*할|줄)\s*날|"
    r"(?:못\s*(?:가|하는)|빠지는)\s*날|"
    r"불가\s*(?:일|날)|일정에서\s*제외"
)
_DAY_SCOPE = re.compile(r"작업\s*(?:일|날짜?)|공사\s*(?:일|날짜?)|시공\s*(?:일|날짜?)")
_OTHER_SCOPE = re.compile(r"시작\s*(?:일|날짜?)|종료\s*(?:일|날짜?)|끝나는\s*날|유효\s*기간")


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    day: int
    month: int | None
    year: int | None


def _mentions(text: str) -> list[_Mention]:
    """겹치는 날짜 표기는 가장 구체적인 토큰 한 개로만 센다."""
    found: list[_Mention] = []
    for regex, kind in ((_FULL, "full"), (_MONTH_DAY, "month"), (_BARE_DAY, "day")):
        for match in regex.finditer(text):
            if any(match.start() < item.end and match.end() > item.start for item in found):
                continue
            if kind == "full":
                year, month, day = (int(value) for value in match.groups())
            elif kind == "month":
                month, day = (int(value) for value in match.groups())
                year = None
            else:
                day = int(match[1])
                month = year = None
            found.append(_Mention(match.start(), match.end(), day, month, year))
    return sorted(found, key=lambda item: item.start)


def _targeted(field: str, prefix: str) -> bool:
    """교체의 옛 날짜 앞에서 가장 가까운 필드 언급만 유효하다."""
    marks = sorted(
        [(m.start(), "excluded") for m in _EXCLUDED_SCOPE.finditer(prefix)]
        + [(m.start(), "days") for m in _DAY_SCOPE.finditer(prefix)]
        + [(m.start(), "other") for m in _OTHER_SCOPE.finditer(prefix)]
    )
    if field == "excluded":
        return bool(marks) and marks[-1][1] == "excluded"
    return not marks or marks[-1][1] == "days"


def _verified_prior(prior: list[str] | None) -> list[date] | None:
    if not isinstance(prior, list) or not prior:
        return None
    values: list[date] = []
    for value in prior:
        if type(value) is not str or not _ISO.fullmatch(value):
            return None
        try:
            values.append(date.fromisoformat(value))
        except ValueError:
            return None
    return values if len(values) == len(set(values)) else None


def _in_context(field: str, days: list[date], context: AssistContext) -> bool:
    if not days or len(days) != len(set(days)):
        return False
    if any(day <= context.today for day in days):
        return False
    if field == "excluded":
        # 이 모듈은 period.start/end를 모른다. 포함 범위는 최종 병합 시 검사한다.
        return len(days) <= 31
    if len(days) > context.days_max or (
        context.required_days is not None and len(days) != context.required_days
    ):
        return False
    if max(days) > context.today + timedelta(days=365):
        return False
    if (max(days) - min(days)).days > JOB_MAX_DAYS:
        return False
    return not any(
        day in context.holidays or context.no_weekend and day.weekday() >= 5 for day in days
    )


def validated_date_edit(
    field: str, text: str, prior: list[str] | None, context: AssistContext
) -> list[str] | None:
    """명시된 ``옛날 → 새날`` 한 번만, 기존 전체 목록의 한 원소로 교체한다.

    ``field='days'``는 dates.days, ``'excluded'``는 period.excluded 전용이다.
    과거 후보의 baseline/evidence/source 검증은 반드시 *먼저* 호출자가 한다.
    월·연도 미기재 값은 유일하게 확인된 옛 목록 원소의 월·연도만 사용한다.
    """
    if field not in ("days", "excluded") or not isinstance(text, str):
        return None
    before = _verified_prior(prior)
    if before is None or _UNCERTAIN.search(text) or _NEGATED_EDIT.search(text):
        return None
    mentions = _mentions(text)
    # 숫자·날짜를 임의 선택하지 않는다. 다른 필드의 날짜 변경도 보류한다.
    if len(mentions) != 2 or not _targeted(field, text[: mentions[0].start]):
        return None
    old, new = mentions
    gap = re.sub(r"\s+", "", text[old.end : new.start])
    suffix = text[new.end :]
    explicit_replace = bool(_REPLACE_GAP.fullmatch(gap))
    explicit_edit = bool(_EDIT_GAP.fullmatch(gap) and _EDIT_SUFFIX.match(suffix))
    if not (explicit_replace or explicit_edit or gap in _DIRECTION):
        return None
    if len(re.findall(r"대신|말고|->|→", text)) > 1:
        return None

    matches = [
        day
        for day in before
        if day.day == old.day
        and (old.month is None or day.month == old.month)
        and (old.year is None or day.year == old.year)
    ]
    if len(matches) != 1:
        return None
    existing = matches[0]
    if new.month is not None and new.month != existing.month:
        return None
    if new.year is not None and new.year != existing.year:
        return None
    try:
        replacement = date(existing.year, existing.month, new.day)
    except ValueError:
        return None
    if replacement == existing or replacement in before:
        return None

    updated = [replacement if day == existing else day for day in before]
    if not _in_context(field, updated, context):
        return None
    return [value.isoformat() for value in updated]
