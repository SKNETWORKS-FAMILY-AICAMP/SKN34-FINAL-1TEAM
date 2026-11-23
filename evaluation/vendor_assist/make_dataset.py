"""업체 도우미의 고정 한국어 평가 문장 생성. 사람이 검수하기 전까지 AI 초안이다."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

FIELDS = {
    "period": ("start", "end", "excluded"),
    "quote": ("amount", "vat", "days", "valid"),
    "dates": ("days", "crew"),
    "extra": ("title", "reason", "amount", "vat"),
}
EMPTY = {
    "period": {"start": None, "end": None, "excluded": []},
    "quote": {"amount": None, "vat": None, "days": None, "valid": None},
    "dates": {"days": [], "crew": 2},
    "extra": {"title": "", "reason": "", "amount": None, "vat": None},
}
CLEAR = ("clear", None)
UNSURE = ("uncertain", None)
HERE = Path(__file__).resolve().parent


def s(text, changes=None, *, current=None, previous=None, context=None, tags=()):
    return {
        "text": text,
        "changes": changes or {},
        "current": current or {},
        "previous": previous,
        "context": context or {},
        "tags": list(tags),
    }


def p(text, **fields):
    """이전 메시지에서 명시한 필드: (값, 원문 근거)."""
    return {"text": text, "fields": fields}


# 각 범주 세 사례 중 앞의 둘은 dev, 마지막은 heldout으로 고정한다.
CASES = {
    "period": {
        "simple": [
            s(
                "12월 2일부터 12월 5일까지 실측 가능해요.",
                {"start": "2026-12-02", "end": "2026-12-05"},
            ),
            s(
                "12월 11일에서 14일까지 방문할 수 있습니다.",
                {"start": "2026-12-11", "end": "2026-12-14"},
            ),
            s(
                "12월 18일 하루만 실측 가능합니다.",
                {"start": "2026-12-18", "end": "2026-12-18"},
            ),
        ],
        "multi_field": [
            s(
                "12/2부터 12/6까지 가능합니다. 12/4는 안 돼요.",
                {
                    "start": "2026-12-02",
                    "end": "2026-12-06",
                    "excluded": ["2026-12-04"],
                },
                tags=("multi_date_exclusion",),
            ),
            s(
                "12월 7일부터 10일까지 가능하고 8일과 9일은 빼주세요.",
                {
                    "start": "2026-12-07",
                    "end": "2026-12-10",
                    "excluded": ["2026-12-08", "2026-12-09"],
                },
                tags=("multi_date_exclusion",),
            ),
            s(
                "12월 15일부터 20일까지 되는데 17일, 18일은 못 갑니다.",
                {
                    "start": "2026-12-15",
                    "end": "2026-12-20",
                    "excluded": ["2026-12-17", "2026-12-18"],
                },
                tags=("multi_date_exclusion",),
            ),
        ],
        "partial": [
            s(
                "실측 시작일만 12월 3일로 입력해 주세요.",
                {"start": "2026-12-03"},
                current={"end": "2026-12-09"},
            ),
            s(
                "끝나는 날은 12월 21일로 정했습니다. 시작은 그대로예요.",
                {"end": "2026-12-21"},
                current={"start": "2026-12-18"},
            ),
            s(
                "기존 실측 기간은 그대로 두고 제외 목록에 12월 5일을 추가해 주세요.",
                {"excluded": ["2026-12-05"]},
                current={"start": "2026-12-02", "end": "2026-12-06"},
            ),
        ],
        "negation": [
            s(
                "12월 4일은 방문 못 해요. 기존 시작과 종료는 바꾸지 마세요.",
                {"excluded": ["2026-12-04"]},
                current={"start": "2026-12-02", "end": "2026-12-06"},
            ),
            s(
                "12/8은 불가합니다. 나머지 기간은 유지 부탁드려요.",
                {"excluded": ["2026-12-08"]},
                current={"start": "2026-12-07", "end": "2026-12-10"},
            ),
            s(
                "12월 19일은 안 되고 다른 가능한 날짜는 그대로입니다.",
                {"excluded": ["2026-12-19"]},
                current={"start": "2026-12-17", "end": "2026-12-21"},
            ),
        ],
        "double_negation": [
            s(
                "12/2부터 12/5까지 가능하고 12/4도 어렵지 않아요.",
                {"start": "2026-12-02", "end": "2026-12-05"},
                tags=("double_negation",),
            ),
            s(
                "12/7~12/11 됩니다. 9일은 못 가는 건 아니에요.",
                {"start": "2026-12-07", "end": "2026-12-11"},
                tags=("double_negation",),
            ),
            s(
                "12/12부터 16일까지 괜찮고 14일도 안 되는 건 아닙니다.",
                {"start": "2026-12-12", "end": "2026-12-16"},
                tags=("double_negation",),
            ),
        ],
        "correction": [
            s(
                "12월 2일부터가 아니라 3일부터 6일까지 가능해요.",
                {"start": "2026-12-03", "end": "2026-12-06"},
                tags=("date_correction",),
            ),
            s(
                "실측 종료일 12/9 아니고 12/10으로 정정할게요.",
                {"end": "2026-12-10"},
                tags=("date_correction",),
            ),
            s(
                "12/12~17이라고 썼는데 12/13~18로 고쳐 주세요.",
                {"start": "2026-12-13", "end": "2026-12-18"},
                tags=("date_correction",),
            ),
        ],
        "previous_correction": [
            s(
                "아까 시작일은 2일이라고 했는데 3일로 변경할게요. 끝날은 그대로.",
                {"start": "2026-12-03"},
                previous=p(
                    "12월 2일부터 5일까지 가능합니다.",
                    start=("2026-12-02", "12월 2일"),
                    end=("2026-12-05", "5일까지"),
                ),
            ),
            s(
                "빼는 날은 4일 말고 5일로 고쳐 주세요. 기간은 전과 같아요.",
                {"excluded": ["2026-12-05"]},
                previous=p(
                    "12월 2일부터 7일까지, 4일은 제외해요.",
                    start=("2026-12-02", "12월 2일"),
                    end=("2026-12-07", "7일까지"),
                    excluded=(["2026-12-04"], "4일은 제외"),
                ),
            ),
            s(
                "종료일은 13일 대신 14일로 부탁해요. 나머지는 이전대로.",
                {"end": "2026-12-14"},
                previous=p(
                    "12월 10일부터 13일까지 가능해요.",
                    start=("2026-12-10", "12월 10일"),
                    end=("2026-12-13", "13일까지"),
                ),
            ),
        ],
        "previous_keep": [
            s(
                "기존 기간은 유지하고 12월 4일만 제외해 주세요.",
                {"excluded": ["2026-12-04"]},
                previous=p(
                    "12/2부터 12/6까지 됩니다.",
                    start=("2026-12-02", "12/2"),
                    end=("2026-12-06", "12/6"),
                ),
            ),
            s(
                "종료일만 12월 11일로 늘려요. 원래 제외 날짜는 유지하고요.",
                {"end": "2026-12-11"},
                previous=p(
                    "12/7~12/10 가능, 12/8은 제외합니다.",
                    start=("2026-12-07", "12/7"),
                    end=("2026-12-10", "12/10"),
                    excluded=(["2026-12-08"], "12/8은 제외"),
                ),
            ),
            s(
                "앞서 얘기한 날은 그대로고 12/16만 제외해 주세요.",
                {"excluded": ["2026-12-16"]},
                previous=p(
                    "12월 14일부터 18일까지 돼요.",
                    start=("2026-12-14", "12월 14일"),
                    end=("2026-12-18", "18일까지"),
                ),
            ),
        ],
        "clear": [
            s(
                "입력해 둔 실측 시작일만 지워 주세요.",
                {"start": CLEAR},
                current={"start": "2026-12-02"},
            ),
            s(
                "종료 날짜를 삭제해 주세요. 아직 정한 게 없어요.",
                {"end": CLEAR},
                current={"end": "2026-12-09"},
            ),
            s(
                "제외한 날짜 목록을 전부 비워 주세요.",
                {"excluded": CLEAR},
                current={"excluded": ["2026-12-04"]},
            ),
        ],
        "ambiguous": [
            s("시작은 다음 주 초쯤 될 것 같습니다.", {"start": UNSURE}),
            s("끝나는 날은 주말 전후로 잡아주세요.", {"end": UNSURE}),
            s("제외해야 할 날이 하루 정도 생길 수도 있어요.", {"excluded": UNSURE}),
        ],
        "conflict": [
            s("시작은 12/4로 할게요. 아니 12/6일 수도 있겠어요.", {"start": UNSURE}),
            s("끝나는 날짜가 12/10인지 12/11인지 확신이 안 서요.", {"end": UNSURE}),
            s(
                "12월 7일은 가능한 날이라고 했는데 못 갈 수도 있어요.",
                {"excluded": UNSURE},
            ),
        ],
        "no_action": [
            s("실측 가능한 날짜는 전화로 나중에 확정하겠습니다."),
            s("지난번에 입력한 기간 잘 봤습니다. 감사합니다."),
            s("먼저 도면 상담부터 하고 일정은 추후 알려드릴게요."),
        ],
    },
    "quote": {
        "simple": [
            s("실측 견적은 455만 원입니다.", {"amount": 4_550_000}),
            s("부가세는 포함이고 공사는 3일 걸립니다.", {"vat": "included", "days": 3}),
            s("견적 유효기간은 12월 20일까지예요.", {"valid": "2026-12-20"}),
        ],
        "multi_field": [
            s(
                "견적 455만 원, 부가세 포함, 공사 3일, 12/17까지 유효합니다.",
                {
                    "amount": 4_550_000,
                    "vat": "included",
                    "days": 3,
                    "valid": "2026-12-17",
                },
            ),
            s(
                "총 630만 원이고 부가세 별도, 작업 4일, 유효기간 12/22까지입니다.",
                {
                    "amount": 6_300_000,
                    "vat": "excluded",
                    "days": 4,
                    "valid": "2026-12-22",
                },
            ),
            s(
                "견적 금액 320만 원, 면세, 시공 기간 2일, 12월 30일까지 유효해요.",
                {
                    "amount": 3_200_000,
                    "vat": "exempt",
                    "days": 2,
                    "valid": "2026-12-30",
                },
            ),
        ],
        "partial": [
            s(
                "공사 기간만 5일로 넣어 주세요.",
                {"days": 5},
                current={"amount": 4_550_000, "vat": "included"},
            ),
            s(
                "다른 칸은 그대로 두고 견적 금액 490만 원으로 바꿔 주세요.",
                {"amount": 4_900_000},
                current={"vat": "excluded", "days": 3},
            ),
            s(
                "유효기간만 12월 28일까지로 설정해요.",
                {"valid": "2026-12-28"},
                current={"amount": 5_000_000},
            ),
        ],
        "negation": [
            s(
                "부가세 포함 아닙니다. 아직 방식을 못 정했어요.",
                {"vat": UNSURE},
                tags=("vat_negation",),
            ),
            s(
                "견적 455만 원은 아니고 555만 원이에요.",
                {"amount": 5_550_000},
                tags=("amount_correction",),
            ),
            s(
                "공사 3일은 아닙니다. 기간은 다시 알려드릴게요.",
                {"days": UNSURE},
                tags=("duration_correction",),
            ),
        ],
        "double_negation": [
            s(
                "면세는 아니고 부가세 포함으로 처리해 주세요.",
                {"vat": "included"},
                tags=("vat_negation", "double_negation"),
            ),
            s(
                "부가세를 별도로 안 받는 건 아니에요. 부가세 별도입니다.",
                {"vat": "excluded"},
                tags=("vat_negation", "double_negation"),
            ),
            s(
                "공사 3일이 불가능한 건 아닙니다. 3일로 적어 주세요.",
                {"days": 3},
                tags=("double_negation",),
            ),
        ],
        "correction": [
            s(
                "견적 455만 원에서 555만 원으로 변경했습니다.",
                {"amount": 5_550_000},
                tags=("amount_correction",),
            ),
            s(
                "공사 3일 아니고 5일 걸립니다.",
                {"days": 5},
                tags=("duration_correction",),
            ),
            s(
                "유효 12/17까지라고 썼는데 12/20까지로 정정할게요.",
                {"valid": "2026-12-20"},
            ),
        ],
        "previous_correction": [
            s(
                "아까 견적 455만 원이라고 했는데 555만 원으로 고칠게요. 공사 기간은 그대로.",
                {"amount": 5_550_000},
                previous=p(
                    "견적 455만 원이고 공사 3일입니다.",
                    amount=(4_550_000, "455만 원"),
                    days=(3, "공사 3일"),
                ),
                tags=("amount_correction",),
            ),
            s(
                "공사일만 3일 말고 5일로 바꿔요. 금액은 이전 그대로요.",
                {"days": 5},
                previous=p(
                    "견적 520만 원이고 공사 3일 걸려요.",
                    amount=(5_200_000, "520만 원"),
                    days=(3, "공사 3일"),
                ),
                tags=("duration_correction",),
            ),
            s(
                "지난번 부가세 별도라고 한 건 취소하고 포함으로 부탁합니다.",
                {"vat": "included"},
                previous=p(
                    "견적 420만 원, 부가세 별도예요.",
                    amount=(4_200_000, "420만 원"),
                    vat=("excluded", "부가세 별도"),
                ),
                tags=("vat_negation",),
            ),
        ],
        "previous_keep": [
            s(
                "기존 견적이랑 기간은 그대로 두고 유효일만 12/23으로요.",
                {"valid": "2026-12-23"},
                previous=p(
                    "견적 430만 원, 공사 4일입니다.",
                    amount=(4_300_000, "430만 원"),
                    days=(4, "공사 4일"),
                ),
            ),
            s(
                "금액은 아까 그대로예요. 부가세만 면세로 고쳐 주세요.",
                {"vat": "exempt"},
                previous=p("견적 금액 340만 원입니다.", amount=(3_400_000, "340만 원")),
            ),
            s(
                "유효기간은 앞에서 말한 대로고 공사 2일로 정할게요.",
                {"days": 2},
                previous=p(
                    "견적 유효기간은 12/27까지입니다.",
                    valid=("2026-12-27", "12/27까지"),
                ),
            ),
        ],
        "clear": [
            s(
                "입력해 둔 견적 금액을 삭제해주세요.",
                {"amount": CLEAR},
                current={"amount": 4_500_000},
            ),
            s(
                "부가세 방식 선택을 지우겠습니다.",
                {"vat": CLEAR},
                current={"vat": "included"},
            ),
            s(
                "공사 기간 칸을 비워 주세요. 미정입니다.",
                {"days": CLEAR},
                current={"days": 3},
            ),
        ],
        "ambiguous": [
            s("견적은 500만 원 안팎 정도가 될 것 같아요.", {"amount": UNSURE}),
            s("공사는 3일에서 4일 정도 생각해요.", {"days": UNSURE}),
            s("부가세 방식은 아직 확답하기 어려워요.", {"vat": UNSURE}),
        ],
        "conflict": [
            s(
                "금액은 450만 원이라고 했지만 520만 원일지도 몰라요.",
                {"amount": UNSURE},
            ),
            s(
                "부가세 포함입니다. 그런데 별도여야 할 수도 있어요.",
                {"vat": UNSURE},
                tags=("vat_negation",),
            ),
            s("공사는 2일 확정이라면서 3일일 수도 있다고 하네요.", {"days": UNSURE}),
        ],
        "no_action": [
            s("견적서 파일은 잠시 후 업로드할게요."),
            s("공사 금액이 왜 이렇게 비싸게 나오나요?"),
            s("고객님이 견적 읽으셨는지 확인 부탁합니다."),
        ],
    },
    "dates": {
        "simple": [
            s(
                "공사는 12월 8일, 9일, 10일에 합니다.",
                {"days": ["2026-12-08", "2026-12-09", "2026-12-10"]},
            ),
            s("시공 인원은 3명입니다.", {"crew": 3}),
            s(
                "12월 14일, 15일, 16일을 작업일로 선택해 주세요.",
                {"days": ["2026-12-14", "2026-12-15", "2026-12-16"]},
            ),
        ],
        "multi_field": [
            s(
                "12월 8일부터 10일까지 작업하고 두 명이 갑니다.",
                {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 2},
            ),
            s(
                "12월 11일, 12일, 14일 시공하고 인원 4명입니다.",
                {"days": ["2026-12-11", "2026-12-12", "2026-12-14"], "crew": 4},
            ),
            s(
                "12/17, 12/18, 12/21 작업 예정이고 3명이 갑니다.",
                {"days": ["2026-12-17", "2026-12-18", "2026-12-21"], "crew": 3},
            ),
        ],
        "partial": [
            s(
                "작업 인원만 5명으로 수정해주세요.",
                {"crew": 5},
                current={"days": ["2026-12-08", "2026-12-09", "2026-12-10"]},
            ),
            s(
                "인원은 그대로 두고 공사 날짜만 12/14, 12/15, 12/16으로 정했어요.",
                {"days": ["2026-12-14", "2026-12-15", "2026-12-16"]},
                current={"crew": 2},
            ),
            s(
                "작업자 수는 1명으로만 바꿔 주세요.",
                {"crew": 1},
                current={"days": ["2026-12-18", "2026-12-19", "2026-12-20"]},
            ),
        ],
        "negation": [
            s(
                "12/9에는 작업 안 하고 12/8, 12/10, 12/11에 합니다.",
                {"days": ["2026-12-08", "2026-12-10", "2026-12-11"]},
            ),
            s("두 명 말고 세 명이 작업합니다.", {"crew": 3}),
            s(
                "12/14는 빼고 12/15, 12/16, 12/17에 시공합니다.",
                {"days": ["2026-12-15", "2026-12-16", "2026-12-17"]},
            ),
        ],
        "double_negation": [
            s(
                "12월 8일에 못 하는 건 아니에요. 8, 9, 10일 작업해요.",
                {"days": ["2026-12-08", "2026-12-09", "2026-12-10"]},
                tags=("double_negation",),
            ),
            s(
                "인원 2명으로 못 맞추는 건 아니에요. 두 명으로 하죠.",
                {"crew": 2},
                tags=("double_negation",),
            ),
            s(
                "12월 14일을 제외할 필요는 없어요. 14, 15, 16일 작업합니다.",
                {"days": ["2026-12-14", "2026-12-15", "2026-12-16"]},
                tags=("double_negation",),
            ),
        ],
        "correction": [
            s(
                "작업 12/8, 9, 10일이라고 했는데 12/9, 10, 11일로 바꿉니다.",
                {"days": ["2026-12-09", "2026-12-10", "2026-12-11"]},
            ),
            s("인원은 2명 아니고 3명입니다.", {"crew": 3}),
            s(
                "12/12, 13, 14일 아니라 12/14, 15, 16일에 공사해요.",
                {"days": ["2026-12-14", "2026-12-15", "2026-12-16"]},
            ),
        ],
        "previous_correction": [
            s(
                "앞에서 말한 9일 대신 11일을 넣을게요. 인원은 같아요.",
                {"days": ["2026-12-08", "2026-12-10", "2026-12-11"]},
                previous=p(
                    "12월 8일, 9일, 10일 작업, 인원 2명입니다.",
                    days=(
                        ["2026-12-08", "2026-12-09", "2026-12-10"],
                        "12월 8일, 9일, 10일",
                    ),
                    crew=(2, "인원 2명"),
                ),
            ),
            s(
                "세 명이라 했는데 네 명으로 정정해요. 날짜는 그대로요.",
                {"crew": 4},
                previous=p(
                    "12/11,12,13일 공사, 작업자 3명이요.",
                    days=(["2026-12-11", "2026-12-12", "2026-12-13"], "12/11,12,13일"),
                    crew=(3, "작업자 3명"),
                ),
            ),
            s(
                "아까 작업 날짜는 20,21,22일이 아니라 21,22,23일이에요.",
                {"days": ["2026-12-21", "2026-12-22", "2026-12-23"]},
                previous=p(
                    "12월 20,21,22일 작업에 인원 2명입니다.",
                    days=(
                        ["2026-12-20", "2026-12-21", "2026-12-22"],
                        "12월 20,21,22일",
                    ),
                    crew=(2, "인원 2명"),
                ),
            ),
        ],
        "previous_keep": [
            s(
                "날짜는 그대로 두고 작업 인원을 네 명으로요.",
                {"crew": 4},
                previous=p(
                    "12월 8,9,10일 시공합니다.",
                    days=(["2026-12-08", "2026-12-09", "2026-12-10"], "12월 8,9,10일"),
                ),
            ),
            s(
                "인원은 전에 말한 3명 유지하고 12/14,15,16일에 작업합니다.",
                {"days": ["2026-12-14", "2026-12-15", "2026-12-16"]},
                previous=p("인원은 3명입니다.", crew=(3, "3명")),
            ),
            s(
                "기존 공사 날짜는 그대로예요. 작업자만 두 명으로 변경할게요.",
                {"crew": 2},
                previous=p(
                    "12월 18,19,20일에 인원 4명입니다.",
                    days=(
                        ["2026-12-18", "2026-12-19", "2026-12-20"],
                        "12월 18,19,20일",
                    ),
                    crew=(4, "인원 4명"),
                ),
            ),
        ],
        "clear": [
            s(
                "작업 날짜를 모두 삭제해주세요.",
                {"days": CLEAR},
                current={"days": ["2026-12-08", "2026-12-09", "2026-12-10"]},
            ),
            s(
                "인원 선택은 지워주세요. 아직 몇 명인지 모릅니다.",
                {"crew": CLEAR},
                current={"crew": 3},
            ),
            s(
                "기존에 고른 공사 날짜 목록을 비워 주세요.",
                {"days": CLEAR},
                current={"days": ["2026-12-11", "2026-12-12", "2026-12-13"]},
            ),
        ],
        "ambiguous": [
            s("공사는 12월 둘째 주 아무 날이나 괜찮을 것 같아요.", {"days": UNSURE}),
            s("작업 인원은 두세 명 정도로 생각해요.", {"crew": UNSURE}),
            s("날짜는 연말쯤으로 잡아 주세요.", {"days": UNSURE}),
        ],
        "conflict": [
            s("시공일이 12/8,9,10일인데 12/9,10,11일일 수도 있어요.", {"days": UNSURE}),
            s("인원 2명 확정이라고 했지만 4명일지도 몰라요.", {"crew": UNSURE}),
            s(
                "12월 14,15,16일로 할지 17,18,19일로 할지 아직 못 정했어요.",
                {"days": UNSURE},
            ),
        ],
        "no_action": [
            s("작업 날짜는 고객이 결정해 주실 수 있나요?"),
            s("오늘은 공사 일정 얘기 대신 자재를 상의하고 싶습니다."),
            s("공사 인원은 확인 후 다시 연락드릴게요."),
        ],
    },
    "extra": {
        "simple": [
            s("추가 금액은 12만 원입니다.", {"amount": 120_000}),
            s("추가 항목은 곰팡이 방지 처리예요.", {"title": "곰팡이 방지 처리"}),
            s("부가세는 포함해서 올릴게요.", {"vat": "included"}),
        ],
        "multi_field": [
            s(
                "곰팡이 방지 처리, 벽면 곰팡이가 발견돼서 추가 12만 원, 부가세 포함입니다.",
                {
                    "title": "곰팡이 방지 처리",
                    "reason": "벽면 곰팡이가 발견돼서",
                    "amount": 120_000,
                    "vat": "included",
                },
            ),
            s(
                "추가 항목 폐기물 운반, 현장 폐기물 증가로 8만 원, 부가세 별도예요.",
                {
                    "title": "폐기물 운반",
                    "reason": "현장 폐기물 증가",
                    "amount": 80_000,
                    "vat": "excluded",
                },
            ),
            s(
                "천장 보강 작업 추가, 누수 흔적이 있어서 35만 원 더 받아요. 면세입니다.",
                {
                    "title": "천장 보강 작업",
                    "reason": "누수 흔적",
                    "amount": 350_000,
                    "vat": "exempt",
                },
            ),
        ],
        "partial": [
            s(
                "추가 항목 이름만 실리콘 재시공으로 바꿔요.",
                {"title": "실리콘 재시공"},
                current={"amount": 80_000},
            ),
            s(
                "사유 칸에는 기존 벽체 손상이라고 적어주세요.",
                {"reason": "기존 벽체 손상"},
            ),
            s(
                "추가금 금액만 15만 원으로 설정해 주세요.",
                {"amount": 150_000},
                current={"title": "단열 보강"},
            ),
        ],
        "negation": [
            s(
                "철거비 20만 원은 추가하지 않고 곰팡이 처리비 12만 원만 올려요.",
                {"amount": 120_000, "title": "곰팡이 처리비"},
            ),
            s(
                "부가세 별도 아닙니다. 지금은 방식을 다시 확인해야 해요.",
                {"vat": UNSURE},
                tags=("vat_negation",),
            ),
            s(
                "추가 항목을 전기 공사로 하지 마세요. 도배 보수로 적어주세요.",
                {"title": "도배 보수"},
            ),
        ],
        "double_negation": [
            s(
                "부가세 포함 안 하는 건 아니고, 포함 맞습니다.",
                {"vat": "included"},
                tags=("vat_negation", "double_negation"),
            ),
            s(
                "곰팡이 보수 작업을 추가하지 않을 이유가 없어요. 항목은 곰팡이 보수로요.",
                {"title": "곰팡이 보수"},
                tags=("double_negation",),
            ),
            s(
                "8만 원을 못 받는 건 아니에요. 추가 금액은 8만 원으로 올립니다.",
                {"amount": 80_000},
                tags=("double_negation",),
            ),
        ],
        "correction": [
            s(
                "추가 금액 12만 원 아니고 18만 원으로 고칩니다.",
                {"amount": 180_000},
                tags=("amount_correction",),
            ),
            s(
                "항목은 방수 공사가 아니라 벽면 보수로 수정해주세요.",
                {"title": "벽면 보수"},
            ),
            s(
                "사유를 자재 부족이라고 적었는데 현장 추가 손상으로 바꿉니다.",
                {"reason": "현장 추가 손상"},
            ),
        ],
        "previous_correction": [
            s(
                "12만 원이라던 추가금을 15만 원으로 수정할게요. 나머지는 그대로.",
                {"amount": 150_000},
                previous=p(
                    "곰팡이 방지 처리 추가 12만 원, 부가세 포함입니다.",
                    title=("곰팡이 방지 처리", "곰팡이 방지 처리"),
                    amount=(120_000, "12만 원"),
                    vat=("included", "부가세 포함"),
                ),
                tags=("amount_correction",),
            ),
            s(
                "항목 이름은 실리콘 보수 말고 누수 보수로 고쳐주세요.",
                {"title": "누수 보수"},
                previous=p(
                    "실리콘 보수 추가 8만 원입니다.",
                    title=("실리콘 보수", "실리콘 보수"),
                    amount=(80_000, "8만 원"),
                ),
            ),
            s(
                "기존 부가세 별도는 취소하고 포함으로 해주세요. 가격은 그대로.",
                {"vat": "included"},
                previous=p(
                    "천장 보강 비용 20만 원, 부가세 별도입니다.",
                    title=("천장 보강", "천장 보강"),
                    amount=(200_000, "20만 원"),
                    vat=("excluded", "부가세 별도"),
                ),
                tags=("vat_negation",),
            ),
        ],
        "previous_keep": [
            s(
                "항목이랑 금액은 그대로이고 사유만 벽면 습기로 수정해 주세요.",
                {"reason": "벽면 습기"},
                previous=p(
                    "곰팡이 방지 처리 12만 원 추가입니다.",
                    title=("곰팡이 방지 처리", "곰팡이 방지 처리"),
                    amount=(120_000, "12만 원"),
                ),
            ),
            s(
                "전 단계 금액 유지하고 부가세만 별도로 바꿔주세요.",
                {"vat": "excluded"},
                previous=p(
                    "추가금 18만 원, 곰팡이 처리입니다.",
                    amount=(180_000, "18만 원"),
                    title=("곰팡이 처리", "곰팡이 처리"),
                ),
            ),
            s(
                "사유는 아까 거 그대로 두고 추가 금액을 10만 원으로 올립니다.",
                {"amount": 100_000},
                previous=p(
                    "벽지 파손 때문에 추가 작업이 필요합니다.",
                    reason=("벽지 파손", "벽지 파손"),
                ),
            ),
        ],
        "clear": [
            s(
                "추가 항목 이름은 지워주세요.",
                {"title": CLEAR},
                current={"title": "곰팡이 처리"},
            ),
            s(
                "추가 사유 칸을 비워 주세요.",
                {"reason": CLEAR},
                current={"reason": "벽면 손상"},
            ),
            s(
                "추가 금액 8만 원 입력한 거 삭제해 주세요.",
                {"amount": CLEAR},
                current={"amount": 80_000},
            ),
        ],
        "ambiguous": [
            s("추가 금액이 10만 원 정도 될 것 같아요.", {"amount": UNSURE}),
            s("항목은 도배 관련해서 적당한 이름으로 넣어 주세요.", {"title": UNSURE}),
            s("사유는 현장 상황 보고 나중에 정할게요.", {"reason": UNSURE}),
        ],
        "conflict": [
            s("추가금 12만 원이라고 했지만 15만 원일 수도 있어요.", {"amount": UNSURE}),
            s(
                "부가세는 포함인데 별도로 해야 할지도 모르겠어요.",
                {"vat": UNSURE},
                tags=("vat_negation",),
            ),
            s("항목은 천장 보강일지 벽면 보수일지 아직 모릅니다.", {"title": UNSURE}),
        ],
        "no_action": [
            s("추가 금액은 고객 승인 후 청구하는 거죠?"),
            s("현장 사진을 올리고 다시 안내하겠습니다."),
            s("이전에 작성한 사유를 한번 검토해주세요."),
        ],
    },
}


def intent(field, spec):
    if isinstance(spec, tuple):
        action, value = spec
    else:
        action, value = "set", spec
    assert action in {"set", "clear", "keep", "uncertain"}
    assert (value is not None) == (action == "set")
    return {"action": action, "value": value}


def build() -> dict[str, list[dict]]:
    data = {"dev": [], "heldout": []}
    for form, kinds in CASES.items():
        index = 0
        for kind, examples in kinds.items():
            assert len(examples) == 3, (form, kind)
            for i, example in enumerate(examples):
                index += 1
                split = "heldout" if i == 2 else "dev"
                previous = example["previous"]
                fields = FIELDS[form]
                gold = {field: intent(field, ("keep", None)) for field in fields}
                prior = None
                if previous is not None:
                    prior_fields = []
                    for field in fields:
                        raw = previous["fields"].get(field)
                        if raw is None:
                            prior_fields.append(
                                {
                                    "field": field,
                                    "action": "keep",
                                    "value": None,
                                    "evidence": "",
                                }
                            )
                        else:
                            value, evidence = raw
                            prior_fields.append(
                                {
                                    "field": field,
                                    "action": "set",
                                    "value": value,
                                    "evidence": evidence,
                                }
                            )
                            gold[field] = {"action": "set", "value": value}
                    prior = {
                        "request_id": f"prev-{form}-{index:03d}",
                        "text": previous["text"],
                        "fields": prior_fields,
                    }
                for field, spec in example["changes"].items():
                    assert field in fields, (form, field)
                    gold[field] = intent(field, spec)
                row = {
                    "schema_version": 1,
                    "id": f"VA-{form[0].upper()}-{index:03d}",
                    "split": split,
                    "form": form,
                    "kind": kind,
                    "tags": sorted({kind, *example["tags"]}),
                    "text": example["text"],
                    "today": "2026-11-23",
                    "base_month": "2026-12-01",
                    "current": {**EMPTY[form], **example["current"]},
                    "previous_candidate": prior,
                    "context": {
                        "vat_modes": ["included", "excluded", "exempt"],
                        "days_max": 30,
                        "crew_max": 6,
                        "required_days": 3 if form == "dates" else None,
                        "holidays": [],
                        "no_weekend": False,
                        **example["context"],
                    },
                    "expected": gold,
                    "gold_origin": "ai_draft",
                    "human_verified": False,
                    "reviewed_by": None,
                }
                data[split].append(row)
    assert (len(data["dev"]), len(data["heldout"])) == (96, 48)
    return data


def main() -> None:
    data = build()
    for split, rows in data.items():
        (HERE / f"{split}.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
    counts = Counter(row["form"] for rows in data.values() for row in rows)
    print(
        f"AI draft: dev={len(data['dev'])}, heldout={len(data['heldout'])}, forms={dict(counts)}"
    )


if __name__ == "__main__":
    main()
