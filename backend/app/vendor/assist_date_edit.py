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
from app.vendor.assist_intent import (
    DATE_CHANGE_ACTION,
    POLITE_EXECUTION,
    intent_clauses,
    polite_execution_risk_text,
)
from app.vendor.assist_lexical import compact_whitespace
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
_EDIT_SUFFIX = re.compile(rf"^\s*(?:로|으로)\s*(?:{DATE_CHANGE_ACTION})")
_EXCLUSION_MOVE_GAP = re.compile(r"^(?:은|는|에)?쉬는(?:걸|것을)(?:만)?$")
_EXCLUSION_MOVE_SUFFIX = re.compile(r"^\s*쉬는\s*(?:걸|것으)로\s*(?:옮기|옮겨|옮길)")
# 이유를 설명한 바로 다음 절에서 그날의 작업만 옮기는 지시.
_ANAPHOR_MOVE_GAP = re.compile(
    r"^\s*(?:에|은|는)[^,.!?！？;。\n]+[.!。]\s*"
    r"(?:그\s*날|해당\s*일|그\s*날짜)의?\s*(?:작업|공사|시공)(?:만|을|를)\s*$"
)
_SHIFT_GAP = re.compile(r"^(?:은|는)?만?하루밀려서$")
_DIRECTION = frozenset(("->", "→"))
_KEEP_OTHERS_TRAILER = re.compile(
    r"(?:나머지|다른)(?:작업일|공사일|날짜|날|휴무일|휴무)?(?:은|는|도)?"
    r"(?:그대로|유지)(?:요|예요|입니다)?$|나머진(?:그대로|유지)(?:요|예요|입니다)?$"
)
_CONFIRMED_ENDINGS = frozenset(
    (
        "넣을게요",
        "넣겠습니다",
        "넣어주세요",
        "작업합니다",
        "작업하겠습니다",
        "고쳐주세요",
        "고칠게요",
        "고쳐요",
        "바꿔주세요",
        "바꿀게요",
        "바꿔요",
        "옮겨주세요",
        "옮기죠",
        "옮길게요",
        "옮기겠습니다",
        "미뤄주세요",
        "미룰게요",
        "미루겠습니다",
        "당겨주세요",
        "당길게요",
        "당기겠습니다",
        *(
            verb + ending
            for verb in ("수정", "정정", "변경", "교체", "대체", "이동")
            for ending in (
                "",
                "해요",
                "합니다",
                "해줘",
                "해주세요",
                "할게요",
                "하겠습니다",
                "부탁",
                "부탁합니다",
                "부탁드립니다",
                "부탁드려요",
                "부탁해요",
                "부탁해주세요",
            )
        ),
    )
)
_CONFIRMED_EXCLUSION_ENDINGS = frozenset(
    ("제외해주세요", "제외해요", "제외할게요", "제외하겠습니다", "빼주세요", "뺄게요")
)

# 희망 표현만으로는 확정하지 않는다. 바로 뒤에 동일한 교체의
# 실행 요청이 완결된 별도 문장으로 이어질 때만 두 동사를 한 지시로 본다.
_INTENTION_STEM = r"(?:바꾸|고치|옮기|미루|당기|변경하|수정하|정정하|교체하|대체하|이동하)"
_INTENTION = rf"^\s*(?:로|으로)\s*{_INTENTION_STEM}(?:고\s*싶|려(?:고)?\s*)"
_DESIRE_EDIT = re.compile(
    rf"^\s*(?:로|으로)\s*{_INTENTION_STEM}"
    r"(?:고\s*싶(?:어요|습니다)|려(?:고)?\s*(?:합니다|해요))"
    r"\s*(?:[.!。！]\s*|\n\s*)"
    r"(?:(?:그렇게|이대로|그대로)\s*)?"
    r"(?P<confirmation>변경|수정|정정|교체|대체|이동)"
    r"\s*(?:부탁(?:합니다|드립니다|드려요|해요)?|"
    r"해\s*(?:주세요|줘요|주십시오))\s*[.!。！]?\s*$"
)
_DESIRE_ONLY = re.compile(_INTENTION)

# 교체와 그 결과 목록의 갱신은 하나의 지시다. 목적어와 종결형까지
# 확인하고, 다른 목록·추가 요청·조건이 이어지면 인정하지 않는다.
_LINKED_STEM = (
    r"(?:바꿔서|고쳐서|옮겨서|미뤄서|당겨서|"
    r"(?:바꾸|고치|옮기|미루|당기)고|"
    r"(?:변경|수정|정정|교체|대체|이동)하(?:고)|"
    r"(?:변경|수정|정정|교체|대체|이동)해서)"
)
_LINKED_ONLY = re.compile(rf"^\s*(?:로|으로)\s*{_LINKED_STEM}")
_LINKED_EDIT = re.compile(
    rf"^\s*(?:로|으로)\s*{_LINKED_STEM}\s*(?:,\s*)?"
    r"(?:(?:그|해당|같은)\s*)?"
    r"(?P<target>(?:(?:제외|휴무|작업|공사|시공)\s*(?:일|일자|날짜|날)?\s*)?)"
    r"목록\s*(?:을|를)\s*"
    r"(?P<confirmation>변경|수정|정정|교체|대체|이동)"
    r"\s*(?:합니다|하겠습니다|할게요|해요|"
    r"부탁(?:합니다|드립니다|드려요|해요)|해\s*(?:주세요|줘요|주십시오))"
    r"\s*[.!。！]?\s*$"
)
_LINKED_RISK = re.compile(
    r"만약|조건(?:부|이|에|을)|가능\s*(?:하다면|하면|할\s*때)|"
    r"(?:맞|되|하)으면|(?:된|한|할)다면|"
    r"인용|가정|예시|[\x22“”‘’]"
)

# 연결형 교체 지시 뒤에는 다른 필드의 명시적인 유지 지시만 허용한다.
# 같은 날짜의 추가 수정이나 다른 필드의 새 값 지정은 여기서 승인하지 않는다.
_CONNECTED_EDIT = re.compile(
    r"^\s*(?:로|으로)\s*"
    r"(?:바꾸|고치|옮기|미루|당기|변경하|수정하|정정하|교체하|대체하|이동하)고"
    r"(?:\s*[,，]\s*|\s+)"
)
_INDEPENDENT_CREW_KEEP = re.compile(
    r"(?:작업\s?)?(?:인원|작업자|인력)(?:은|는|이|가|만|도)\s?"
    r"(?:(?:아까|앞서|방금|이전|지난번)\s?(?:제안(?:한)?|정한|잡은|말한|알려준)"
    r"\s?(?:(?:값|인원|내용)?대로|그대로)\s?|"
    r"(?:현재|지금)\s?(?:값|입력값|인원)\s?(?:대로|그대로)\s?|"
    r"(?:그대로|기존대로|원래대로)\s?)?"
    r"(?:유지(?:해\s?주세요|합니다|할게요|하겠습니다)|"
    r"두세요|둘게요|두겠습니다|둡니다|둬\s?주세요)$"
)


def _confirmed_connected_retention(text: str, field: str) -> bool:
    """교체 절과 독립된 인원 유지 절을 모두 끝까지 확인한다."""
    connected = _CONNECTED_EDIT.match(text)
    if connected is None or field != "days":
        return False
    tail = " ".join(text[connected.end() :].strip().rstrip(".!。").split())
    return bool(_INDEPENDENT_CREW_KEEP.fullmatch(tail))


def _confirmed_suffix(text: str, field: str, *, shifted: bool) -> bool:
    """교체 절의 끝이 확정 지시인지 확인한다. 문의·인용·조건부 요청은 보류한다."""
    if _confirmed_connected_retention(text, field):
        return True
    # 같은 완결 실행 요청 문법을 의도 판정과 정정 검증에서 공유한다.
    # 앞쪽 위험 표현/관계/다른 변경은 아래 기존 검사에 남긴다.
    polite = POLITE_EXECUTION.search(text)
    if polite and re.fullmatch(r"\s*(?:으로|로|을|를|에)?\s*", text[: polite.start()]):
        return True
    clause = re.split(r"[.,;。!，\n]", text, maxsplit=1)[0]
    ending = "".join(clause.split())
    ending = _KEEP_OTHERS_TRAILER.sub("", ending)
    if not ending:
        return True
    if field == "excluded" and ending == "쉬는걸로요":
        return True
    if field == "excluded":
        ending = re.sub(r"^쉬는(?:걸|것으)로", "", ending)
    for particle in ("으로", "로", "을", "를", "에"):
        if ending.startswith(particle):
            ending = ending[len(particle) :]
            break
    return (
        ending in _CONFIRMED_ENDINGS
        or field == "excluded"
        and ending in _CONFIRMED_EXCLUSION_ENDINGS
        or shifted
        and ending == "요"
    )


_EDIT_WORD = (
    r"바꾸|바꿔|바꿀|고치|고쳐|고칠|변경|수정|정정|교체|대체|"
    r"옮기|옮겨|옮길|미루|미뤄|미룰|당기|당겨|당길|이동|추가|넣|삭제|지우|비우|초기화|늘리|줄이|조정"
)
_OTHER_EDIT = re.compile(_EDIT_WORD)
_FOLLOWING_EDIT = re.compile(
    rf"^\s*(?:(?:로|으로|을|를|만|쉬는\s*(?:걸|것으)로)\s*)?(?P<verb>{_EDIT_WORD})"
)
_UNCHANGED_EDIT = re.compile(r"^변경\s*없")
_MANUAL_BOUNDARY_RETENTION = re.compile(
    r"^(?:시작|종료|마감)\s*(?:일|날짜?)\s*(?:[은는이가]\s*)?"
    r"(?:제가\s*)?(?:방금\s*|이미\s*)?직접\s*(?:수정|변경)했(?:으니까|으니|기\s*때문에)\s*"
    r"(?:그\s*값|현재\s*값)\s*(?:그대로\s*)?유지(?:해\s*주(?:시고|세요)|하고|해요)$"
)
_KEEP = re.compile(
    r"^\s*(?:(?:은|는|이|가|을|를|도|만)\s*)?"
    r"(?:그대로|유지|기존대로|원래대로|변함\s*없|변경\s*없|동일|같(?:게|아요|습니다))"
)
# 부정형 보존은 교체하지 않는 날짜에 직접 연결된 지시일 때만 인정한다.
# 보호 동사의 '말고'는 교체 연결어와 구분하여 별도로 검증한다.
_PROTECTED_KEEP = re.compile(
    r"^\s?(?:은|는|이|가|을|를|도|만)?\s?"
    r"(?:건드리지|손대지|바꾸지|변경하지|수정하지)\s?"
    r"(?:말고|마시고|마세요|말아\s?주세요)"
    r"(?=$|[,，.;。!！\n]|\s+(?:(?:작업|공사|시공)\s?(?:일|날짜)|"
    r"제외일|휴무일|(?:\d{1,2}\s?월\s?)?\d{1,2}\s?일))"
)
_POST_BOUNDARY_KEEP = re.compile(
    r"^\s*(?:(?:은|는|이|가)\s*)?"
    r"(?:시작\s*(?:일|날짜?)|종료\s*(?:일|날짜?)|끝나는\s*날)"
    r"\s*(?:(?:은|는|이|가)\s*)?"
    r"(?:그대로|유지|기존대로|원래대로|변함\s*없|변경\s*없|건드리지\s*마|손대지\s*마)"
)
_KEEP_JOIN = re.compile(r"^\s*(?:,|，|과|와|이랑|랑|하고|및)\s*$")
_CLAUSE_BREAK = re.compile(r"[,，.;。!?！？\n]|그대로\s*두고|유지하고")
# 본 파서가 누락하는 0일/32일 같은 날짜 표기가 문장 안에 있으면 보류한다.
_DAY_SHAPE = re.compile(
    r"(?<![\d/.-])\d{1,3}\s*일"
    r"(?!\s*(?:간|동안|정도|째|걸|소요|후|전|차|뒤))"
)
_MONTH_SHAPE = re.compile(r"(?<!\d)\d{1,2}\s*월")


def _changes_crew(text: str) -> bool:
    """'2명에서 3명'처럼 인원도 교체하면 단일 날짜 변경으로 보지 않는다."""
    parts = text.split("에서")
    for left, right in pairwise(parts):
        left = left.rstrip()
        count, unit, _ = right.partition("명")
        if (
            left.endswith("명")
            and left[:-1].rstrip()[-1:].isdecimal()
            and unit
            and count.strip().isdecimal()
        ):
            return True
    return False


_UNCERTAIN = re.compile(
    r"[?？]|아마|혹시|미정|보류|불확실|모르겠|어쩌면|일단|"
    r"(?:될|할)\s*것\s*같|(?:수도|지도|정도|쯤)|"
    r"(?:인가|일까|될까|할까)요?|가능하면|생각\s*중|검토\s*중|"
    r"(?:잠정|임시|아직|예정|듯(?:해|합니다|한데))"
)
_NEGATED_EDIT = re.compile(
    r"(?:바꾸|고치|수정|변경|정정|교체|대체|옮기|미루|당기|이동)"
    r"(?:지(?:는)?\s*(?:않|말|마|못)|는\s*(?:게|건|것)\s*아니|\s*안\s*(?:할|해|하))"
    r"|(?:안\s*(?:돼|되|됨|바꾸|바꿔|바꿀|고치|수정|변경|교체)|"
    r"안\s*(?:옮기|옮겨|미루|미뤄|당기|당겨|이동)|"
    r"아니|아닌|아닐|아님|아닙니다|취소|하지\s*(?:마|않|말)|안\s*(?:갈|가)|철회)"
)
_EXCLUDED_SCOPE = re.compile(
    r"제외\s*(?:(?:할|하는)\s*)?(?:일|날짜?|날|할\s*날)|"
    r"(?:제외하|빼|쉬)기로\s*(?:한|했던|정한|결정한|합의한)|"
    r"빼\s*(?:는|야\s*하는|야\s*할|줄)\s*날|"
    r"(?:쉬는|휴무)\s*(?:날|날짜?)|"
    r"(?:못\s*(?:가|하는)|빠지는)\s*날|"
    r"불가\s*(?:일|날)|일정에서\s*제외|휴무(?:일|날짜?)?"
)
_EXCLUDED_RESULT = re.compile(
    r"(?:[은는]\s*)?쉬는\s*(?:걸로요(?=\s|[.,;。!，\n]|$)|"
    r"(?:걸|것으)로\s*(?:옮기|옮겨|옮길))"
)
_DAY_SCOPE = re.compile(r"작업\s*(?:일|날짜?)|공사\s*(?:일|날짜?)|시공\s*(?:일|날짜?)")
_OTHER_SCOPE = re.compile(
    r"시작\s*(?:일|날짜?)|(?:종료|마감)\s*(?:일|날짜?)|끝나는\s*날|유효\s*기간"
)
# 의도 분류에는 유효하지 않은 날짜와 미완성 교체도 포함한다.
# 실제 날짜 계산은 기존 엄격한 토큰과 검증기를 사용한다.
_RELATION_DATE = re.compile(
    r"(?<!\d)(?:20\d{2}\s*(?:년|[-/.])\s*)?"
    r"(?:\d{1,2}\s*(?:월|[-/.])\s*\d{1,3}\s*일?|"
    r"\d{1,3}\s*일(?!\s*(?:간|동안|정도|째|소요|후|전|차|뒤)))"
)
_RELATION_CONNECTOR = re.compile(r"^(?:[은는이가을를]\s*)?(?:대신|말고|아니고|아니라|->|→)")
_RELATION_ACTION = re.compile(rf"^\s*(?:로|으로)\s*(?:{DATE_CHANGE_ACTION})")
_MOVE_ACTION = re.compile(r"옮기|옮겨|옮길|미루|미뤄|미룰|당기|당겨|당길")
# '이동'은 명사로도 쓰이므로 날짜에 직접 연결된 미완성 이동만 보류한다.
_NOMINAL_MOVE_ACTION = re.compile(
    r"^\s?(?:[은는이가을를]?만|[은는이가을를]|에서|으로|로)?\s?"
    r"(?:(?:일정|작업일|공사일|작업\s?날짜|공사\s?날짜)\s?)?"
    r"(?:안\s?)?이동(?=$|\s|[하해할은는이가을를만도])"
)
_RELATION_GAP = re.compile(
    r"^(?:[은는이가을를]|에서|[은는이가을를]?만|[은는에]?쉬는(?:걸|것을)(?:만)?)$"
)


def date_replacement_requested(field: str, text: str) -> bool:
    """목록 교체 의도는 날짜·이전값·확정 여부의 검증 성공과 별개로 분류한다."""
    if field not in ("days", "excluded"):
        return False
    mentions = list(_RELATION_DATE.finditer(text))
    for index, old in enumerate(mentions):
        prefix = _local_prefix(text, old.start())
        marks = sorted(
            [(m.start(), "excluded") for m in _EXCLUDED_SCOPE.finditer(prefix)]
            + [(m.start(), "days") for m in _DAY_SCOPE.finditer(prefix)]
            + [(m.start(), "other") for m in _OTHER_SCOPE.finditer(prefix)]
        )
        if marks and marks[-1][1] not in (field,):
            continue
        tail = text[old.end() :]
        if _RELATION_CONNECTOR.match(tail.lstrip()):
            return True
        if index + 1 < len(mentions):
            new = mentions[index + 1]
            gap = text[old.end() : new.start()]
            compact = "".join(gap.split())
            suffix = text[new.end() :]
            if (
                _RELATION_GAP.fullmatch(compact)
                and _RELATION_ACTION.match(suffix)
                or _SHIFT_GAP.fullmatch(compact)
                or _EXCLUSION_MOVE_GAP.fullmatch(compact)
                or _ANAPHOR_MOVE_GAP.fullmatch(gap)
            ):
                return True
        # 옛날/새날 중 하나가 빠진 이동 요청도 열거로 바꾸지 않는다.
        clause = re.split(r"[,;.!?？。\n]", tail, maxsplit=1)[0]
        if _MOVE_ACTION.search(clause) or _NOMINAL_MOVE_ACTION.match(compact_whitespace(clause)[0]):
            return True
    return False


def date_edit_confirmation_start(field: str, text: str) -> int | None:
    """날짜 교체 희망에 바로 연결된 완료형 확인의 위치만 식별한다.

    대상 분류용이며 교체 값의 유효성은 validated_date_edit가 별도로 검증한다.
    """
    if not date_replacement_requested(field, text):
        return None
    mentions = _mentions(text)
    if len(mentions) != 2:
        return None
    pair = _single_replacement(text, mentions, field)
    if pair != (0, 1):
        return None
    old, last = mentions
    confirmation = _confirmed_list_followup(field, text, old, last)
    if confirmation is None:
        return None
    following = _FOLLOWING_EDIT.match(text[last.end :])
    if following is None or any(
        action.start()
        not in (
            last.end + following.start("verb"),
            last.end + confirmation.start("confirmation"),
        )
        for action in _OTHER_EDIT.finditer(text)
    ):
        return None
    return last.end + confirmation.start("confirmation")


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


_DATE_PROTECTION_PREFIX = re.compile(
    r"(?:(?:현재|지금) ?(?:(?:폼|화면|입력란|달력|캘린더)(?:에|의|에서)? ?)?"
    r"(?:(?:있는|입력한|입력된|등록한|등록된|선택한|선택된|적힌) ?)?|"
    r"(?:아까|앞서|이전|방금|지난번) ?(?:제안한|말한|정한|잡은) ?)?"
    r"(?:(?:작업|공사|시공) ?(?:일|날짜) ?)?"
)


def date_protection_clause(text: str) -> bool:
    """단독 작업일 보호 절인지 판별한다. 날짜 값의 검증은 호출자가 수행한다."""
    clause = " ".join(text.split())
    mentions = _mentions(clause)
    if len(mentions) != 1:
        return False
    mention = mentions[0]
    return bool(
        _DATE_PROTECTION_PREFIX.fullmatch(clause[: mention.start])
        and _PROTECTED_KEEP.fullmatch(clause[mention.end :])
    )


def _targeted(field: str, prefix: str, suffix: str) -> bool:
    """교체의 옛 날짜 앞에서 가장 가까운 필드 언급만 유효하다."""
    marks = sorted(
        [(m.start(), "excluded") for m in _EXCLUDED_SCOPE.finditer(prefix)]
        + [(m.start(), "days") for m in _DAY_SCOPE.finditer(prefix)]
        + [(m.start(), "other") for m in _OTHER_SCOPE.finditer(prefix)]
    )
    exclusion_result = bool(_EXCLUDED_RESULT.match(suffix.lstrip()))
    if field == "excluded":
        return marks[-1][1] == "excluded" if marks else exclusion_result
    return not exclusion_result and (not marks or marks[-1][1] == "days")


def _local_prefix(text: str, start: int) -> str:
    """완결된 앞 절의 필드 표기가 뒤 정정에 넘어가지 않도록 한다."""
    prefix = text[:start]
    breaks = list(_CLAUSE_BREAK.finditer(prefix))
    return prefix[breaks[-1].end() :] if breaks else prefix


def _confirmed_list_followup(
    field: str, text: str, old: _Mention, new: _Mention
) -> re.Match[str] | None:
    """연결된 확인이 동일한 날짜 목록 하나만 가리키는지 확인한다."""
    suffix = text[new.end :]
    confirmation = _DESIRE_EDIT.fullmatch(suffix)
    linked = _LINKED_EDIT.fullmatch(suffix)
    if confirmation is None and linked is None:
        return None
    if _UNCERTAIN.search(text) or _NEGATED_EDIT.search(text) or _LINKED_RISK.search(text):
        return None
    prefix = _local_prefix(text, old.start)
    if not _targeted(field, prefix, suffix):
        return None
    if (
        _OTHER_SCOPE.search(prefix)
        or field == "days"
        and _EXCLUDED_SCOPE.search(prefix)
        or field == "excluded"
        and _DAY_SCOPE.search(prefix)
        or re.search(r"인원|작업자|인력", prefix)
    ):
        return None
    if linked is not None:
        target = linked["target"].strip()
        if target and (
            field == "excluded"
            and not re.match(r"제외|휴무", target)
            or field == "days"
            and not re.match(r"작업|공사|시공", target)
        ):
            return None
        return linked
    return confirmation


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


def _same_mention_day(left: _Mention, right: _Mention, before: list[date]) -> bool:
    """유지·교체 대상이 동일한 실제 목록 원소인지 검사한다."""
    return any(
        day.day == left.day == right.day
        and (left.month is None or day.month == left.month)
        and (right.month is None or day.month == right.month)
        and (left.year is None or day.year == left.year)
        and (right.year is None or day.year == right.year)
        for day in before
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
        # 동일 날짜를 동시에 보존하고 교체하라는 요청은 확정하지 않는다.
        if not other_scope and _same_mention_day(mention, mentions[min(replaced)], before):
            return False
        if (
            _KEEP.match(following)
            or _POST_BOUNDARY_KEEP.match(following)
            or _PROTECTED_KEEP.match(following)
        ):
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
        raw_gap = text[old.end : new.start]
        gap = re.sub(r"\s+", "", raw_gap)
        suffix = text[new.end :]
        if (
            _REPLACE_GAP.fullmatch(gap)
            or (_EDIT_GAP.fullmatch(gap) and _EDIT_SUFFIX.match(suffix))
            or (
                field == "excluded"
                and _EXCLUSION_MOVE_GAP.fullmatch(gap)
                and _EXCLUSION_MOVE_SUFFIX.match(suffix)
            )
            or (
                field == "days"
                and _ANAPHOR_MOVE_GAP.fullmatch(raw_gap)
                and _EDIT_SUFFIX.match(suffix)
            )
            or _SHIFT_GAP.fullmatch(gap)
            or gap in _DIRECTION
        ):
            candidates.append((index, index + 1))
    if len(candidates) != 1:
        return None
    old_index, new_index = candidates[0]
    if not _targeted(
        field, _local_prefix(text, mentions[old_index].start), text[mentions[new_index].end :]
    ):
        return None
    return old_index, new_index


def date_edit_reference_prefix(field: str, text: str) -> str | None:
    """교체 대상이 있는 절에서만 현재/이전 참조를 판별할 수 있게 한다."""
    text = _independent_edit_clause(field, text) or text
    mentions = _mentions(text)
    pair = _single_replacement(text, mentions, field)
    if pair is None:
        return None
    old_index, _ = pair
    prefixes = [_local_prefix(text, mentions[old_index].start)]
    for mention in mentions[:old_index]:
        prefix = _local_prefix(text, mention.start)
        following = text[mention.end :]
        if (
            _OTHER_SCOPE.search(prefix)
            or _POST_BOUNDARY_KEEP.match(following)
            or not (_KEEP.match(following) or _PROTECTED_KEEP.match(following))
        ):
            continue
        # 명시된 보호 날짜의 같은 목록 참조만 합친다. 양쪽 출처가
        # 충돌하면 호출자의 기존 ambiguous 검증에서 거부된다.
        if field == "days" and _EXCLUDED_SCOPE.search(prefix):
            continue
        if field == "excluded" and _DAY_SCOPE.search(prefix):
            continue
        prefixes.append(prefix)
    return " ".join(prefixes)


def _retained_manual_boundary(text: str, action_at: int, field: str) -> bool:
    if field != "excluded":
        return False
    left = max((m.end() for m in _CLAUSE_BREAK.finditer(text, 0, action_at)), default=0)
    right = _CLAUSE_BREAK.search(text, action_at)
    clause = text[left : right.start() if right else len(text)].strip()
    return bool(_MANUAL_BOUNDARY_RETENTION.fullmatch(clause))


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


def _independent_edit_clause(field: str, text: str) -> str | None:
    """A complete edit and independent typed fields need not share a risk span.

    Do not isolate an edit from an unbound, competing or conditional clause.
    The existing full-list and business validation still runs on the edit.
    """
    if field not in ("days", "excluded"):
        return None
    form = "dates" if field == "days" else "period"
    parts = intent_clauses(form, text)
    if len(parts) < 2:
        return None
    candidates = [
        part
        for part in parts
        if part.fields == {field} and date_replacement_requested(field, text[part.start : part.end])
    ]
    if len(candidates) != 1:
        return None
    own = candidates[0]
    if any(
        not part.fields
        or field in part.fields
        or part.boundary not in ("sentence", "question", "end")
        or _LINKED_RISK.search(text[part.start : part.end])
        for part in parts
        if part is not own
    ):
        return None
    if own.boundary not in ("sentence", "question", "end"):
        return None
    return text[own.start : own.end].strip()


def validated_date_edit(
    field: str,
    text: str,
    prior: list[str] | None,
    context: AssistContext,
    *,
    require_affirmative: bool = False,
) -> list[str] | None:
    complete = _validated_date_edit(
        field, text, prior, context, require_affirmative=require_affirmative
    )
    if complete is not None or not require_affirmative:
        return complete
    scoped = _independent_edit_clause(field, text)
    if scoped is None:
        return None
    return _validated_date_edit(field, scoped, prior, context, require_affirmative=True)


def _validated_date_edit(
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
    if before is None or _UNCERTAIN.search(polite_execution_risk_text(text)):
        return None
    mentions = _mentions(text)
    if len(mentions) < 2:
        return None
    # 누락된 잘못된 날짜, 미확인 월·날짜 표기 및 독립된 인원 변경은 보류한다.
    if any(
        not any(item.start <= shape.start() and shape.end() <= item.end for item in mentions)
        for regex in (_DAY_SHAPE, _MONTH_SHAPE)
        for shape in regex.finditer(text)
    ) or _changes_crew(text):
        return None
    pair = _single_replacement(text, mentions, field)
    if pair is None:
        return None
    if not _retained_elsewhere(text, mentions, set(pair), before, context):
        return None
    # 검증된 다른 날짜의 보호 동사만 위험 검사에서 가린다. 위치는 보존하여
    # 교체 동사·날짜 근거·나머지 부정 및 취소는 기존 검사로 다시 확인한다.
    masked = list(text)
    for index, mention in enumerate(mentions):
        if index in pair:
            continue
        protection = _PROTECTED_KEEP.match(text[mention.end :])
        if protection:
            stop = mention.end + protection.end()
            masked[mention.end : stop] = " " * protection.end()
    checked_text = "".join(masked)
    if _NEGATED_EDIT.search(checked_text) or len(re.findall(r"대신|말고|->|→", checked_text)) > 1:
        return None
    old, new = (mentions[index] for index in pair)
    gap = "".join(text[old.end : new.start].split())
    shifted = bool(_SHIFT_GAP.fullmatch(gap))
    following = _FOLLOWING_EDIT.match(text[new.end :])
    suffix = text[new.end :]
    confirmed_followup = _confirmed_list_followup(field, text, old, new)
    if (
        _DESIRE_ONLY.match(suffix)
        or _LINKED_ONLY.match(suffix)
        and not _confirmed_connected_retention(suffix, field)
    ) and confirmed_followup is None:
        return None
    if (
        (require_affirmative or following and following.group("verb") == "이동")
        and confirmed_followup is None
        and not _confirmed_suffix(suffix, field, shifted=shifted)
    ):
        return None
    # 다른 절의 변경 지시가 새 날짜를 몰래 바꾸지 못하게 한다.
    allowed_action = new.end + following.start("verb") if following else None
    confirmation_action = (
        new.end + confirmed_followup.start("confirmation") if confirmed_followup else None
    )
    if any(
        action.start() not in (allowed_action, confirmation_action)
        and not (action.group() == "변경" and _UNCHANGED_EDIT.match(text[action.start() :]))
        and not _retained_manual_boundary(text, action.start(), field)
        for action in _OTHER_EDIT.finditer(checked_text)
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
    if shifted and replacement != existing + timedelta(days=1):
        return None
    if following and (
        following.group("verb") in ("미루", "미뤄", "미룰")
        and replacement <= existing
        or following.group("verb") in ("당기", "당겨", "당길")
        and replacement >= existing
    ):
        return None
    if replacement == existing or replacement in before:
        return None

    updated = [replacement if day == existing else day for day in before]
    if not _in_context(field, updated, context):
        return None
    return [value.isoformat() for value in updated]
