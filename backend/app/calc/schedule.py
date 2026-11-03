"""공정표 날짜 계산: 공정을 하루 단위로 잇고, 공사 날짜 · 기한 · 연락처 기한을 센다.

- 공정 기간: 단계마다 반나절 단위로 센 것을 더해 하루 단위로 올린다(공정 계산이 단계를 만든다).
- 잇기: 넘긴 순서대로 공정을 놓고, 뒤 공정은 앞 공정이 끝난 다음 일하는 날 시작한다.
  일하는 날은 주말 요일과 쉬는 날(공휴일)을 뺀 날이다. 짐 정리 단계는 처음 한 번만 둔다.
- 도배가 있으면 끝난 다음 날부터 자연 건조(달력 날짜), 그다음 입주 청소(고객이 직접)를 둔다.
- 기한: 기준 시각의 서울 날짜 + N일의 18:00(달력 날짜).
- 연락처 기한: 업체가 마지막으로 '공사 끝냈어요'를 올린 날의 1년 뒤 같은 날(2월 29일이면 2월 28일).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.calc.common import Step
from app.shared.clock import SEOUL

WEEKEND = (5, 6)  # 토 · 일 (date.weekday 값)
MAX_SPAN_DAYS = 3 * 366  # 이만큼 넘겨도 일할 날이 모자라면 멈춘다
JOB_MAX_DAYS = 120  # 업체가 낼 수 있는 한 공정의 공사 날짜 범위
CONTACT_YEARS = 1  # 전문공사 하자담보책임기간(1~3년) 중 가장 짧은 것
DRY_DAYS = 2  # 도배 뒤 자연 건조 (도배 시방서: 창문을 닫고 천천히 말린다)
CLEAN_DAYS = 1  # 입주 청소 (고객이 직접)
DUE_HOUR = 18

BlockKind = Literal["work", "dry", "clean"]


@dataclass(frozen=True)
class TradePlan:
    """공정 하나: 공정 계산이 낸 단계와 하루 인원."""

    trade: str
    steps: Sequence[Step]
    crew: int


@dataclass(frozen=True)
class Block:
    trade: str | None  # 건조 · 청소는 공정이 없다
    kind: BlockKind
    label: str
    start: date
    end: date
    days: int  # 일하는 날(작업) 또는 달력 날짜(건조 · 청소)
    crew: int | None


@dataclass(frozen=True)
class DayTask:
    day: date
    trade: str | None
    kind: BlockKind | Literal["off"]
    names: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    blocks: list[Block]
    days: list[DayTask]  # 시작일부터 마지막 날까지 하루씩(쉬는 날 포함)
    work_days: int  # 작업하는 날 수 (건조 · 청소 빼고)
    start: date
    end: date


def is_off(d: date, days_off: Collection[date], weekend: Collection[int]) -> bool:
    return d.weekday() in weekend or d in days_off


def workdays(
    start: date,
    count: int,
    days_off: Collection[date] = (),
    weekend: Collection[int] = WEEKEND,
) -> list[date]:
    """start부터 일하는 날 count개. start가 쉬는 날이면 다음 일하는 날부터 센다."""
    if count < 0 or not set(weekend) <= set(range(7)) or len(set(weekend)) == 7:
        raise ValueError("일하는 날 수와 주말 요일을 다시 확인해 주세요")
    out: list[date] = []
    d, last = start, start + timedelta(days=MAX_SPAN_DAYS)
    while len(out) < count:
        if d > last:
            raise ValueError("쉬는 날이 너무 많아 일할 날을 찾지 못했어요")
        if not is_off(d, days_off, weekend):
            out.append(d)
        d += timedelta(days=1)
    return out


def trade_days(steps: Sequence[Step]) -> int:
    """단계 반나절을 더해 하루 단위로 올린다."""
    halves = sum(s.half_days for s in steps)
    if halves < 1 or any(s.half_days < 1 for s in steps):
        raise ValueError("단계는 반나절 이상이어야 해요")
    return (halves + 1) // 2


def lay_out(
    trades: Sequence[TradePlan],
    start: date,
    days_off: Collection[date] = (),
    weekend: Collection[int] = WEEKEND,
) -> Plan:
    """공정을 순서대로 하루 단위로 잇는다. trades가 비면 ValueError."""
    if not trades:
        raise ValueError("공정이 하나 이상 있어야 해요")
    seen_prep = False
    blocks: list[Block] = []
    tasks: dict[date, tuple[str | None, BlockKind, list[str]]] = {}
    cursor = start
    for tp in trades:
        steps = [s for s in tp.steps if not (s.key == "prep" and seen_prep)]
        seen_prep = seen_prep or any(s.key == "prep" for s in steps)
        n = trade_days(steps)
        days = workdays(cursor, n, days_off, weekend)
        pos = 0
        for s in steps:
            for half in range(pos, pos + s.half_days):
                d = days[half // 2]
                names = tasks.setdefault(d, (tp.trade, "work", []))[2]
                if s.name not in names:
                    names.append(s.name)
            pos += s.half_days
        blocks.append(Block(tp.trade, "work", "", days[0], days[-1], n, tp.crew))
        cursor = days[-1] + timedelta(days=1)
    work_end = blocks[-1].end
    if any(tp.trade == "dobae" for tp in trades):
        dry_start = work_end + timedelta(days=1)
        dry_end = dry_start + timedelta(days=DRY_DAYS - 1)
        blocks.append(Block(None, "dry", "자연 건조", dry_start, dry_end, DRY_DAYS, None))
        for i in range(DRY_DAYS):
            tasks[dry_start + timedelta(days=i)] = (None, "dry", ["창문을 닫고 자연 건조"])
        work_end = dry_end
    clean_start = work_end + timedelta(days=1)
    clean_end = clean_start + timedelta(days=CLEAN_DAYS - 1)
    blocks.append(Block(None, "clean", "입주 청소", clean_start, clean_end, CLEAN_DAYS, None))
    for i in range(CLEAN_DAYS):
        tasks[clean_start + timedelta(days=i)] = (None, "clean", ["입주 청소 · 고객이 직접"])

    first = blocks[0].start
    out: list[DayTask] = []
    d = first
    while d <= clean_end:
        if d in tasks:
            trade, kind, names = tasks[d]
            out.append(DayTask(d, trade, kind, tuple(names)))
        else:
            out.append(DayTask(d, None, "off", ()))
        d += timedelta(days=1)
    work = sum(b.days for b in blocks if b.kind == "work")
    return Plan(blocks, out, work, first, clean_end)


def count_workdays(
    date_from: date,
    date_to: date,
    days_off: Collection[date] = (),
    weekend: Collection[int] = WEEKEND,
) -> int:
    """시작일부터 끝날까지(둘 다 넣어서) 일하는 날 수."""
    span = (date_to - date_from).days
    if span < 0 or span > JOB_MAX_DAYS:
        raise ValueError("공사 날짜 범위를 다시 확인해 주세요")
    days = (date_from + timedelta(days=i) for i in range(span + 1))
    return sum(1 for d in days if not is_off(d, days_off, weekend))


@dataclass(frozen=True)
class Dated:
    trade: str
    date_from: date
    date_to: date


def order_conflicts(dated: Sequence[Dated], order: Sequence[str]) -> list[tuple[str, str]]:
    """순서(앞 → 뒤)가 정해진 공정에서 뒤 공정이 앞 공정이 끝나기 전에 시작하면 (앞, 뒤)."""
    if any(d.date_to < d.date_from for d in dated):
        raise ValueError("끝날이 시작일보다 앞이에요")
    by = {d.trade: d for d in dated}
    ranked = [t for t in order if t in by]
    out = []
    for i, a in enumerate(ranked):
        for b in ranked[i + 1 :]:
            if by[b].date_from <= by[a].date_to:
                out.append((a, b))
    return out


def due_at(base: datetime, days: int, hour: int = DUE_HOUR) -> datetime:
    """기준 시각의 서울 날짜 + days일 hour시(서울)."""
    if base.tzinfo is None or days < 0:
        raise ValueError("기준 시각은 시간대가 있어야 하고 일수는 0 이상이어야 해요")
    day = base.astimezone(SEOUL).date() + timedelta(days=days)
    return datetime.combine(day, time(hour), SEOUL)


def contact_until(work_done: date) -> date:
    """연락처가 서로 보이는 마지막 날: 1년 뒤 같은 날 (2월 29일이면 2월 28일)."""
    year = work_done.year + CONTACT_YEARS
    try:
        return work_done.replace(year=year)
    except ValueError:
        return work_done.replace(year=year, day=28)


def years_since(start: date, today: date) -> int:
    """start부터 오늘까지 꽉 찬 햇수 (업력). 아직 안 온 날이면 0."""
    years = today.year - start.year - ((today.month, today.day) < (start.month, start.day))
    return max(years, 0)


def days_since(at: datetime, today: date) -> int:
    """그 시각의 서울 날짜부터 오늘까지 지난 날 수 (같은 날이면 0). 심사 기다린 날에 쓴다."""
    if at.tzinfo is None:
        raise ValueError("시각은 시간대가 있어야 해요")
    return max((today - at.astimezone(SEOUL).date()).days, 0)
