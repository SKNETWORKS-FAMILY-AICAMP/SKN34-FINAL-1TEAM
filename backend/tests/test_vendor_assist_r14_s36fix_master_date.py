from datetime import date

import pytest

from app.vendor.assist_date_edit import date_replacement_requested, validated_date_edit
from app.vendor.assist_schemas import AssistContext


@pytest.fixture
def context():
    return AssistContext(today=date(2026,11,26),base_month=date(2027,2,1),required_days=2,days_max=30)


@pytest.mark.parametrize("text",[
    "첫 방문 2월 9일만 2월 12일로 밀어 주세요. 두 번째 날짜는 기존대로요.",
    "첫 번째 방문일 2월 9일을 2월 12일로 미뤄 주세요. 둘째 날은 그대로요.",
    "방문일 2월 9일을 2월 12일로 옮겨 주세요.",
])
def test_owned_member_move(context,text):
    before=["2027-02-09","2027-02-20"]
    assert date_replacement_requested("days",text)
    assert validated_date_edit("days",text,before,context,require_affirmative=True)==["2027-02-12","2027-02-20"]
    assert before==["2027-02-09","2027-02-20"]


@pytest.mark.parametrize("text",[
    "두 번째 방문 2월 9일을 2월 12일로 밀어 주세요.",
    "첫 방문 2월 9일을 2월 12일로 밀어 주세요. 첫 번째 날짜는 그대로요.",
    "첫 방문 2월 9일을 2월 12일로 밀어 주세요. 세 번째 날짜는 그대로요.",
    "첫 방문 2월 9일을 2월 5일로 밀어 주세요.",
    "가능하면 첫 방문 2월 9일을 2월 12일로 밀어 주세요.",
    "첫 방문 2월 9일을 2월 12일로 밀어 주세요. 두 번째 날짜는 변경해 주세요.",
])
def test_conflicting_or_unresolved_member_move(context,text):
    assert validated_date_edit("days",text,["2027-02-09","2027-02-20"],context,require_affirmative=True) is None
