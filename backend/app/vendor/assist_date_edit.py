"""검증된 이전 날짜 목록에서 명시된 하루만 교체한다.

이 모듈은 후보의 값만 만든다. ``prior``가 원문·기준값 검사를 통과했는지,
출처와 원요청 ID가 무엇인지는 호출자가 판단한다. 기간 경계와의 결합 검사는
기존 최종 후보 검증기에 맡긴다.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

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
_EDIT_GAP = re.compile(r"^(?:을|를|은|는|에서|(?:을|를|은|는)?만)$")
_EDIT_SUFFIX = re.compile(
    r"^\s*(?:로|으로)\s*(?:바꾸|바꿔|바꿀|고치|고쳐|고칠|변경|수정|정정|교체|대체|옮기)"
)
_DIRECTION = frozenset(("->", "→"))
_CONFIRMED_ENDINGS = frozenset(
    (
        "넣을게요",
        "넣겠습니다",
        "넣어주세요",
        "작업합니다",
        "작업하겠습니다",
        "고쳐주세요",
        "고칠게요",
        "바꿔주세요",
        "바꿀게요",
        "옮겨주세요",
        *(
            verb + ending
            for verb in ("수정", "정정", "변경", "교체", "대체")
            for ending in ("", "해요", "합니다", "해줘", "해주세요", "할게요", "하겠습니다")
        ),
    )
)


def _confirmed_suffix(text: str) -> bool:
    """교체 절의 끝이 확정 지시인지 확인한다. 문의·인용·조건부 요청은 보류한다."""
    clause = re.split(r"[.,;。!，\n]", text, maxsplit=1)[0]
    ending = "".join(clause.split())
    if not ending:
        return True
    for particle in ("으로", "로", "을", "를", "에"):
        if ending.startswith(particle):
            ending = ending[len(particle) :]
            break
    return ending in _CONFIRMED_ENDINGS


_EDIT_WORD = (
    r"바꾸|바꿔|바꿀|고치|고쳐|고칠|변경|수정|정정|교체|대체|"
    r"옮기|추가|넣|삭제|지우|비우|초기화|늘리|줄이|조정"
)
_OTHER_EDIT = re.compile(_EDIT_WORD)
_FOLLOWING_EDIT = re.compile(rf"^\s*(?:로|으로|을|를|만)?\s*(?P<verb>{_EDIT_WORD})")
_UNCHANGED_EDIT = re.compile(r"^변경\s*없")
_KEEP = re.compile(
    r"^\s*(?:은|는|이|가|을|를|도|만)?\s*"
    r"(?:그대로|유지|기존대로|원래대로|변함\s*없|변경\s*없|동일|같(?:게|아요|습니다))"
)
_POST_BOUNDARY_KEEP = re.compile(
    r"^\s*(?:은|는|이|가)?\s*"
    r"(?:시작\s*(?:일|날짜?)|종료\s*(?:일|날짜?)|끝나는\s*날)"
    r"\s*(?:은|는|이|가)?\s*"
    r"(?:그대로|유지|기존대로|원래대로|변함\s*없|변경\s*없|건드리지\s*마|손대지\s*마)"
)
_KEEP_JOIN = re.compile(r"^\s*(?:,|，|과|와|랑|하고|및)\s*$")
_CLAUSE_BREAK = re.compile(r"[,，.;。!?！？\n]|그대로\s*두고|유지하고")
# 본 파서가 누락하는 0일/32일 같은 날짜 표기가 문장 안에 있으면 보류한다.
_DAY_SHAPE = re.compile(
    r"(?<![\d/.-])\d{1,3}\s*일"
    r"(?!\s*(?:간|동안|정도|째|걸|소요|후|전|차|뒤))"
)
_MONTH_SHAPE = re.compile(r"(?<!\d)\d{1,2}\s*월")
_CREW_REPLACEMENT = re.compile(r"\d+\s*명\s*에서\s*\d+\s*명")
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
    r"아니|아닌|아닐|아님|아닙니다|취소|하지\s*(?:마|않|말)|안\s*(?:갈|가)|철회)"
)
_EXCLUDED_SCOPE = re.compile(
    r"제외\s*(?:(?:할|하는)\s*)?(?:일|날짜?|날|할\s*날)|"
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


def _local_prefix(text: str, start: int) -> str:
    """완결된 앞 절의 필드 표기가 뒤 정정에 넘어가지 않도록 한다."""
    prefix = text[:start]
    breaks = list(_CLAUSE_BREAK.finditer(prefix))
    return prefix[breaks[-1].end() :] if breaks else prefix


def _unique_prior_mention(mention: _Mention, before: list[date]) -> bool:
    return (
        sum(
            value.day == mention.day
            and (mention.month is None or value.month == mention.month)
            and (mention.year is None or value.year == mention.year)
            for value in before
        )
        == 1
    )


def _retained_elsewhere(
    text: str,
    mentions: list[_Mention],
    replaced: set[int],
    before: list[date],
    context: AssistContext,
) -> bool:
    """교체 외의 날짜는 분명하게 '유지/그대로'라고 적힌 경우만 허용한다."""
    remaining = [index for index in range(len(mentions)) if index not in replaced]
    for position, index in enumerate(remaining):
        mention = mentions[index]
        # 시작·종료일은 다른 필드라 prior에 없어도 되지만 날짜 자체는 유효해야 한다.
        if mention.month is not None or mention.year is not None:
            try:
                date(
                    mention.year or (context.base_month or before[0]).year,
                    mention.month or (context.base_month or before[0]).month,
                    mention.day,
                )
            except ValueError:
                return False
        following = text[mention.end :]
        other_scope = bool(
            _OTHER_SCOPE.search(_local_prefix(text, mention.start))
            or _POST_BOUNDARY_KEEP.match(following)
        )
        if not other_scope and not _unique_prior_mention(mention, before):
            return False
        if _KEEP.match(following) or _POST_BOUNDARY_KEEP.match(following):
            continue
        # '8일과 10일은 그대로'처럼 여러 기존 원소를 한 번에 유지할 수 있다.
        if position + 1 >= len(remaining):
            return False
        next_index = remaining[position + 1]
        if next_index != index + 1 or not _KEEP_JOIN.fullmatch(
            text[mention.end : mentions[next_index].start]
        ):
            return False
    return True


def _single_replacement(text: str, mentions: list[_Mention], field: str) -> tuple[int, int] | None:
    """연속된 두 날짜 사이에서 분명한 교체 관계 하나만 찾는다."""
    candidates: list[tuple[int, int]] = []
    for index, (old, new) in enumerate(pairwise(mentions)):
        gap = re.sub(r"\s+", "", text[old.end : new.start])
        suffix = text[new.end :]
        if (
            _REPLACE_GAP.fullmatch(gap)
            or _EDIT_GAP.fullmatch(gap)
            and _EDIT_SUFFIX.match(suffix)
            or gap in _DIRECTION
        ):
            candidates.append((index, index + 1))
    if len(candidates) != 1:
        return None
    old_index, new_index = candidates[0]
    if not _targeted(field, _local_prefix(text, mentions[old_index].start)):
        return None
    return old_index, new_index


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
    field: str,
    text: str,
    prior: list[str] | None,
    context: AssistContext,
    *,
    require_affirmative: bool = False,
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
    if len(mentions) < 2:
        return None
    # 누락된 잘못된 날짜, 미확인 월·날짜 표기 및 독립된 인원 변경은 보류한다.
    if any(
        not any(item.start <= shape.start() and shape.end() <= item.end for item in mentions)
        for regex in (_DAY_SHAPE, _MONTH_SHAPE)
        for shape in regex.finditer(text)
    ) or _CREW_REPLACEMENT.search(text):
        return None
    if len(re.findall(r"대신|말고|->|→", text)) > 1:
        return None
    pair = _single_replacement(text, mentions, field)
    if pair is None:
        return None
    old, new = (mentions[index] for index in pair)
    if require_affirmative and not _confirmed_suffix(text[new.end :]):
        return None
    if not _retained_elsewhere(text, mentions, set(pair), before, context):
        return None
    # 다른 절의 변경 지시가 새 날짜를 몰래 바꾸지 못하게 한다.
    following = _FOLLOWING_EDIT.match(text[new.end :])
    allowed_action = new.end + following.start("verb") if following else None
    if any(
        action.start() != allowed_action
        and not (action.group() == "변경" and _UNCHANGED_EDIT.match(text[action.start() :]))
        for action in _OTHER_EDIT.finditer(text)
    ):
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
