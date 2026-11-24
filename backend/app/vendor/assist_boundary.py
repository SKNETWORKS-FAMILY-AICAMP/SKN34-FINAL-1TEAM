"""기간 종료일 허용 문구와 이전 종료일 유지 지시의 좁은 원문 근거."""

import re
from datetime import date

from app.vendor.assist_normalize import date_tokens
from app.vendor.assist_schemas import AssistContext

# 마감 날짜와 일정 잡기 허용이 한 문장으로 확정되어야 한다.
_END_PERMISSION = re.compile(
    r"(?:네[, ]*|예[, ]*|그럼[, ]*|그러면[, ]*|이번엔[, ]*|이번에는[, ]*)?"
    r"(?:(?:[1-9]|1[0-2])월 )?"
    r"(?P<day>[1-9]|[12]\d|3[01])일까지(?:는|도)? "
    r"(?:(?:공사|작업|시공|현장) )?"
    r"(?:일정|스케줄)(?:을|은|도|를)? "
    r"(?:잡아도|잡으셔도) (?:돼요|됩니다|괜찮아요|괜찮습니다)[.!]?"
)
_END_SCOPE = re.compile(r"(?<![가-힣])(?:종료\s*(?:일|날짜)|끝(?:날)?|마지막\s*(?:날짜|날))")
_END_PRIOR = re.compile(
    r"(?<![가-힣])(?:종료\s*(?:일|날짜)|끝(?:날)?|마지막\s*(?:날짜|날))"
    r"(?:(?:은|는|도|을|를)\s*|\s+)"
    r"(?:아까|방금|앞서|이전에|이전|지난번(?:에)?|조금\s*전)\s*"
    r"(?:말한|말씀드린|정한|제안한|얘기한|확정한)\s*"
    r"(?:그대로|대로)\s*"
    r"(?:하고|해\s*주세요|유지(?:해\s*주세요)?|부탁(?:합니다|드려요|해요))"
)
_END_UNDECIDED = re.compile(
    r"(?:[?？]|모르|미정|아직|고민|싶|것\s*같|일까(?!지)|까요|겠죠|"
    r"어떨|가정|만약|혹시|아니면)"
)
_END_REVOKED = re.compile(r"(?:취소|철회|말고|아니라|아니고|하지\s*마|안\s*돼|거둘|무효)")
_OTHER_DEADLINE = re.compile(r"(?<!\d)\d{1,2}\s*일\s*(?:까지|로)")
_UNRELATED_DATE = re.compile(r"(?:시작|첫날|제외|빼|휴무|쉬는\s*날|안\s*되는\s*날)")
_OTHER_FIELD_SCOPE = re.compile(
    r"(?:시작|첫날|제외|빼는\s*날|쉬는\s*날|휴무)"
    r"|(?<!\d)(?:[1-9]|[12]\d|3[01])\s*일\s*(?:하루만\s*|하루\s*|만\s*)?"
    r"(?:빼|제외)"
)


def affirmative_end_deadline(text: str, context: AssistContext) -> tuple[str, date] | None:
    """서로 다른 날이나 추측이 없는 단일 마감 허용 문구만 통과한다."""
    simplified = " ".join(text.split())
    matched = _END_PERMISSION.fullmatch(simplified)
    if not matched:
        return None
    tokens = date_tokens(text, context)
    if len(tokens) != 1 or tokens[0][2].day != int(matched["day"]):
        return None
    return text.strip(), tokens[0][2]


def explicit_prior_end_reference(text: str, context: AssistContext) -> str | None:
    """특정 종료일을 이전 제안대로 확정한 단일 참조만 인정한다."""
    matches = list(_END_PRIOR.finditer(text))
    if (
        len(matches) != 1
        or len(list(_END_SCOPE.finditer(text))) != 1
        or _END_UNDECIDED.search(text)
    ):
        return None
    matched = matches[0]
    if _END_REVOKED.search(text[matched.start() :]):
        return None
    before = text[: matched.start()].strip()
    if before and not _OTHER_FIELD_SCOPE.search(before):
        return None
    after = text[matched.end() :].strip()
    # '해 주세요라고 썼지만' 같은 과거 인용은 현재 유지 지시가 아니다.
    if after.strip(".! ") and not (
        matched.group().rstrip().endswith("하고") and _OTHER_FIELD_SCOPE.search(after)
    ):
        return None
    # 같은 문장에 다른 날짜가 있으면, 별도 시작일·제외일임이 명시된 경우만 허용한다.
    for start, end, _ in date_tokens(text, context):
        clause_start = max(
            text.rfind(separator, 0, start) + 1 for separator in (",", ";", ".", "\n")
        )
        clause_end = min(
            (at for separator in (",", ";", ".", "\n") if (at := text.find(separator, end)) >= 0),
            default=len(text),
        )
        clause = text[clause_start:clause_end]
        if not _UNRELATED_DATE.search(clause):
            return None
    if _OTHER_DEADLINE.search(text[: matched.start()]) or _OTHER_DEADLINE.search(
        text[matched.end() :]
    ):
        return None
    return matched.group()
