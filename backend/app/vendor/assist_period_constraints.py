"""업체 도우미의 기간 제약과 위반 필드 귀속.

원문/의도 검증과 달리 실제 폼 및 검증된 후보만 입력으로 받는다.
미적용 후보를 제거한 뒤에는 반드시 실제 폼을 다시 투영한다.
"""

from dataclasses import dataclass
from datetime import date

from app.vendor.assist_schemas import CandidateField, Value


@dataclass(frozen=True)
class PeriodViolation:
    fields: frozenset[str]
    exclusion_boundary: str | None = None


def _day(value: Value) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def period_violations(
    state: dict[str, Value], today: date, *, strict_interior: bool = True
) -> tuple[PeriodViolation, ...]:
    """같은 제약의 실패를 실제 참여 필드로 표현한다.

    경계의 구조/순서가 틀렸다면 먼저 이를 고친 뒤 제외일 관계를
    검사한다. 그렇지 않으면 잘못된 경계 때문에 독립 목록도 사라진다.
    """
    start_raw, end_raw = state.get("start"), state.get("end")
    invalid = []
    for field, raw in (("start", start_raw), ("end", end_raw)):
        if raw is not None and not isinstance(raw, str):
            invalid.append(PeriodViolation(frozenset({field})))
        else:
            try:
                _day(raw)
            except ValueError:
                invalid.append(PeriodViolation(frozenset({field})))
    if invalid:
        return tuple(invalid)
    start, end = _day(start_raw), _day(end_raw)
    if start and start <= today:
        invalid.append(PeriodViolation(frozenset({"start"})))
    if end and not start or start and end and (end < start or (end - start).days > 62):
        invalid.append(PeriodViolation(frozenset({"start", "end"})))
    if invalid:
        return tuple(invalid)
    excluded = state.get("excluded")
    if excluded is None:
        return ()
    if (
        not isinstance(excluded, list)
        or any(not isinstance(value, str) for value in excluded)
        or len(excluded) > 31
        or len(set(excluded)) != len(excluded)
    ):
        return (PeriodViolation(frozenset({"excluded"})),)
    try:
        days = [date.fromisoformat(value) for value in excluded]
    except ValueError:
        return (PeriodViolation(frozenset({"excluded"})),)
    if not days:
        return ()
    if start is None or any(day < start or strict_interior and day == start for day in days):
        invalid.append(PeriodViolation(frozenset({"start", "excluded"}), "start"))
    if end is None or any(day > end or strict_interior and day == end for day in days):
        invalid.append(PeriodViolation(frozenset({"end", "excluded"}), "end"))
    return tuple(invalid)


def period_state(current: dict[str, Value], active: dict[str, CandidateField]) -> dict[str, Value]:
    """검증된 변경만 투영하며 실제 폼/후보를 변경하지 않는다."""
    return {**current, **{field: item.value for field, item in active.items()}}


def _retained(item: CandidateField) -> bool:
    # 파생 목록은 계승 중에도 value_source=derived를 보존한다.
    return item.intent_kind == "retain_previous" or item.value_source == "previous"


def _boundary_conflict(state: dict[str, Value], edge: str, *, strict_interior: bool) -> bool:
    """한 경계와 목록의 관계만 검사한다. 다른 제약은 재투영 뒤 검사한다."""
    excluded = state.get("excluded")
    if excluded is None or excluded == []:
        return False
    if not isinstance(excluded, list):
        return True
    try:
        boundary = _day(state.get(edge))
        days = [date.fromisoformat(value) for value in excluded]
    except (ValueError, TypeError):
        return True
    return boundary is None or any(
        (day < boundary if edge == "start" else day > boundary)
        or strict_interior
        and day == boundary
        for day in days
    )


def period_responsible_fields(
    violations: tuple[PeriodViolation, ...],
    current: dict[str, Value],
    active: dict[str, CandidateField],
    *,
    strict_interior: bool,
) -> set[str]:
    rejected: set[str] = set()
    state = period_state(current, active)
    for violation in violations:
        participants = violation.fields & active.keys()
        edge = violation.exclusion_boundary
        if edge is not None and participants == {edge, "excluded"}:
            # 새 경계 날짜 자체를 새 제외일로 지정하는 지시는 서로
            # 양립할 수 없다. 경계만 되돌려 제외일을 확정하면 원래
            # 요청의 의미를 바꾸므로 두 후보 모두 보류한다.
            if (
                strict_interior
                and not _retained(active[edge])
                and not _retained(active["excluded"])
                and isinstance(state.get("excluded"), list)
                and state.get(edge) in state["excluded"]
            ):
                rejected.update(participants)
                continue
            # 각 후보만 되돌리는 반사실 검증으로, 원래 경계와도 맞지
            # 않는 새 목록이 정상 경계까지 없애는 것을 방지한다.
            repairs = {
                field
                for field in participants
                if not _boundary_conflict(
                    {**state, field: current.get(field)},
                    edge,
                    strict_interior=strict_interior,
                )
            }
            if len(repairs) == 1:
                participants = repairs
            elif len(repairs) == 2:
                # 양쪽 중 하나만 복구해도 되는 경우, 검증된 이전 후보를
                # 새 경쟁 후보보다 우선 보존한다. 동일 수준이면 보류.
                old_edge, old_list = _retained(active[edge]), _retained(active["excluded"])
                if old_edge != old_list:
                    participants = {"excluded"} if old_edge else {edge}
        rejected.update(participants)
    return rejected


def resolve_period_candidates(
    current: dict[str, Value],
    candidates: dict[str, CandidateField],
    today: date,
    *,
    strict_interior: bool = True,
) -> tuple[dict[str, CandidateField], set[str]]:
    """업무 제약 위반의 책임 후보만 제거하고 현재값으로 재검증한다.

    참여 후보를 전부 제거해도 유효하지 않은 현재 조합에서는 추가
    변경을 안전하다고 주장하지 않는다. 반복마다 후보 수가 감소한다.
    """
    active = {
        field: item
        for field, item in candidates.items()
        if item.status == "valid" and item.action in ("set", "clear")
    }
    rejected: set[str] = set()
    while active:
        violations = period_violations(
            period_state(current, active), today, strict_interior=strict_interior
        )
        if not violations:
            break
        failures = period_responsible_fields(
            violations, current, active, strict_interior=strict_interior
        ) or set(active)
        rejected.update(failures)
        active = {field: item for field, item in active.items() if field not in failures}
    return active, rejected
