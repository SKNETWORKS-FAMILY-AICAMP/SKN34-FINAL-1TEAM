"""공정표 날짜 계산. 기대값은 시안 예시 집(상계동 32평, 도배 기본형 + 목공 몰딩 · 걸레받이)이다."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.calc import dobae, mokgong
from app.calc.common import Step
from app.calc.schedule import (
    SEOUL,
    Dated,
    TradePlan,
    contact_until,
    count_workdays,
    days_since,
    due_at,
    lay_out,
    order_conflicts,
    trade_days,
    workdays,
    years_since,
)
from app.refprice.book import DOBAE_2026H2 as D
from app.refprice.book import MOKGONG_2026H2 as M
from app.refprice.holidays import days_off


def example():
    d = dobae.quote(dobae.DobaeInput(Decimal("105.8"), "supply", "basic", True, False), D)
    m = mokgong.quote(
        mokgong.MokgongInput(Decimal("105.8"), "supply", ("molding", "base"), False), M
    )
    return [TradePlan("mokgong", m.steps, M.crew), TradePlan("dobae", d.steps, D.crew)]


def test_trade_days_round_half_days_up_to_whole_days():
    assert trade_days([Step("paper", "벽지", 4), Step("finish", "마감", 1)]) == 3
    assert trade_days([Step("a", "a", 2)]) == 1
    with pytest.raises(ValueError):
        trade_days([Step("a", "a", 0)])


def test_example_house_matches_the_plan():
    start = date(2026, 11, 16)
    plan = lay_out(example(), start, days_off(start, date(2026, 12, 31)))
    work = [(b.trade, b.start, b.end, b.days) for b in plan.blocks if b.kind == "work"]
    assert work == [
        ("mokgong", date(2026, 11, 16), date(2026, 11, 18), 3),
        ("dobae", date(2026, 11, 19), date(2026, 11, 23), 3),  # 21 · 22 주말은 건너뛴다
    ]
    dry, clean = plan.blocks[2], plan.blocks[3]
    assert (dry.kind, dry.start, dry.end) == ("dry", date(2026, 11, 24), date(2026, 11, 25))
    assert (clean.kind, clean.start) == ("clean", date(2026, 11, 26))
    assert plan.work_days == 6 and (plan.start, plan.end) == (start, date(2026, 11, 26))
    by_day = {t.day: t for t in plan.days}
    assert by_day[date(2026, 11, 21)].kind == "off"
    assert by_day[date(2026, 11, 23)].names == ("마감 · 확인 · 청소",)
    assert len(plan.days) == 11


def test_next_trade_starts_after_a_holiday():
    # 추석 연휴(9.24~26)와 주말을 건너뛴다
    start = date(2026, 9, 22)
    plan = lay_out(example(), start, days_off(start, date(2026, 10, 31)))
    mok, dob = plan.blocks[0], plan.blocks[1]
    assert (mok.start, mok.end) == (date(2026, 9, 22), date(2026, 9, 28))
    assert dob.start == date(2026, 9, 29)


def test_prep_is_kept_once_and_weekend_work_is_allowed():
    occupied = [Step("prep", "짐 정리", 1), Step("work", "작업", 2)]
    plan = lay_out(
        [TradePlan("mokgong", occupied, 2), TradePlan("dobae", occupied, 4)],
        date(2026, 11, 21),
        weekend=(),
    )
    names = [n for t in plan.days for n in t.names]
    assert names.count("짐 정리") == 1
    assert plan.blocks[0].start == date(2026, 11, 21)  # 토요일에도 일한다


def test_workdays_and_count_skip_weekends_and_days_off():
    assert workdays(date(2026, 11, 20), 2) == [date(2026, 11, 20), date(2026, 11, 23)]
    assert count_workdays(date(2026, 11, 19), date(2026, 11, 23)) == 3
    assert (
        count_workdays(
            date(2026, 9, 21), date(2026, 9, 28), days_off(date(2026, 9, 1), date(2026, 9, 30))
        )
        == 4
    )
    with pytest.raises(ValueError):
        count_workdays(date(2026, 11, 23), date(2026, 11, 19))
    with pytest.raises(ValueError):
        workdays(date(2026, 11, 20), 1, weekend=range(7))


def test_order_conflicts_when_later_trade_starts_too_early():
    ok = [
        Dated("mokgong", date(2026, 11, 16), date(2026, 11, 18)),
        Dated("dobae", date(2026, 11, 19), date(2026, 11, 23)),
    ]
    assert order_conflicts(ok, ("mokgong", "dobae")) == []
    bad = [ok[0], Dated("dobae", date(2026, 11, 18), date(2026, 11, 20))]
    assert order_conflicts(bad, ("mokgong", "dobae")) == [("mokgong", "dobae")]


def test_due_at_is_calendar_days_at_18_seoul():
    # 11.08(일) 10:12 서울에 확정 → 11.10(화) 18:00
    base = datetime(2026, 11, 8, 1, 12, tzinfo=UTC)
    assert due_at(base, 2) == datetime(2026, 11, 10, 18, 0, tzinfo=SEOUL)
    # 서울 날짜로 센다: UTC 11.07 16:00은 서울 11.08 01:00
    assert due_at(datetime(2026, 11, 7, 16, 0, tzinfo=UTC), 2).date() == date(2026, 11, 10)
    with pytest.raises(ValueError):
        due_at(datetime(2026, 11, 8, 10, 0), 2)


def test_contact_until_is_one_year_later():
    assert contact_until(date(2026, 11, 23)) == date(2027, 11, 23)
    assert contact_until(date(2028, 2, 29)) == date(2029, 2, 28)


def test_days_since_counts_seoul_dates():
    today = date(2026, 11, 2)
    # 10.30(금) 18:47 서울 신청 → 11.02(월)에 3일째
    assert days_since(datetime(2026, 10, 30, 9, 47, tzinfo=UTC), today) == 3
    # UTC 10.31 15:30은 서울 11.01 00:30 → 1일째
    assert days_since(datetime(2026, 10, 31, 15, 30, tzinfo=UTC), today) == 1
    assert days_since(datetime(2026, 11, 2, 0, 0, tzinfo=SEOUL), today) == 0
    assert days_since(datetime(2026, 11, 5, 0, 0, tzinfo=SEOUL), today) == 0  # 앞날은 0
    with pytest.raises(ValueError):
        days_since(datetime(2026, 11, 1, 10, 0), today)


def test_years_since_counts_full_years():
    assert years_since(date(2014, 3, 2), date(2026, 11, 2)) == 12  # 2014.03 · 업력 12년
    assert years_since(date(2014, 11, 3), date(2026, 11, 2)) == 11  # 하루 모자람
    assert years_since(date(2014, 11, 2), date(2026, 11, 2)) == 12
    assert years_since(date(2027, 1, 1), date(2026, 11, 2)) == 0  # 앞날은 0
