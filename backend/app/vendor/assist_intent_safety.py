"""업체 도우미의 문자열 대입 지시와 날짜 문의를 구분한다.

값 정규화·업무 조건·이전 후보의 검증은 기존 검증기에 맡긴다.
"""

import re
from dataclasses import dataclass
from itertools import pairwise

from app.vendor.assist_intent import (
    CONDITIONAL_LINK,
    DATE_CHANGE_ACTION,
    EXTRA_LABEL_MARKER,
    EXTRA_REASON_LABEL,
    EXTRA_TITLE_LABEL,
    HISTORY_PAST_MODIFIER,
    HISTORY_TIME,
    POLITE_EXECUTION,
    WITHDRAW_IDIOM,
    WITHDRAW_IDIOM_REQUEST,
    completed_inquiry,
    date_list_prefix,
    field_mentions,
    intent_clauses,
    intent_target_mentions,
    retention_scopes,
)
from app.vendor.assist_normalize import crew_tokens, duration_quantity_risk, duration_tokens
from app.vendor.assist_written_money import written_money_tokens

# 실제 명사 안의 제안/확정은 유지하고, 값 뒤의 할당 지시만 배제한다.
# 생략형으로 쓰이는 기존 동사와 요청 종결이 있어야 명령이 되는 동사를
# 구분한다. '조정', '지정' 자체는 정상적인 공사명·사유에도 들어간다.
_ASSIGNMENT_VERBS = r"제안|확정|변경|수정|정정|입력|기재|작성|기록|설정|반영|등록|선택|결정"
_REQUEST_VERBS = r"지정|조정|표기|표시|명시|교체|대체|저장|사용|채택|갱신|정"
_POLITE_REQUEST_END = (
    r"주세요|줘요|줘|주십시오|주시겠어요|주실래요|주시고|주시겠습니까|"
    r"주실\s*수\s*있(?:을까요|나요)"
)
_REQUEST_ENDING = (
    rf"해\s*(?:{_POLITE_REQUEST_END})|"
    r"합니다|해요|할게요|하겠습니다|하고요?|하며|"
    r"부탁(?:합니다|드립니다|드려요|해요|해\s*(?:주세요|줘요))?|"
    r"바랍니다"
)
# 조사 뒤에 붙는 수식어는 값의 일부가 아니라 완결된 대입 술어의
# 구성 요소이다. 실제 명사/인용값까지 임의로 잘라내지 않도록
# 아래 전체 종결 문법으로만 경계를 확정한다.
_ASSIGNMENT_MODIFIER = (
    r"(?:먼저|우선|다시|재차|새로(?:이)?|새롭게|최종(?:적으로)?|"
    r"정확히|정확하게|명확히|명확하게|정식으로|한\s*번\s*더)"
)
# 같은 대입 동사에 대해 직접 요청과 관형형 요청을 조립한다.
# 관형형 자체는 의도가 아니다. 요청 대상과 긍정 종결까지
# 완결됐을 때만 값의 끝을 확정한다.
_ASSIGNMENT_PARTICLE = r"(?:이라고|라고|으로|로)"
_ASSIGNMENT_STEM = rf"(?:{_ASSIGNMENT_VERBS}|{_REQUEST_VERBS})"
_PROPOSAL_NOUN = r"(?:초안|후보(?:안)?|제안(?:안)?|안)"
_PROPOSAL_OBJECT = rf"{_PROPOSAL_NOUN}(?:\s*[을를])?"
_ADNOMINAL_ASSIGNMENT = rf"{_ASSIGNMENT_STEM}(?:할|하는|하기\s*위한)"
_ADNOMINAL_PROPOSAL = rf"{_ADNOMINAL_ASSIGNMENT}\s+{_PROPOSAL_OBJECT}"
_OBJECT_FIRST_PROPOSAL = (
    rf"{_PROPOSAL_NOUN}(?:\s*(?:[을를]|으로|로)\s*|\s+){_ASSIGNMENT_STEM}(?:\s*[을를])?"
)
_PROPOSAL_REQUEST = (
    rf"(?:(?:부탁|요청)\s*(?:합니다|드립니다|드려요|해요|해\s*(?:{_POLITE_REQUEST_END}))|"
    rf"{_POLITE_REQUEST_END}|바랍니다|해\s*(?:{_POLITE_REQUEST_END})|"
    rf"(?:만들어|제시해|제안해|보여)\s*(?:{_POLITE_REQUEST_END}))"
)
_ASSIGNMENT_HEAD = rf"{_ASSIGNMENT_PARTICLE}\s*(?:{_ASSIGNMENT_MODIFIER}\s*)*"
_ASSIGNMENT = re.compile(
    rf"{_ASSIGNMENT_HEAD}"
    rf"(?:(?:{_ADNOMINAL_PROPOSAL}|{_OBJECT_FIRST_PROPOSAL})\s*{_PROPOSAL_REQUEST}"
    rf"|(?:{_ASSIGNMENT_VERBS})(?:\s*(?:{_REQUEST_ENDING}))?"
    rf"|(?:{_REQUEST_VERBS})\s*(?:{_REQUEST_ENDING})"
    r"|(?:바꿔|고쳐|적어|넣어|써|해|정해|잡아|맞춰|골라|올려|만들어|남겨|기입해)"
    rf"\s*(?:{_POLITE_REQUEST_END})"
    rf"|(?:초안|후보)\s*(?:(?:{_ASSIGNMENT_VERBS}|{_REQUEST_VERBS})\s*)?"
    rf"(?:{_REQUEST_ENDING})"
    r"|부탁(?:합니다|드립니다|드려요|해요|해\s*(?:주세요|줘요))?"
    r"|바꿀게요|고칠게요|하겠습니다|할게요|적고|쓰고|기입하고)$"
)
_ASSIGNMENT_START = re.compile(r"이라고|라고|으로|로")


def affirmative_assignment_suffix(text: str) -> bool:
    """값 뒤의 완결된 실행 지시만 독립 대입 근거로 사용한다.

    문자열 파서의 동사·수식어·종결 구조를 공유하지만, 명사형이나
    생략된 종결은 모델의 보류를 해제할 정도의 증명은 아니다.
    """
    tail = text.strip().rstrip(".!。 ")
    return bool(
        _ASSIGNMENT.fullmatch(tail)
        and re.search(rf"(?:{_REQUEST_ENDING}|{_POLITE_REQUEST_END}|겠습니다|을게요)$", tail)
        and not clause_is_inquiry(tail)
    )


_TEXT_TARGET = re.compile(
    rf"(?<![가-힣])(?:(?P<title>{EXTRA_TITLE_LABEL})|(?P<reason>{EXTRA_REASON_LABEL})|"
    r"(?:(?:추가|공사|시공)\s*)?(?:(?P<amount>금액|비용|공사비|총액|추가금)|(?P<vat>부가세|세금)))"
    rf"{EXTRA_LABEL_MARKER}"
)
# 값 자체에 붙은 필드 표지는 별도로 본다. 맨 앞의 명칭과 값 사이에
# 실제 구분자가 있어야 하며, 이미 명시된 값 속의 명사는 표지가 아니다.
_LABEL_PREFIX = re.compile(
    rf"^(?:(?P<title>{EXTRA_TITLE_LABEL})|(?P<reason>{EXTRA_REASON_LABEL}))"
    r"(?:\s*(?:칸|입력란|란)(?:에)?)?"
    r"(?:\s*[:=]\s*|\s*[은는을를이가는만도]\s+|\s+)"
)
_CLAUSE_LEAD = re.compile(r"\s*(?:(?:그리고|또|다음으로|다만)\s+)?(?:(?:추가|공사|시공)\s+)?")
_CONNECTED_LABEL = re.compile(
    rf"(?:{_ASSIGNMENT_VERBS}|{_REQUEST_VERBS})(?:하고|하며)\s*$"
    r"|(?:바꾸|고치|넣|잡|두|적|쓰)고\s*$"
)
_SENTENCE = re.compile(r"(?<!\d)\.|\.(?!\d)|[!?！？;。\n]")
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))


def _space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _quoted_value(text: str, start: int, end: int) -> bool:
    return any(
        text[:start].rstrip().endswith(left) and text[end:].lstrip().startswith(right)
        for left, right in _QUOTE_PAIRS
    )


def _selected_text(raw: str, value: str) -> str:
    selected = _space(value).strip()
    if selected and any(
        _quoted_value(raw, match.start(), match.end())
        for match in re.finditer(re.escape(selected), raw)
    ):
        # 인용한 실제 이름의 마지막 문장 부호도 값의 일부이다.
        return selected
    return selected.rstrip(".!。").strip()


_QUOTE_CLOSE = dict(_QUOTE_PAIRS)
_QUOTE_RIGHT = frozenset(_QUOTE_CLOSE.values())


def _literal_quote_ends(raw: str) -> dict[int, int]:
    """같은 줄에 있는 가장 가까운 닫는 인용부호를 한 번의 역방향 탐색으로 찾는다."""
    nearest: dict[str, int] = {}
    ends: dict[int, int] = {}
    for index in range(len(raw) - 1, -1, -1):
        char = raw[index]
        if char == "\n":
            nearest.clear()
            continue
        right = _QUOTE_CLOSE.get(char)
        if right is not None and right in nearest:
            ends[index] = nearest[right] + 1
        if char in _QUOTE_RIGHT:
            nearest[char] = index
    return ends


def _literal_quote(raw: str) -> bool:
    if len(raw) < 2:
        return False
    right = _QUOTE_CLOSE.get(raw[0])
    return right is not None and raw[-1] == right and right not in raw[1:-1] and "\n" not in raw


_TEXT_LABEL_MARKER = re.compile(rf"^{EXTRA_LABEL_MARKER}")


def _text_explicit_lead(prefix: str) -> bool:
    clause = re.split(r"[,;.!?！？。\n]", prefix)[-1]
    return not clause.strip() or (clause[-1].isspace() and clause.strip() in ("그리고", "다만"))


def _ends_with_clause_separator(text: str) -> bool:
    trimmed = text.rstrip()
    return (
        trimmed.endswith((",", ";", ".", "!", "?", "！", "？", "。"))
        or "\n" in text[len(trimmed) :]
    )


_REFERENCE_AS_VALUE = re.compile(
    r"^(?:(?:앞서|아까|방금|이전|기존|직전|지난번|앞에서)\s*"
    r"(?:(?:제시한|제안한|정한|말한|말씀드린|요청한)\s*)?"
    r"(?:후보|제안|내용|값|안|걸|것|거)"
    r"(?:\s*[을를은는이가])?\s*"
    r"(?:그대로|대로|으로|로)?\s*"
    r"(?:유지|사용|적용|진행|반영|쓰|쓸|가|두|둘|부탁))"
)


def _mask_quoted_spans(raw: str) -> str:
    ends = _literal_quote_ends(raw)
    masked = list(raw)
    index = 0
    while index < len(raw):
        end = ends.get(index)
        if end is None:
            index += 1
        else:
            masked[index:end] = " " * (end - index)
            index = end
    return "".join(masked)


def _label_marker(raw: str, mark):
    """필드 표지 뒤의 인용값은 마스킹하기 전 위치로 확인한다."""
    tail = raw[mark.end :]
    marker = _TEXT_LABEL_MARKER.match(tail)
    if marker is not None:
        return marker
    if mark.fields in ({"title"}, {"reason"}):
        return re.match(r"\s+(?=['\"“‘])", tail)
    return None


def _explicit_text_labels(raw: str):
    """실제 대입 표지를 식별한다. 인용값 안의 명사는 표지가 아니다."""
    masked = _mask_quoted_spans(raw)
    marks = []
    for mark in field_mentions("extra", masked):
        tail = masked[mark.end :]
        prefix = masked[: mark.start]
        # 값 자체의 명사(예: 사유 확인 누락)를 필드로 오인하지 않는다.
        # 구분자 또는 명확한 필드+수량은 독립 대입 표지이다.
        if (
            _label_marker(raw, mark)
            or (
                mark.fields == {"amount"}
                and (
                    tail.lstrip()[:1].isdecimal()
                    or any(a == 0 for a, _, _ in written_money_tokens(tail.lstrip()) or [])
                )
            )
            or (
                mark.fields == {"vat"}
                and tail.lstrip().startswith(
                    ("별도", "포함", "면세", "미포함", "제외", "방식", "조건", "으로", "로")
                )
            )
            or (
                mark.fields.intersection(("title", "reason"))
                and _text_explicit_lead(prefix)
                and masked[mark.start : mark.end].startswith(
                    ("추가 ", "추가작업", "추가항목", "추가사유")
                )
            )
        ):
            marks.append(mark)
    # '항목 이름은'처럼 두 별칭이 붙은 경우 앞쪽 명사의 끝에 있는
    # '이름'의 첫 음절을 조사로 중복 해석하지 않는다.
    return [
        mark
        for i, mark in enumerate(marks)
        if not (
            i + 1 < len(marks)
            and mark.fields == marks[i + 1].fields
            and not masked[mark.end : marks[i + 1].start].strip()
            and _label_marker(raw, marks[i + 1])
        )
    ]


def mask_bound_text_literals(raw: str) -> str:
    """명시 제목/사유의 인용값만 가린다. 다른 필드의 인용 수량은 보존한다."""
    masked = raw
    ends = _literal_quote_ends(raw)
    labels = _explicit_text_labels(raw)
    for label in labels:
        if label.fields not in ({"title"}, {"reason"}):
            continue
        marker = _label_marker(raw, label)
        if marker is None:
            continue
        tail = raw[label.end + marker.end() :]
        start = label.end + marker.end() + len(tail) - len(tail.lstrip())
        end = ends.get(start)
        if end is not None:
            masked = masked[:start] + " " * (end - start) + masked[end:]
    return masked


def mask_owned_text_values(raw: str) -> str:
    """가리킨 제목/사유의 완전한 값은 숫자·세금 필드의 근거가 아니다.

    원문 위치는 보존한다. 독립 스칼라 대입의 경계는 공통 문자열
    소유권 분석에서 입증된 구간만 사용한다.
    """
    masked = mask_bound_text_literals(raw)
    for field in ("title", "reason"):
        for _, start, end, _ in _explicit_assignment_spans(raw, field):
            if 0 <= start < end:
                masked = masked[:start] + " " * (end - start) + masked[end:]
    return masked


_VALUE_CLAUSE_BREAK = re.compile(r"(?<!\d)[,.，]|[,.，](?!\d)|[;!?！？。\n]")
_VALUE_RELATION = re.compile(r"(?:말고|아니고|아니라|대신|에서|또는|혹은|아니면)")
_INCOMPLETE_ASSIGNMENT = re.compile(
    rf"(?:{_ASSIGNMENT_PARTICLE}\s*(?:{_ASSIGNMENT_MODIFIER}\s*)+"
    r"(?:정|정해|기재|작성|입력|변경|수정|지정|확정|결정|기록|설정|반영|선택)?$"
    rf"|{_ASSIGNMENT_HEAD}(?:{_ADNOMINAL_PROPOSAL}|{_OBJECT_FIRST_PROPOSAL})(?=$|\s|[은는이가도만?？])"
    rf"|{_ASSIGNMENT_HEAD}{_ADNOMINAL_ASSIGNMENT}\s*$)"
)
_HISTORY_REFERENCE_NOMINAL = re.compile(
    rf"(?:(?:{HISTORY_TIME})\s*{HISTORY_PAST_MODIFIER}\s*|"
    r"(?:이전|기존|직전|앞선|지난)\s*)"
    r"(?:후보|제안|내용|값|안|걸|것|거|항목|제목|사유|이유|작업명|항목명)"
)
# A nominal field clause can establish a complete value without repeating the
# final request verb. Do not infer a nominal span from a reported, conditional,
# withdrawn or unfinished instruction; the existing intent/correction checks
# still decide whether a grounded value may be offered.
# 비우기/삭제 지시는 문자열 값이 아니다. 단순 명사인 '삭제 이력'
# 등은 그대로 두고, 실제 요청 술어(연결형 포함)를 검사한다.
_TEXT_CLEAR_COMMAND = re.compile(
    r"(?:(?:지워|비워|없애)(?:\s*(?:주세요|줘요|주십시오|주시겠어요|둘게요)|서\s*[^,;.!?！？。]{0,32}(?:두세요|둬\s*주세요|주세요|줘요))"
    r"|(?:삭제|제거|초기화)\s*(?:해\s*(?:주세요|줘요|주십시오)|합니다|하겠습니다|할게요))"
    r"\s*[.!。]?$"
)
_NOMINAL_CONTROL = re.compile(
    r"[?？]|아마|혹시|미정|보류|검토\s*중|고민|예정|생각\s*중|모르|애매|"
    r"가능하면|조건부|(?:라|다|으|이|하|되)면|경우|또는|아니면|"
    r"말고|아니|아닙|아님|취소|철회|그대로|유지|하지\s*않|하지\s*마|"
    r"거부|원하지|잘못|틀렸|안\s*맞|건드리지|삭제|비우|지우|"
    r"(?:이라고|라고|으로|로)\s*(?:먼저|후보|초안|부탁|요청|정하|등록하|바꾸|변경하)|"
    r"(?:입니다|이에요|예요|이요|했어요|했습니다|합니다|하겠습니다|할게요|"
    r"주세요|줘요|바랍니다|부탁드립니다)$"
)
# A dependent predicate is not a standalone field value. Inspect the whole
# containing clause, including the tail outside a producer's proposed span.
_DEPENDENT_NOMINAL = re.compile(
    r"(?:때문에|탓에|덕분에|아서|어서|여서|해서|돼서|라서|"
    r"워서|와서|겨서|려서|쳐서|켜서|져서|펴서|써서|"
    r"으므로|므로|으니까|니까|아져|어져|여져|되어|되어서)"
    r"(?:요)?\s*$"
)
_SHARED_VALUE_CASE = re.compile(r"(?:이라고|라고|으로|로)$")


def _shared_nominal_end(raw: str, masked: str, labels, i: int, begin: int, end: int) -> int:
    """생략된 대입 술어를 같은 문장의 다음 독립 필드에서만 빌린다."""
    if end == len(raw) or raw[end] not in ",，":
        return end
    # 다음 명시 필드 앞에 새 문장, 조건 또는 별도 명사가 끼면
    # 공통 술어의 범위로 인정하지 않는다.
    if i + 1 >= len(labels):
        return end
    neighbor = labels[i + 1]
    if neighbor.fields == labels[i].fields or masked[end + 1 : neighbor.start].strip():
        return end
    marker = _TEXT_LABEL_MARKER.match(masked[neighbor.end :])
    if marker is None:
        return end
    next_start = neighbor.end + marker.end()
    next_stop = _VALUE_CLAUSE_BREAK.search(masked, next_start)
    next_end = next_stop.start() if next_stop else len(raw)
    next_tail = masked[next_start:next_end].rstrip()
    if not _ASSIGNMENT.search(next_tail) or _NOMINAL_CONTROL.search(
        masked[next_start : next_start + _ASSIGNMENT.search(next_tail).start()]
    ):
        return end
    original = raw[begin:end].rstrip()
    case = _SHARED_VALUE_CASE.search(original)
    if case is None:
        return end
    before = original[: case.start()].rstrip()
    if not before:
        return end
    if _literal_quote(before):
        return begin + len(before)
    # 조사와 동일한 일반 명사의 끝을 무조건 절단하지 않는다.
    # 실제 조사가 붙은 형태의 받침과 독립된 명사 어절을 확인한다.
    last = before[-1]
    if not ("가" <= last <= "힣"):
        return end
    batchim = (ord(last) - 0xAC00) % 28
    particle = case.group()
    expected = (
        ("으로" if batchim not in (0, 8) else "로")
        if particle.endswith("로") and particle not in ("라고", "이라고")
        else ("이라고" if batchim else "라고")
    )
    if particle != expected or (
        len(before.split()[-1]) < 2 and not original[case.start() - 1].isspace()
    ):
        return end
    return begin + len(before)


def _explicit_assignment_spans(raw: str, field: str):
    """확정 가능한 명시 문자열 대입의 (절 시작, 값 시작, 값 끝, 절 끝).

    구문이 완결되지 않거나 교체/복수값 해석이 필요하면 기존 의도·정정
    검증에 맡긴다. 인용부호 안의 조사와 술어는 실제 문자열이다.
    """
    masked = _mask_quoted_spans(raw)
    labels = _explicit_text_labels(raw)
    for i, label in enumerate(labels):
        if label.fields != {field}:
            continue
        marker = _label_marker(raw, label)
        if marker is None:
            continue
        begin = label.end + marker.end()
        stop = _VALUE_CLAUSE_BREAK.search(masked, begin)
        end = stop.start() if stop else len(raw)
        # 구두점 없는 독립 필드는 실제 완결 술어 뒤에서만 자른다.
        for neighbor in labels[i + 1 :]:
            if neighbor.start >= end:
                break
            if _CONNECTED_LABEL.search(masked[begin : neighbor.start].rstrip()):
                end = neighbor.start
                break
        # 공백으로 마스킹된 인용값은 원문의 값 범위를 유지한다.
        tail = masked[begin:end].rstrip()
        suffix = _ASSIGNMENT.search(tail)
        clause = raw[begin:end].strip()
        if clause and any(
            clause.startswith(left)
            and not re.search(re.escape(left) + r"[^\n]*?" + re.escape(right), clause)
            for left, right in _QUOTE_PAIRS
        ):
            yield label.start, -1, -1, end
            continue
        if suffix is None and _INCOMPLETE_ASSIGNMENT.search(tail):
            yield label.start, -1, -1, end
            continue
        # 명사형 값은 필드 표지부터 독립 절 끝까지 한 구간으로
        # 검증한다. 인용값은 원문 그대로, 보고/위험 문장은 기존
        # 의도 검증에 맡긴다. 같은 절의 이웃 필드는 값이 아니다.
        if (
            suffix is None
            and not re.search(r"[:=]", marker.group())
            and not _literal_quote(clause)
            and (
                _NOMINAL_CONTROL.search(tail)
                or _TEXT_CLEAR_COMMAND.search(tail)
                or any(begin <= other.start < end for other in labels[i + 1 :])
            )
        ):
            continue
        value_end = (
            begin + suffix.start()
            if suffix
            else _shared_nominal_end(raw, masked, labels, i, begin, end)
        )
        body = raw[begin:value_end].strip()
        if not body:
            continue
        if not _literal_quote(body) and _VALUE_RELATION.search(masked[begin:value_end]):
            continue
        if not _literal_quote(body) and _HISTORY_REFERENCE_NOMINAL.fullmatch(body):
            yield label.start, -1, -1, end
            continue
        start = begin + len(raw[begin:value_end]) - len(raw[begin:value_end].lstrip())
        value_end = start + len(body)
        if _literal_quote(body):
            start += 1
            value_end -= 1
            while start < value_end and raw[start].isspace():
                start += 1
            while value_end > start and raw[value_end - 1].isspace():
                value_end -= 1
        if start < value_end:
            yield label.start, start, value_end, end


def text_value_boundary_ok(field: str, value: str, text: str) -> bool:
    """명확한 대입 절에서 잘린 명사/지시문 조각을 값으로 인정하지 않는다."""
    if field not in ("title", "reason"):
        return True
    raw = "\n".join(_space(line) for line in text.splitlines())
    selected = _selected_text(raw, value)
    if not selected:
        return False
    if text_value_is_clear_instruction(field, selected, raw):
        return False
    spans = tuple(_explicit_assignment_spans(raw, field))
    for occurrence in re.finditer(re.escape(selected), raw):
        a, b = occurrence.span()
        containing = [
            (start, end) for left, start, end, right in spans if left <= a < right or a < left < b
        ]
        if not containing or (a, b) in containing:
            return True
    return False


def text_value_is_clear_instruction(field: str, value: str, text: str) -> bool:
    """A clear request is an action, never an unquoted string value."""
    if field not in ("title", "reason"):
        return False
    raw = "\n".join(_space(line) for line in text.splitlines())
    selected = _selected_text(raw, value)
    if not selected or not _TEXT_CLEAR_COMMAND.search(selected):
        return False
    labels = _explicit_text_labels(raw)
    occurrences = []
    for match in re.finditer(re.escape(selected), raw):
        owners = [
            label
            for label in labels
            if label.start < match.end() and not _SENTENCE.search(raw[label.end : match.start()])
        ]
        if not owners or field in owners[-1].fields:
            occurrences.append(match)
    return bool(occurrences) and not any(
        _quoted_value(raw, match.start(), match.end()) for match in occurrences
    )


def explicit_text_assignment(field: str, value: str, text: str) -> bool:
    """완결된 명시 대입의 값과 정확히 일치하는 경우에만 독립 규칙 근거."""
    raw = "\n".join(_space(line) for line in text.splitlines())
    selected = _space(value).strip()
    spans = tuple(_explicit_assignment_spans(raw, field))
    return (
        bool(spans)
        and all(start >= 0 for _, start, _, _ in spans)
        and {raw[start:end] for _, start, end, _ in spans} == {selected}
    )


def mask_explicit_text_value(field: str, value: str, text: str) -> str:
    """검증된 전체 문자열 안의 명사만 가려 과거·부정 지시와 구별한다."""
    raw = "\n".join(_space(line) for line in text.splitlines())
    selected = _space(value).strip()
    masked = raw
    for _, start, end, _ in _explicit_assignment_spans(raw, field):
        if start >= 0 and raw[start:end] == selected:
            masked = masked[:start] + " " * (end - start) + masked[end:]
    return masked


def text_value_scope_ok(field: str, value: str, text: str) -> bool:
    """실제 다른 필드의 지시/값이나 참조 구절을 새 문자열 값으로 바꾸지 않는다.

    원문에 실제 값이 존재한다는 검증과 함께 사용한다. 독립적인 명사구와
    명시 대입은 보존하되 다른 필드의 명시 지시 앞에 있는 소유권 없는
    설명은 제목/사유 대입으로 간주하지 않는다.
    """
    if field not in ("title", "reason"):
        return True
    raw = "\n".join(_space(line) for line in text.splitlines())
    selected = _selected_text(raw, value)
    if not selected:
        return False
    labels = _explicit_text_labels(raw)
    masked = _mask_quoted_spans(raw)
    scopes = retention_scopes("extra", masked)
    spans = tuple(_explicit_assignment_spans(raw, field))
    # 명시적으로 소유된 값 전체가 다른 필드의 값으로 재사용되는 것을
    # 막는다. 정확한 인용 구간은 주변의 이름과 술어에서 분리한다.
    other_spans = tuple(
        span
        for other in ("title", "reason")
        if other != field
        for span in _explicit_assignment_spans(raw, other)
    )
    for occurrence in re.finditer(re.escape(selected), raw):
        a, b = occurrence.span()
        exact_owner = any(start == a and end == b for _, start, end, _ in spans)
        if not exact_owner and any(
            start >= 0 and a < end and start < b or a <= left < b
            for left, start, end, _ in other_spans
        ):
            continue
        containing = [
            (start, end) for left, start, end, right in spans if left <= a < right or a < left < b
        ]
        if containing and (a, b) not in containing:
            continue
        quoted = _quoted_value(raw, a, b)
        if not quoted and (
            _REFERENCE_AS_VALUE.match(selected)
            or any(
                scope.predicate_start <= a < scope.end or a <= scope.start < b for scope in scopes
            )
        ):
            continue
        preceding = [
            label
            for label in labels
            if label.start < a and not _SENTENCE.search(_mask_quoted_spans(raw[label.end : a]))
        ]
        owner = preceding[-1].fields if preceding else frozenset()
        if owner and field not in owner:
            continue
        if (
            not owner
            and not containing
            and not quoted
            and labels
            and selected.endswith(("어요", "아요", "습니다", "네요", "군요", "거든요"))
        ):
            continue
        if not owner and not containing and not quoted:
            left = max((m.end() for m in _VALUE_CLAUSE_BREAK.finditer(masked, 0, a)), default=0)
            right = _VALUE_CLAUSE_BREAK.search(masked, b)
            clause = masked[left : right.start() if right else len(raw)].strip()
            # A complete, standalone reason shorthand is already supported
            # by the form. A fragment of a spoken/background sentence is
            # not that shorthand, nor is a dependent clause of another field.
            standalone_reason = (
                field == "reason"
                and not labels
                and selected == raw.strip()
                and not selected.endswith("요")
            )
            if _DEPENDENT_NOMINAL.search(clause) and not standalone_reason:
                continue
        # A later named operation cannot lend its predicate to an unowned
        # background phrase. A postposed own label is a separate witness;
        # do not confuse it with a different field's assignment. This is a
        # scope rule, not a list of particular causal connective endings.
        following = next((label for label in labels if label.start >= b), None)
        if not owner and not containing and following is not None and field not in following.fields:
            # An entire standalone nominal sentence is existing shorthand,
            # unlike a fragment of a description or a dependent antecedent.
            left = max((m.end() for m in _SENTENCE.finditer(masked, 0, a)), default=0)
            # A leading bare title is an existing form shorthand when it is
            # a complete list item before another owned field. A bare reason
            # is not inferred from a comma-delimited explanatory fragment.
            separator = _VALUE_CLAUSE_BREAK if field == "title" else _SENTENCE
            boundary = separator.search(masked, b, following.start)
            prefix = raw[left:a].strip()
            suffix = raw[b : boundary.start()].strip() if boundary else ""
            bounded = (not prefix and not suffix) or (quoted and (prefix, suffix) in _QUOTE_PAIRS)
            independent = (
                boundary is not None
                and bounded
                and (
                    quoted
                    or not _NOMINAL_CONTROL.search(selected)
                    and not _DEPENDENT_NOMINAL.search(selected)
                    and not selected.endswith(("습니다", "어요", "아요", "네요", "군요", "거든요"))
                )
            )
            if not independent:
                continue
        if not quoted and any(
            a <= label.start < b
            and (
                label.start == a
                and not owner
                or field not in label.fields
                and (
                    not owner
                    or _CONNECTED_LABEL.search(raw[a : label.start].rstrip(" ,，"))
                    or _ends_with_clause_separator(raw[a : label.start])
                )
            )
            for label in labels
        ):
            continue
        return True
    return False


def assignment_directive_in_value(field: str, value: str, text: str) -> bool:
    """값 자체에 포함된 할당 명령을 거부한다. 명사와 인용한 실제 이름은 보존한다."""
    if field not in ("title", "reason"):
        return False
    raw = "\n".join(_space(line) for line in text.splitlines())
    selected = _selected_text(raw, value)
    label = _LABEL_PREFIX.match(selected)
    starts = list(_ASSIGNMENT_START.finditer(selected))
    if not selected or not (label or starts):
        return False
    for match in re.finditer(re.escape(selected), raw):
        if _quoted_value(raw, match.start(), match.end()):
            continue
        if label and label.lastgroup == field and selected[label.end() :].strip():
            prefix = re.split(r"[,;.!?！？。\n]", raw[: match.start()])[-1]
            connected = _CONNECTED_LABEL.search(prefix)
            prior_targets = list(_TEXT_TARGET.finditer(prefix))
            if _CLAUSE_LEAD.fullmatch(prefix) or (
                connected and prior_targets and prior_targets[-1].lastgroup != field
            ):
                return True
        # 후보에 지시문 일부만 들어 있어도 실제 원문의 종결 경계로 검증한다.
        for start in starts:
            if not selected[: start.start()].strip():
                continue
            position = match.start() + start.start()
            local = re.split(r"[,;.!?！？。\n]", raw[:position])[-1]
            targets = list(_TEXT_TARGET.finditer(local))
            if targets and targets[-1].lastgroup != field:
                continue
            # 명사의 중간에서 잘린 경우(예: 확정 도면)는 실제 명령이 아니다.
            rest = raw[position:]
            boundary = _VALUE_CLAUSE_BREAK.search(_mask_quoted_spans(rest))
            tail = rest[: boundary.start()] if boundary else rest
            next_field = _TEXT_TARGET.search(tail)
            if next_field:
                tail = tail[: next_field.start()]
            if _ASSIGNMENT.fullmatch(tail.strip()):
                return True
    return False


# 날짜의 값은 계산하지 않는다. 날짜 토큰과 실제 대상만 찾아 문의 범위를 제한한다.
_CALENDAR_DATE = re.compile(
    r"(?<!\d)(?:20\d{2}\s*(?:년|[-/.])\s*)?"
    r"\d{1,2}\s*(?:월|[-/.])\s*\d{1,3}\s*일?|"
    r"(?:오늘|내일|모레|이번\s*주|다음\s*주|[월화수목금토일]요일)"
)
_DATE = re.compile(rf"(?:{_CALENDAR_DATE.pattern})|(?<!\d)\d{{1,3}}\s*일")
_EXCLUSION = re.compile(
    r"제외(?:해|하|할|되|하는)?|빼(?:고|도|면|는|야|주)?|뺄(?:지|까|\s*날)|"
    r"쉬(?:는|어|면|게|기로)|휴무|못\s*(?:가|하|받)|불가능|안\s*(?:돼|되|가)"
)
_DATE_FIELDS = {
    "period": frozenset(("start", "end", "excluded")),
    "dates": frozenset(("days",)),
    "quote": frozenset(("days", "valid")),
}
_FIELD_JOIN = re.compile(r"^\s*(?:과|와|및|하고|이랑|랑|,|，)\s*$")
_VALUE_OR_PREDICATE = re.compile(
    r"\d|(?:확정|변경|수정|정정|제안|유지|확인|가능|미정|등록|입력|선택|결정)"
    r"|(?:잡아|잡고|바꿔|바꾸고|고쳐|옮겨|옮기고|넣어|두고|둡니다|해\s*주)"
)
# 숫자 나열의 '하고'는 절을 나누지 않는다.
_PREDICATE_JOIN = re.compile(
    r"(?:확정|변경|수정|정정|제안|유지|입력|등록|선택|결정)(?:하고|하며)|"
    r"(?:바꾸|고치|옮기|잡|넣|두|빼)고|가능하고|가능하며|"
    r"(?:확인|검토)(?:하고|하며)"
)
_COMMA_AFTER_PREDICATE = re.compile(
    r"(?:확정|변경|수정|정정|제안|입력|등록|선택|결정|가능|미정|보류)"
    r"(?:합니다|해요|입니다|해\s*주세요|할게요|하겠습니다|하고|이며|이고)?\s*[,，]"
)
_OTHER_VALUE = {
    "dates": re.compile(
        r"(?:\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|"
        r"한두|두세|서너|네댓|대여섯|일|이|삼|사|오|육|칠|팔|구|십)\s*(?:명|인)"
        r"(?=$|[\s,.;!?？은는이가을를으로만도])"
    ),
    "quote": re.compile(r"\d[\d,.]*\s*(?:만\s*원?|원|억)|포함|별도|면세"),
}
_INQUIRY = re.compile(
    r"가능\s*(?:할지|한지|한가요|하나요|할까요|여부)|"
    r"(?:수\s*있|수\s*없)(?:는지|을지|나요|을까요)|"
    r"(?:해도|넣어도|되어도|돼도|잡아도|바꿔도|옮겨도|빼도|둬도|두어도|정해도|늘려도|줄여도|적어도|써도)\s*"
    r"(?:되(?:는지|나요|겠어요|겠습니까)|될(?:지|까요))|"
    r"(?:괜찮|맞)(?:은지|는지|을지|나요|을까요)|"
    r"(?:할|될|갈|잡을|옮길|바꿀|정할|넣을|뺄)(?:지|까(?:요)?)|"
    r"(?:[가-힣]까요|[가-힣]나요)(?=$|[\s,.!?？])|"
    r"(?:미정|보류|고민\s*(?:중|하고)|검토\s*중|모르겠|확실하지\s*않|가능하면|조건부|"
    r"(?:정|결정하|확정하|선택하)지\s*(?:못|않)|"
    r"(?:정하|결정하|확정하|선택하)지\s*(?:못|않)|"
    r"(?:할|잡을|넣을|정할|옮길|기입할|입력할|변경할)\s*생각|"
    r"정도|쯤|대략|가량|내외|안팎)"
)
_CONDITION = CONDITIONAL_LINK
_CHECK_REQUEST = re.compile(
    r"(?:확인|검토)\s*(?:부탁(?:합니다|드립니다|드려요|해요)|"
    r"해\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요|주실\s*수\s*있을까요))"
)

# A candidate request is an operation on a proposal, not a hypothetical
# instruction to execute a change. Only the completed, adjacent proposal
# object may consume its own "경우의" and polite interrogative ending.
_PREVIEW_REQUEST = re.compile(
    rf"{_PROPOSAL_OBJECT}\s*(?:(?:먼저|우선|한번|한\s*번)\s*)?"
    rf"(?:보여|만들어|작성해|제시해)\s*(?:{_POLITE_REQUEST_END})"
    r"\s*[?？.!。]?\s*$"
)
_PREVIEW_CONTENT = re.compile(
    rf"(?:(?:{DATE_CHANGE_ACTION}|확정|설정|선택|정하|정할)"
    r"(?:하는|할|는|을|ㄹ)?\s*(?P<nominal_case>경우의\s*)?)$"
)


def proposal_risk_text(clause: str) -> str | None:
    """Prove a complete proposal object and preserve outside risk spans."""
    request = _PREVIEW_REQUEST.search(clause)
    if request is None:
        return None
    content = _PREVIEW_CONTENT.search(clause[: request.start()].rstrip())
    if content is None:
        return None
    checked = clause[: request.start()] + " " * (len(clause) - request.start())
    if content["nominal_case"]:
        a, b = content.span("nominal_case")
        checked = checked[:a] + " " * (b - a) + checked[b:]
    return checked


# 직접적인 대입 금지는 문의와 마찬가지로 확정 후보가 될 수 없다.
# '9일은 변경하지 말고, 16일 대신 23일로 ...'처럼 보호할 날짜를
# 명시한 연결절은 기존 교체 관계 검증에서 따로 판단한다.
_DENIED_ASSIGNMENT = re.compile(
    r"(?:(?:확정|설정|지정|변경|수정|정정|제안|입력|등록|반영|선택|교체|제외|조정|연장|단축)\s*하지"
    r"|(?:잡|넣|바꾸|고치|옮기|빼|미루|당기|정하|적|쓰|늘리|줄이|두|하)지)"
    r"(?:는|도)?\s*(?:마(?:세요|십시오|요)?|말아\s*(?:주세요|줘요|주십시오)"
    r"|않(?:겠습니다|을게요|아요|습니다|았어요|았습니다))(?=$|[\s,.!?！？;。])"
)
_NEGATIVE_DATE_REQUEST = re.compile(
    r"(?<![가-힣])안\s*(?:(?:확정|설정|지정|변경|수정|정정|제안|입력|등록|반영|선택|교체|제외|조정|연장|단축)\s*하"
    r"|잡|넣|바꾸|고치|옮기|빼|미루|당기|정하|적|쓰|늘리|줄이|두|하)"
    r"(?:겠습니다|을게요|을래요|기로\s*했어요|기로\s*했습니다)(?=$|[\s,.!?！？;。])"
)
_WITHDRAW_REQUEST = re.compile(
    r"(?:(?:취소|철회)\s*(?:합니다|해요|할게요|하겠습니다|해\s*(?:주세요|줘요|주십시오))?"
    rf"|{WITHDRAW_IDIOM}(?:\s*(?:합니다|해요|할게요|하겠습니다|해\s*(?:주세요|줘요|주십시오)))?)"
    r"\s*[,，;.!。]?$"
)
_AFFIRMATIVE = re.compile(
    r"(?:확정|변경|수정|정정|제안|입력|등록|설정|반영|선택|교체|제외)"
    r"(?:해\s*(?:주세요|줘요|주십시오)|합니다|하겠습니다|할게요|하고|하며)|"
    r"(?:잡아|넣어|바꿔|고쳐|옮겨|미뤄|당겨|빼)\s*(?:주세요|줘요|주십시오)|"
    r"(?:으로|로)\s*해\s*(?:주세요|줘요|주십시오)"
)


def _duration_mentioned(text: str) -> bool:
    # 범위/근삿값도 대상 언급이다. 정확한 수량인지는 검증기에서 별도로 판단한다.
    return bool(duration_tokens(text) or duration_quantity_risk(text))


def _other_mentioned(form: str, text: str) -> bool:
    pattern = _OTHER_VALUE.get(form)
    return bool(
        (pattern and pattern.search(text))
        or (form == "dates" and crew_tokens(text))
        or (form == "quote" and written_money_tokens(text))
    )


def _field_clauses(form: str, sentence: str) -> list[tuple[str, frozenset[str]]]:
    """연결된 대상 명사와 값을 말한 뒤의 독립 대상을 구분한다."""
    # 공통 분석이 이미 분리한 서로 다른 명시 대상의 연결절은
    # 값/술어 목록이 부족하다는 이유로 다시 합치지 않는다.
    # 그 안의 날짜 목록/대상 생략/연동 조건은 아래 특화 검사에서 다룬다.
    shared = intent_clauses(form, sentence)
    cuts = [
        i
        for i, (left, right) in enumerate(pairwise(shared), 1)
        if left.boundary in ("connective", "question")
        and left.fields
        and right.fields
        and left.fields.isdisjoint(right.fields)
    ]
    if cuts:
        result = []
        for a, b in zip([0, *cuts], [*cuts, len(shared)], strict=True):
            start = 0 if a == 0 else shared[a - 1].end
            end = len(sentence) if b == len(shared) else shared[b - 1].end
            chunk = sentence[start:end]
            if b == a + 1 and shared[a].fields:
                result.append((chunk, shared[a].fields))
            else:
                result.extend(_field_clauses(form, chunk))
        return result
    # 명시 대상 없이 새 날짜가 이어지는 독립 술어도 분리한다.
    joins = sorted(
        [*_PREDICATE_JOIN.finditer(sentence), *_COMMA_AFTER_PREDICATE.finditer(sentence)],
        key=lambda match: match.end(),
    )
    for join in joins:
        tail = sentence[join.end() :].lstrip(" ,，")
        tail = re.sub(r"^(?:그리고|다만|반면)\s+", "", tail)
        other = _OTHER_VALUE.get(form)
        # 새 목적어가 생략된 수량 절도 앞의 확정 술어와 분리한다.
        if (
            _DATE.match(tail)
            or (other and other.match(tail))
            or (form == "quote" and any(a == 0 for a, _, _ in duration_tokens(tail)))
            or (form == "dates" and any(a == 0 for a, _, _ in crew_tokens(tail)))
            or (form == "quote" and any(a == 0 for a, _, _ in written_money_tokens(tail) or []))
        ):
            return _field_clauses(form, sentence[: join.end()]) + _field_clauses(form, tail)
    # 유지/상태 검증과 동일한 실제 필드 별칭을 사용한다. 유지 술어 속
    # '앞서 정한 날짜' 같은 참조 명사는 다른 목적어로 세지 않는다.
    scopes = retention_scopes(form, sentence)
    marks = [
        mention
        for mention in intent_target_mentions(form, sentence)
        if not any(scope.predicate_start <= mention.start < scope.end for scope in scopes)
    ]
    if not marks:
        return [(sentence, frozenset())]
    clauses: list[tuple[str, frozenset[str]]] = []
    cursor = 0
    targets: frozenset[str] = frozenset()
    last = None
    for mark in marks:
        if last is None:
            prefix = sentence[: mark.start]
            # 명시 대상 앞에 독립된 날짜 절이 있으면 분리한다.
            if (
                (_DATE.search(prefix) or (form == "quote" and _duration_mentioned(prefix)))
                and (_PREDICATE_JOIN.search(prefix) or re.search(r"[,，]", prefix))
                and _VALUE_OR_PREDICATE.search(prefix)
                and not (
                    form == "period"
                    and mark.fields == {"excluded"}
                    and date_list_prefix(prefix, allow_condition=True, allow_context_lead=True)
                )
            ):
                clauses.append((prefix, frozenset()))
                cursor = mark.start
            targets = mark.fields
        else:
            between = sentence[last.end : mark.start]
            if _FIELD_JOIN.fullmatch(between):
                targets |= mark.fields
            elif (
                _VALUE_OR_PREDICATE.search(between)
                or completed_inquiry(between)
                or retention_scopes(form, sentence[cursor : mark.start].strip(" ,，"))
                or (form == "quote" and _duration_mentioned(between))
            ):
                clauses.append((sentence[cursor : mark.start], targets))
                cursor = mark.start
                targets = mark.fields
            else:
                # 여러 대상이 묶였으나 값이 아직 없으면 함께 보류한다.
                targets |= mark.fields
        last = mark
    clauses.append((sentence[cursor:], targets))
    return clauses


def _default_date_fields(form: str, clause: str) -> frozenset[str]:
    if form == "dates":
        return frozenset(("days",)) if _DATE.search(clause) else frozenset()
    if form == "period" and _DATE.search(clause):
        if _EXCLUSION.search(clause):
            return frozenset(("excluded",))
        return frozenset(("start", "end"))
    if form == "quote":
        if _CALENDAR_DATE.search(clause):
            return frozenset(("valid",))
        if _duration_mentioned(clause):
            return frozenset(("days",))
    return frozenset()


def _other_value_fields(form: str, clause: str) -> frozenset[str]:
    if _other_mentioned(form, clause):
        return frozenset(("crew",)) if form == "dates" else frozenset(("amount", "vat"))
    return frozenset()


def _sentences(raw: str) -> list[str]:
    # 물음표는 앞 절에 남기고, 숫자 안의 마침표는 나누지 않는다.
    cursor = 0
    sentences = []
    for ending in [*_SENTENCE.finditer(raw), None]:
        end = ending.end() if ending else len(raw)
        sentences.append(raw[cursor:end])
        cursor = end
    return sentences


def _date_clause_targets(form: str, clause: str, targets: frozenset[str]) -> frozenset[str]:
    # A generic 견적 noun is not an amount assignment. A calendar value
    # immediately bound to the validity predicate has its own field owner.
    if (
        form == "quote"
        and targets in (frozenset(), frozenset(("amount",)))
        and re.search(rf"{_CALENDAR_DATE.pattern}\s*까지\s*유효(?:하|할|한|하게|하도록)", clause)
        and not _OTHER_VALUE["quote"].search(clause)
        and all(
            mention.fields == {"amount"}
            and clause[mention.start : mention.end] in ("견적", "견적서")
            for mention in field_mentions(form, clause)
        )
    ):
        return frozenset(("valid",))
    return targets or _default_date_fields(form, clause) or _other_value_fields(form, clause)


@dataclass(frozen=True)
class DateClauseFact:
    text: str
    value_fields: frozenset[str]
    risk_fields: frozenset[str]


def date_clause_facts(form: str, text: str) -> tuple[DateClauseFact, ...]:
    """One clause traversal; value ownership and inherited risk stay distinct."""
    if form not in _DATE_FIELDS:
        return ()
    result = []
    last_targets: frozenset[str] = frozenset()
    for sentence in _sentences(text):
        dependent = False
        for clause, targets in _field_clauses(form, sentence):
            if not clause.strip():
                continue
            values = _date_clause_targets(form, clause, targets)
            risk_targets = values or last_targets
            # Only a complete proposal object consumes its internal nominal
            # condition; an external condition remains visible.
            preview = proposal_risk_text(clause.strip())
            dependent = dependent or bool(
                _CONDITION.search(preview if preview is not None else clause)
            )
            risk = (
                risk_targets & _DATE_FIELDS[form]
                if dependent or clause_is_inquiry(clause.strip())
                else frozenset()
            )
            result.append(DateClauseFact(clause, values, risk))
            if risk_targets:
                last_targets = risk_targets
            if completed_inquiry(clause):
                dependent = False
    return tuple(result)


def date_value_clauses(form: str, text: str) -> tuple[tuple[str, frozenset[str]], ...]:
    """날짜 값 근거 검증에 쓸 독립 원문 절과 대상.

    단순 이어지는 문장에서 이전 대상을 추정해 값의 근거로 쓰지 않는다.
    날짜 정규화, 범위 경계와 검증된 교체 관계는 호출자가 판단한다.
    """
    return tuple((part.text, part.value_fields) for part in date_clause_facts(form, text))


def clause_is_inquiry(clause: str) -> bool:
    # '변경해 주실 수 있을까요?'는 실행을 요청하는 표현이다.
    # 그 앞에 별도 가능 여부 문의가 있으면 그것은 그대로 검증한다.
    preview = proposal_risk_text(clause)
    checked = preview if preview is not None else clause
    if (
        _DENIED_ASSIGNMENT.search(clause)
        or _NEGATIVE_DATE_REQUEST.search(clause)
        or _WITHDRAW_REQUEST.search(clause)
        or _CONDITION.search(checked)
    ):
        return True
    polite = POLITE_EXECUTION.search(checked)
    risk_text = checked[: polite.start()] if polite else checked
    # '안 늘려 주시겠어요?'처럼 정중한 종결 앞의 부정은 확정으로 바꾸지 않는다.
    if polite and re.search(r"(?<![가-힣])(?:안|못)\s*$", risk_text):
        return True
    # "일정도" is 일정 + 도, not the approximate quantity suffix 정도.
    # The distinction applies only to a complete schedule noun and never
    # removes approximation next to an actual number or duration.
    schedule_particle = {
        match.end() - 2 for match in re.finditer(r"(?<![가-힣\d])일정도(?=$|[\s,.!?？])", risk_text)
    }
    if any(
        not (match.group() == "정도" and match.start() in schedule_particle)
        for match in _INQUIRY.finditer(risk_text)
    ) or re.search(r"[?？]", risk_text):
        return True
    return bool(_CHECK_REQUEST.search(clause) and not _AFFIRMATIVE.search(clause))


def date_set_is_inquiry(form: str, field: str, text: str, evidence: str = "") -> bool:
    """해당 날짜 필드의 문의·미정·직접 대입 금지는 확정 후보가 아니다.

    원문 전체의 같은 대상 절을 검사한다. 짧게 잘린 evidence로 물음표나
    가능 여부를 숨길 수 없다. evidence는 원문이 없는 내부 호출의 대체값이다.
    """
    if field not in _DATE_FIELDS.get(form, ()):
        return False
    return any(field in part.risk_fields for part in date_clause_facts(form, text or evidence))


_TEXT_UNDECIDED_VALUE = re.compile(
    r"(?:아직\s*)?(?:미정|보류|고민\s*중|검토\s*중|결정\s*못\s*함|"
    r"정하지\s*않(?:음|았어요|았습니다)|모르겠(?:어요|습니다))"
    r"(?:입니다|이에요|이네요)?[.!。]?$"
)
_TEXT_REQUEST_TAIL = re.compile(
    r"(?:(?:정|확정|설정|지정|변경|수정|정정|제안|입력|등록|반영|선택|"
    r"기재|작성|표기|표시)(?:하|해|할)|"
    r"잡|넣|바꾸|바꿔|고치|고쳐|적|쓰|써|하|해)"
    r"[^,;.!?！？。]{0,32}"
    r"(?:까요|나요|는지|을지|할지|마세요|마십시오|말아\s*주세요|"
    r"않겠습니다|않을게요|않아요|않습니다)[?！？]?$"
)
_TEXT_UNCERTAIN_END = re.compile(
    r"(?:[가-힣](?:까요|나요)|모르겠(?:어요|습니다)|"
    r"확실하지\s*않(?:아요|습니다)|"
    r"고민\s*하고\s*있(?:어요|습니다)|"
    r"(?:취소|철회)\s*(?:합니다|해요|할게요|하겠습니다|해\s*(?:주세요|줘요|주십시오)))"
    r"[.!?！？。]?$"
)


_TEXT_HISTORICAL_REPORT = re.compile(
    rf"(?:{HISTORY_TIME}|예전에|과거에)\s*"
    rf"(?:(?:{_ASSIGNMENT_STEM})\s*(?:했|하였)|정했|적었|썼|말했|얘기했|말씀드렸)"
    r"(?:습니다|어요|죠|다)(?:[.!。]\s*)?$"
)


def _literal_text_value(value: str) -> bool:
    # 후보 전체에 질문이나 실행 취소 종결을 넣어도 값으로 가리지 않는다.
    # '고객 계약 취소' 같은 명사와 인용한 실제 값은 이 검사에서 보존한다.
    body = value.rstrip(".!?！？。 ")
    if (
        _TEXT_UNDECIDED_VALUE.fullmatch(value)
        or _TEXT_CLEAR_COMMAND.search(body)
        or value.endswith(("?", "？"))
        or _TEXT_UNCERTAIN_END.search(value)
        or re.search(rf"{WITHDRAW_IDIOM_REQUEST}$", body)
        or any(
            match.end() == len(body)
            for pattern in (_DENIED_ASSIGNMENT, _NEGATIVE_DATE_REQUEST)
            for match in pattern.finditer(body)
        )
    ):
        return False
    return not any(
        _TEXT_REQUEST_TAIL.fullmatch(value[mark.end() :].strip())
        for mark in _ASSIGNMENT_START.finditer(value)
    )


def _mask_text_literals(field: str, value: str, raw: str) -> str:
    """실제 문자열 값을 가리고, 값 밖의 요청·조건·질문은 남긴다."""
    selected = _space(value)
    if not selected:
        return raw
    masked = raw
    for match in re.finditer(re.escape(selected), raw, flags=re.IGNORECASE):
        a, b = match.span()
        quoted = _quoted_value(raw, a, b)
        if not quoted and not _literal_text_value(selected):
            continue
        # 다른 필드의 실제 값이나 요청 첫머리의 필드명은 지우지 않는다.
        prefix = re.split(r"[,;.!?！？。\n]", raw[:a])[-1]
        targets = field_mentions("extra", prefix)
        if targets and field not in targets[-1].fields:
            continue
        if (
            not quoted
            and not targets
            and any(
                mention.start == a and mention.end <= b for mention in field_mentions("extra", raw)
            )
        ):
            continue
        masked = masked[:a] + " " * (b - a) + masked[b:]
    return masked


def text_set_is_inquiry(field: str, value: str, text: str) -> bool:
    """제목/사유의 문자 그대로인 내용과 그 밖의 문의·취소·조건을 구분한다."""
    if field not in ("title", "reason"):
        return False
    raw = "\n".join(_space(line) for line in text.splitlines())
    masked = _mask_text_literals(field, value, raw)
    last_targets: frozenset[str] = frozenset()
    for sentence in _sentences(masked):
        dependent = False
        for clause, targets in _field_clauses("extra", sentence):
            if not clause.strip():
                continue
            targets = targets or last_targets
            dependent = dependent or bool(_CONDITION.search(clause))
            # 현재 대상이 없는 조건·부정은 다른 필드의 것으로 가정하지 않는다.
            if (not targets or field in targets) and (
                dependent
                or clause_is_inquiry(clause.strip())
                or re.search(
                    r"(?:또는|혹은|아니면|중에서|\s중\s).{0,40}(?:고민|미정|검토|결정\s*못)", clause
                )
                or _TEXT_HISTORICAL_REPORT.search(clause.strip())
                or _TEXT_CLEAR_COMMAND.search(clause.strip())
            ):
                return True
            if targets:
                last_targets = targets
    return False
