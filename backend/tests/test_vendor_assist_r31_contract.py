"""R3 보완: 동결 live50의 정확한 요청/모델 응답과 독립 상태 전이 검증.

실제 서비스 해석·검증·병합만 호출하며 모델은 고정 응답으로 대체한다.
원본 결과 SHA256: 1472be4ca63efff21e122043fb09ff1a4ea0a221015bd9750a940c1c8b712424
동결 입력 SHA256: 793ccfd769b1d75e894687655efa85007c5400d5bae1b571935319107ba695bc
금액 상한 표현의 해석은 별도 안전성 정책 대조이며 동결 골드를 바꾸지 않는다.
"""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import FALLBACK_WARNING, interpret

# 필드 순서: 이름, 동작, 값, 근거, 의도, 값 출처, 정정 관계(끝의 null은 생략).
_ARCHIVED = [
    {
        "label": "quote-02-T1",
        "request": {
            "request_id": "4d1dbc55-9ce6-4c54-aa1f-a1e738713479",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "공사를 열흘 잡으면 괜찮을까요? 부가세는 별도로 정해주세요",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": None,
            "rule_fields": [
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
                ["days", "keep", None, ""],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["amount", "keep", None, "", "no_mention"],
            [
                "vat",
                "set",
                "excluded",
                "부가세는 별도로 정해주세요",
                "explicit_change",
                "utterance",
            ],
            ["days", "uncertain", None, "공사를 열흘 잡으면 괜찮을까요?", "ambiguous"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"vat": "excluded"},
        "hold": ["days"],
        "released": [],
        "retained": [],
    },
    {
        "label": "quote-07-T1",
        "request": {
            "request_id": "3985bc2c-debf-4feb-9d94-8e7c5ea9d9c0",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "견적 총액은 912만원 안으로 먼저 보여주세요",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": None,
            "rule_fields": [
                ["amount", "set", 9120000, "견적 총액은 912만원 안으로 먼저 보여주세요"],
                ["vat", "keep", None, ""],
                ["days", "keep", None, ""],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["amount", "uncertain", None, "견적 총액은 912만원 안으로", "ambiguous"],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "keep", None, "", "no_mention"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 9120000},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "quote-08-T4",
        "request": {
            "request_id": "0e945289-5c3d-40ab-86f4-52438bbb4250",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "견적서 만료일은 12월 25일로 지정해주세요",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "8b9a5353-60e9-449a-b260-32b27edbdf9c",
                "text": "견적 687만원하고 공사기간 8일로 먼저 제안해요\n총액만 704만원으로 올릴게요. 공사 일수는 앞서 잡은 값 그대로\n부가세는 면세로 정하고 나머지 후보는 그대로 유지 부탁드립니다",
                "fields": [
                    ["amount", "set", 7040000, "총액만 704만원으로 올릴게요"],
                    ["vat", "set", "exempt", "부가세는 면세로 정하고"],
                    ["days", "set", 8, "공사기간 8일"],
                ],
                "sources": {"amount": "llm", "vat": "llm", "days": "llm"},
                "origins": {
                    "amount": "261f78d0-6db7-475f-8c64-b7e685916428",
                    "vat": "8b9a5353-60e9-449a-b260-32b27edbdf9c",
                    "days": "77ffdb0c-b5b2-4371-9b8f-90596fbe377b",
                },
                "baselines": {"amount": 4550000, "vat": "included", "days": 3},
            },
            "rule_fields": [
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
                ["days", "keep", None, ""],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "keep", None, "", "no_mention"],
            [
                "valid",
                "set",
                "2026-12-25",
                "견적서 만료일은 12월 25일로 지정해주세요",
                "explicit_change",
                "utterance",
            ],
        ],
        "desired": {"amount": 7040000, "days": 8, "vat": "exempt", "valid": "2026-12-25"},
        "hold": [],
        "released": [],
        "retained": ["amount", "vat", "days"],
    },
    {
        "label": "quote-09-T3",
        "request": {
            "request_id": "d2dee207-a9e0-4d2c-8983-8fbdde79fa86",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "공사비는 382만원으로 결정하겠습니다",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "81721ed3-7cac-40bc-9b70-ede028959111",
                "text": "총 공사비 358만원, 작업 기간 5일로 초안 잡아주세요\n금액은 370만과 390만 사이에서 아직 결정 못했어요. 일수는 직전 제안 유지",
                "fields": [["days", "set", 5, "작업 기간 5일"]],
                "sources": {"days": "llm"},
                "origins": {"days": "d91a8ea8-40ed-49cf-805a-c69d1af885cd"},
                "baselines": {"days": 3},
            },
            "rule_fields": [
                ["amount", "set", 3820000, "공사비는 382만원으로 결정하겠습니다"],
                ["vat", "keep", None, ""],
                ["days", "keep", None, ""],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            [
                "amount",
                "set",
                3820000,
                "공사비는 382만원으로 결정하겠습니다",
                "explicit_change",
                "utterance",
            ],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "keep", None, "", "no_mention"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 3820000, "days": 5},
        "hold": [],
        "released": [],
        "retained": ["days"],
    },
    {
        "label": "quote-12-T4",
        "request": {
            "request_id": "f531a03c-b212-4bd2-9645-1578e5865838",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "일수만 6일로 수정하고 금액은 앞선 제안 유지",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "deefb1e8-3812-4418-a8d5-fcc30f8ca2c6",
                "text": "견적은 635만 4천원, 작업 일수 4일로 초안 부탁\n세금 방식은 별도 처리로 추가\n유효기간은 12월 21일까지로 제안",
                "fields": [
                    ["amount", "set", 6354000, "635만 4천원"],
                    ["vat", "set", "excluded", "세금 방식은 별도 처리로 추가"],
                    ["days", "set", 4, "작업 일수 4일"],
                    ["valid", "set", "2026-12-21", "유효기간은 12월 21일까지"],
                ],
                "sources": {"amount": "llm", "vat": "llm", "days": "llm", "valid": "llm"},
                "origins": {
                    "amount": "0cfcc19f-0391-4a04-890d-6c9056c61c1d",
                    "vat": "b7d25465-01fb-4c24-aa26-461b6bf2d5e0",
                    "days": "0cfcc19f-0391-4a04-890d-6c9056c61c1d",
                    "valid": "deefb1e8-3812-4418-a8d5-fcc30f8ca2c6",
                },
                "baselines": {
                    "amount": 4550000,
                    "vat": "included",
                    "days": 3,
                    "valid": "2026-12-17",
                },
            },
            "rule_fields": [
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
                ["days", "keep", None, ""],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["amount", "keep", None, "금액은 앞선 제안 유지", "retain_previous", "previous"],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "set", 6, "일수만 6일로 수정", "explicit_change", "utterance"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 6354000, "days": 6, "vat": "excluded", "valid": "2026-12-21"},
        "hold": [],
        "released": [],
        "retained": ["amount", "vat", "valid"],
    },
    {
        "label": "quote-13-T3",
        "request": {
            "request_id": "f45bd544-a912-44cd-a321-04cf97fd3cf9",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "견적 효력은 12월 24일까지로 잡아주세요",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "a5bfc0f2-8ce2-4210-8c67-c78bac38e051",
                "text": "우선 총액 1062만원, 공사일수 12일 후보로요\n부가세 제외 방식으로 넣어주세요",
                "fields": [
                    ["amount", "set", 10620000, "총액 1062만원"],
                    ["vat", "set", "excluded", "부가세 제외 방식으로 넣어주세요"],
                    ["days", "set", 12, "공사일수 12일"],
                ],
                "sources": {"amount": "llm", "vat": "llm", "days": "llm"},
                "origins": {
                    "amount": "61ce8bd4-2670-4eef-a518-24397f2ba634",
                    "vat": "a5bfc0f2-8ce2-4210-8c67-c78bac38e051",
                    "days": "61ce8bd4-2670-4eef-a518-24397f2ba634",
                },
                "baselines": {"amount": 4550000, "vat": "included", "days": 3},
            },
            "rule_fields": [
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
                ["days", "keep", None, ""],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "keep", None, "", "no_mention"],
            [
                "valid",
                "set",
                "2026-12-24",
                "견적 효력은 12월 24일까지",
                "explicit_change",
                "utterance",
            ],
        ],
        "desired": {"amount": 10620000, "days": 12, "vat": "excluded", "valid": "2026-12-24"},
        "hold": [],
        "released": [],
        "retained": ["amount", "vat", "days"],
    },
    {
        "label": "period-07-T1",
        "request": {
            "request_id": "469c98da-7e68-464e-8e2b-386caa86cac7",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "실측 가능 기간의 시작은 12월 7일로 먼저 제안해 주세요",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": None,
            "rule_fields": [
                [
                    "start",
                    "set",
                    "2026-12-07",
                    "실측 가능 기간의 시작은 12월 7일로 먼저 제안해 주세요",
                ],
                [
                    "end",
                    "set",
                    "2026-12-07",
                    "실측 가능 기간의 시작은 12월 7일로 먼저 제안해 주세요",
                ],
                ["excluded", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["start", "set", "2026-12-07", "시작은 12월 7일로", "explicit_change", "utterance"],
            ["end", "keep", None, "", "no_mention"],
            ["excluded", "keep", None, "", "no_mention"],
        ],
        "desired": {"start": "2026-12-07"},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "period-09-T1",
        "request": {
            "request_id": "a8966fc0-645d-4714-95f5-8e307397a137",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "실측 시작일을 12월 3일로 변경하는 안으로 제안",
            "current": {"start": "2026-12-10", "end": "2026-12-24", "excluded": []},
            "previous_candidate": None,
            "rule_fields": [
                ["start", "set", "2026-12-03", "실측 시작일을 12월 3일로 변경하는 안으로 제안"],
                ["end", "set", "2026-12-03", "실측 시작일을 12월 3일로 변경하는 안으로 제안"],
                ["excluded", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            [
                "start",
                "set",
                "2026-12-03",
                "실측 시작일을 12월 3일로 변경",
                "explicit_change",
                "utterance",
            ],
            ["end", "keep", None, "", "no_mention"],
            ["excluded", "keep", None, "", "no_mention"],
        ],
        "desired": {"start": "2026-12-03"},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "period-09-T2",
        "request": {
            "request_id": "01d2ffd2-4bcf-43aa-bd09-f36b1d56dc04",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "종료일 12월 30일로 먼저 잡아주세요",
            "current": {"start": "2026-12-10", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "a8966fc0-645d-4714-95f5-8e307397a137",
                "text": "실측 시작일을 12월 3일로 변경하는 안으로 제안",
                "fields": [["start", "set", "2026-12-03", "실측 시작일을 12월 3일로 변경"]],
                "sources": {"start": "llm"},
                "origins": {"start": "a8966fc0-645d-4714-95f5-8e307397a137"},
                "baselines": {"start": "2026-12-10"},
            },
            "rule_fields": [
                ["start", "set", "2026-12-30", "종료일 12월 30일로 먼저 잡아주세요"],
                ["end", "set", "2026-12-30", "종료일 12월 30일로 먼저 잡아주세요"],
                ["excluded", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["start", "keep", None, "", "no_mention"],
            [
                "end",
                "set",
                "2026-12-30",
                "종료일 12월 30일로 먼저 잡아주세요",
                "explicit_change",
                "utterance",
            ],
            ["excluded", "keep", None, "", "no_mention"],
        ],
        "desired": {"start": "2026-12-03", "end": "2026-12-30"},
        "hold": [],
        "released": [],
        "retained": ["start"],
    },
    {
        "label": "period-11-T4",
        "request": {
            "request_id": "58279202-31bb-4199-9e15-463e723a7f23",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "실측 종료일만 12월 25일로 조정합니다",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "5901e2d0-4fd7-4dad-9a1d-76dbd75aca0b",
                "text": "12월 4일부터 12월 27일까지 실측 가능하도록 먼저 제안\n12월 12일과 19일을 실측 제외일로 제안해요\n제외 날짜 변경 요청을 취소할게요. 기간은 앞서 제안한 그대로",
                "fields": [
                    ["start", "set", "2026-12-04", "12월 4일부터"],
                    ["end", "set", "2026-12-27", "12월 27일까지"],
                ],
                "sources": {"start": "llm", "end": "llm"},
                "origins": {
                    "start": "98221423-8a2e-4270-8736-294cb574fe24",
                    "end": "98221423-8a2e-4270-8736-294cb574fe24",
                },
                "baselines": {"start": "2026-12-02", "end": "2026-12-24"},
            },
            "rule_fields": [
                ["start", "set", "2026-12-25", "실측 종료일만 12월 25일로 조정합니다"],
                ["end", "set", "2026-12-25", "실측 종료일만 12월 25일로 조정합니다"],
                ["excluded", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["start", "keep", None, "", "no_mention"],
            [
                "end",
                "set",
                "2026-12-25",
                "실측 종료일만 12월 25일로 조정합니다",
                "explicit_change",
                "utterance",
            ],
            ["excluded", "keep", None, "", "no_mention"],
        ],
        "desired": {"start": "2026-12-04", "end": "2026-12-25"},
        "hold": [],
        "released": [],
        "retained": ["start"],
    },
    {
        "label": "period-12-T2",
        "request": {
            "request_id": "4a33a4ed-59e0-4bc7-89a2-0633794cb21d",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "제외 날짜 12월 8일하고 16일도 제안해줘요",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "d09d0168-f91c-4d5d-bea6-3f2a4930cc44",
                "text": "실측 기간은 12월 3일부터 23일까지 먼저 제안",
                "fields": [
                    ["start", "set", "2026-12-03", "12월 3일부터"],
                    ["end", "set", "2026-12-23", "12월 3일부터 23일까지"],
                ],
                "sources": {"start": "llm", "end": "llm"},
                "origins": {
                    "start": "d09d0168-f91c-4d5d-bea6-3f2a4930cc44",
                    "end": "d09d0168-f91c-4d5d-bea6-3f2a4930cc44",
                },
                "baselines": {"start": "2026-12-02", "end": "2026-12-24"},
            },
            "rule_fields": [
                ["start", "set", "2026-12-16", "제외 날짜 12월 8일하고 16일도 제안해줘요"],
                ["end", "set", "2026-12-16", "제외 날짜 12월 8일하고 16일도 제안해줘요"],
                ["excluded", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["start", "keep", None, "", "no_mention"],
            ["end", "keep", None, "", "no_mention"],
            [
                "excluded",
                "set",
                ["2026-12-08", "2026-12-16"],
                "제외 날짜 12월 8일하고 16일",
                "explicit_change",
                "utterance",
            ],
        ],
        "desired": {
            "start": "2026-12-03",
            "end": "2026-12-23",
            "excluded": ["2026-12-08", "2026-12-16"],
        },
        "hold": [],
        "released": [],
        "retained": ["start", "end"],
    },
    {
        "label": "period-13-T3",
        "request": {
            "request_id": "d84250ae-a715-48e8-b7ee-ed7e21b56f2e",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "아까 제안한 제외일 중 15일 대신 17일로 이동",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "e2600ad2-8935-4d14-a721-6dafb588b7f0",
                "text": "방문 가능 기간 12월 4일에서 28일까지로 제안\n실측 제외일 12월 9일, 15일, 21일로 정해 주세요",
                "fields": [
                    ["start", "set", "2026-12-04", "12월 4일에서"],
                    ["end", "set", "2026-12-28", "28일까지"],
                    [
                        "excluded",
                        "set",
                        ["2026-12-09", "2026-12-15", "2026-12-21"],
                        "실측 제외일 12월 9일, 15일, 21일로 정해 주세요",
                        "explicit_change",
                        "derived",
                    ],
                ],
                "sources": {"start": "llm", "end": "llm", "excluded": "llm"},
                "origins": {
                    "start": "7e848bea-d02f-4264-b390-ece908442ecb",
                    "end": "7e848bea-d02f-4264-b390-ece908442ecb",
                    "excluded": "e2600ad2-8935-4d14-a721-6dafb588b7f0",
                },
                "baselines": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            },
            "rule_fields": [
                ["start", "keep", None, ""],
                ["end", "keep", None, ""],
                ["excluded", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["start", "keep", None, "", "no_mention"],
            ["end", "keep", None, "", "no_mention"],
            [
                "excluded",
                "uncertain",
                None,
                "아까 제안한 제외일 중 15일 대신 17일로 이동",
                "ambiguous",
            ],
        ],
        "desired": {
            "start": "2026-12-04",
            "end": "2026-12-28",
            "excluded": ["2026-12-09", "2026-12-17", "2026-12-21"],
        },
        "hold": [],
        "released": [],
        "retained": ["start", "end"],
    },
    {
        "label": "dates-08-T4",
        "request": {
            "request_id": "1e771b62-09a3-4b59-8426-467a6c141918",
            "form": "dates",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "작업 날짜와 작업자 수 모두 앞서 제안한 대로 유지합니다",
            "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            "previous_candidate": {
                "request_id": "9160a3a5-3a9d-4ae8-b1e0-ac68493733e9",
                "text": "작업일은 12월 5일과 21일로 먼저 제안, 인원 3명\n공사 날짜 12월 5일, 17일, 21일로 변경하고 인원 그대로 유지해주세요\n작업일은 12월 7일과 22일로 결정해요. 인원은 직전 제안대로",
                "fields": [
                    [
                        "days",
                        "set",
                        ["2026-12-07", "2026-12-22"],
                        "작업일은 12월 7일과 22일로 결정해요",
                        "explicit_change",
                        "derived",
                    ],
                    ["crew", "set", 3, "인원 3명"],
                ],
                "sources": {"days": "llm", "crew": "llm"},
                "origins": {
                    "days": "9160a3a5-3a9d-4ae8-b1e0-ac68493733e9",
                    "crew": "83eec699-ff52-4cd9-9267-257b5ae1060c",
                },
                "baselines": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            },
            "rule_fields": [["days", "keep", None, ""], ["crew", "keep", None, ""]],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
            "requiredDays": 2,
        },
        "model": [
            [
                "days",
                "uncertain",
                None,
                "작업 날짜와 작업자 수 모두 앞서 제안한 대로 유지합니다",
                "ambiguous",
            ],
            [
                "crew",
                "keep",
                None,
                "작업 날짜와 작업자 수 모두 앞서 제안한 대로 유지합니다",
                "retain_previous",
                "previous",
            ],
        ],
        "desired": {"days": ["2026-12-07", "2026-12-22"], "crew": 3},
        "hold": [],
        "released": [],
        "retained": ["days", "crew"],
    },
    {
        "label": "dates-09-T2",
        "request": {
            "request_id": "23f2797f-3afa-4db1-a21c-3d57887a13aa",
            "form": "dates",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "앞서 제안한 공사일 20일만 23일로 이동",
            "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            "previous_candidate": {
                "request_id": "5d85a846-e919-4c69-bdca-41e5e9f92614",
                "text": "12월 3일, 20일 작업하고 인원은 4명으로 제안해주세요",
                "fields": [
                    ["days", "set", ["2026-12-03", "2026-12-20"], "12월 3일, 20일 작업"],
                    ["crew", "set", 4, "인원은 4명으로"],
                ],
                "sources": {"days": "llm", "crew": "llm"},
                "origins": {
                    "days": "5d85a846-e919-4c69-bdca-41e5e9f92614",
                    "crew": "5d85a846-e919-4c69-bdca-41e5e9f92614",
                },
                "baselines": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            },
            "rule_fields": [
                [
                    "days",
                    "set",
                    ["2026-12-20", "2026-12-23"],
                    "앞서 제안한 공사일 20일만 23일로 이동",
                ],
                ["crew", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
            "requiredDays": 2,
        },
        "model": [
            ["days", "uncertain", None, "앞서 제안한 공사일 20일만 23일로 이동", "ambiguous"],
            ["crew", "keep", None, "", "no_mention"],
        ],
        "desired": {"days": ["2026-12-03", "2026-12-23"], "crew": 4},
        "hold": [],
        "released": [],
        "retained": ["crew"],
    },
    {
        "label": "dates-11-T5",
        "request": {
            "request_id": "139451ec-efd3-49b8-97c4-ef091b80fa92",
            "form": "dates",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "인원은 지금 폼에 입력한 수 그대로 둘게요",
            "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            "previous_candidate": {
                "request_id": "0367c338-d2e8-4b59-9fb2-3dab80c64737",
                "text": "작업일 12월 6일, 22일로 우선 제안합니다\n투입 인원은 다섯 명으로 후보 추가\n앞서 제안한 공사 날짜 중 6일 대신 8일로 변경\n작업자는 4명으로 바꾸고 작업일은 이전 제안 유지",
                "fields": [
                    [
                        "days",
                        "set",
                        ["2026-12-08", "2026-12-22"],
                        "앞서 제안한 공사 날짜 중 6일 대신 8일로 변경",
                        None,
                        "derived",
                        {
                            "kind": "replace",
                            "rejected_value": ["2026-12-06", "2026-12-22"],
                            "rejected_evidence": "6일",
                            "accepted_evidence": "8일",
                        },
                    ],
                    ["crew", "set", 4, "작업자는 4명으로 바꾸고"],
                ],
                "sources": {"days": "rule", "crew": "llm"},
                "origins": {
                    "days": "f9ebaf80-a437-4f0d-8537-17c27c71b75b",
                    "crew": "0367c338-d2e8-4b59-9fb2-3dab80c64737",
                },
                "baselines": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            },
            "rule_fields": [["days", "keep", None, ""], ["crew", "keep", None, ""]],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
            "requiredDays": 2,
        },
        "model": [
            ["days", "keep", None, "", "no_mention"],
            [
                "crew",
                "keep",
                None,
                "인원은 지금 폼에 입력한 수 그대로 둘게요",
                "retain_current",
                "current",
            ],
        ],
        "desired": {"days": ["2026-12-08", "2026-12-22"]},
        "hold": [],
        "released": ["crew"],
        "retained": ["days"],
    },
    {
        "label": "extra-02-T1",
        "request": {
            "request_id": "1ad45a8a-f0cd-4828-986b-1ed71418b3aa",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "추가금 45만원 정도로 하면 될까요? 부가세는 면세로 정합니다",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": None,
            "rule_fields": [
                ["title", "keep", None, ""],
                ["reason", "keep", None, ""],
                ["amount", "keep", None, ""],
                [
                    "vat",
                    "set",
                    "exempt",
                    "추가금 45만원 정도로 하면 될까요? 부가세는 면세로 정합니다",
                ],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["title", "keep", None, "", "no_mention"],
            ["reason", "keep", None, "", "no_mention"],
            ["amount", "uncertain", None, "추가금 45만원 정도로 하면 될까요?", "ambiguous"],
            ["vat", "set", "exempt", "부가세는 면세로 정합니다", "explicit_change", "utterance"],
        ],
        "desired": {"vat": "exempt"},
        "hold": ["amount"],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-05-T2",
        "request": {
            "request_id": "32c440fa-8638-4cec-8c7d-1cfe0d661fc2",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "사유 변경 요청은 철회하고 추가 금액은 앞서 정한 값으로 유지",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "7006a260-92a8-4f4c-9524-395d93327d6d",
                "text": "사유는 배수구 단차, 추가금 36만원으로 후보 지정",
                "fields": [
                    ["reason", "set", "배수구 단차", "사유는 배수구 단차"],
                    ["amount", "set", 360000, "추가금 36만원"],
                ],
                "sources": {"reason": "llm", "amount": "llm"},
                "origins": {
                    "reason": "7006a260-92a8-4f4c-9524-395d93327d6d",
                    "amount": "7006a260-92a8-4f4c-9524-395d93327d6d",
                },
                "baselines": {"reason": "현장 추가", "amount": 1000000},
            },
            "rule_fields": [
                ["title", "keep", None, ""],
                ["reason", "keep", None, ""],
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["title", "keep", None, "", "no_mention"],
            ["reason", "keep", None, "사유 변경 요청은 철회하고", "retain_current", "current"],
            [
                "amount",
                "keep",
                None,
                "추가 금액은 앞서 정한 값으로 유지",
                "retain_previous",
                "previous",
            ],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 360000},
        "hold": [],
        "released": ["reason"],
        "retained": ["amount"],
    },
    {
        "label": "extra-07-T1",
        "request": {
            "request_id": "b42ee385-8ddc-4eed-bda4-b6873403dde6",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "항목명은 벽면 보수, 사유는 석고보드 균열로 초안 부탁",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": None,
            "rule_fields": [
                [
                    "title",
                    "set",
                    "벽면 보수",
                    "항목명은 벽면 보수, 사유는 석고보드 균열로 초안 부탁",
                ],
                [
                    "reason",
                    "set",
                    "석고보드 균열로 초안 부탁",
                    "항목명은 벽면 보수, 사유는 석고보드 균열로 초안 부탁",
                ],
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["title", "set", "벽면 보수", "항목명은 벽면 보수", "explicit_change", "utterance"],
            [
                "reason",
                "set",
                "석고보드 균열",
                "사유는 석고보드 균열",
                "explicit_change",
                "utterance",
            ],
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"title": "벽면 보수", "reason": "석고보드 균열"},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-08-T1",
        "request": {
            "request_id": "31512d35-a8b3-49ec-bef8-7354a3ab4359",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "추가 사유는 고정 볼트 파손, 금액 29만원으로 제안해주세요",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": None,
            "rule_fields": [
                [
                    "title",
                    "set",
                    "추가 사유는 고정 볼트 파손",
                    "추가 사유는 고정 볼트 파손, 금액 29만원으로 제안해주세요",
                ],
                ["reason", "keep", None, ""],
                [
                    "amount",
                    "set",
                    290000,
                    "추가 사유는 고정 볼트 파손, 금액 29만원으로 제안해주세요",
                ],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["title", "keep", None, "", "no_mention"],
            [
                "reason",
                "set",
                "고정 볼트 파손",
                "추가 사유는 고정 볼트 파손",
                "explicit_change",
                "utterance",
            ],
            ["amount", "set", 290000, "금액 29만원", "explicit_change", "utterance"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"reason": "고정 볼트 파손", "amount": 290000},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-09-T1",
        "request": {
            "request_id": "5fe7b15e-0e7f-4a57-810d-5d85125907f7",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "항목명은 배관 덮개 설치, 추가 비용 63만원 안으로 제안",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": None,
            "rule_fields": [
                [
                    "title",
                    "set",
                    "배관 덮개 설치",
                    "항목명은 배관 덮개 설치, 추가 비용 63만원 안으로 제안",
                ],
                ["reason", "keep", None, ""],
                ["amount", "set", 630000, "항목명은 배관 덮개 설치, 추가 비용 63만원 안으로 제안"],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            [
                "title",
                "set",
                "배관 덮개 설치",
                "항목명은 배관 덮개 설치",
                "explicit_change",
                "utterance",
            ],
            ["reason", "keep", None, "", "no_mention"],
            ["amount", "uncertain", None, "추가 비용 63만원 안으로 제안", "ambiguous"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"title": "배관 덮개 설치", "amount": 630000},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-10-T1",
        "request": {
            "request_id": "4f029132-7455-403f-90e5-1adeb5edb17b",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "추가 작업명 천장 고정, 금액 84만원으로 먼저 제안합니다",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": None,
            "rule_fields": [
                [
                    "title",
                    "set",
                    "추가 작업명 천장 고정",
                    "추가 작업명 천장 고정, 금액 84만원으로 먼저 제안합니다",
                ],
                ["reason", "keep", None, ""],
                ["amount", "set", 840000, "추가 작업명 천장 고정, 금액 84만원으로 먼저 제안합니다"],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded"],
        },
        "model": [
            ["title", "set", "천장 고정", "추가 작업명 천장 고정", "explicit_change", "utterance"],
            ["reason", "keep", None, "", "no_mention"],
            ["amount", "set", 840000, "금액 84만원", "explicit_change", "utterance"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"title": "천장 고정", "amount": 840000},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-11-T3",
        "request": {
            "request_id": "01b315eb-b5c1-4936-99d7-6456d5a1ae28",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "세금은 별도로 표시 부탁합니다",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "ee5cb379-b648-4336-a456-fad5153c521e",
                "text": "추가 항목명은 선반 고정, 사유는 벽체 앵커 이탈로 제안\n추가금 47만원을 후보에 넣어주세요",
                "fields": [
                    ["title", "set", "선반 고정", "추가 항목명은 선반 고정"],
                    ["reason", "set", "벽체 앵커 이탈", "사유는 벽체 앵커 이탈로 제안"],
                    ["amount", "set", 470000, "추가금 47만원"],
                ],
                "sources": {"title": "llm", "reason": "llm", "amount": "llm"},
                "origins": {
                    "title": "4da0b920-148d-4c14-90ef-f12fc938d882",
                    "reason": "4da0b920-148d-4c14-90ef-f12fc938d882",
                    "amount": "ee5cb379-b648-4336-a456-fad5153c521e",
                },
                "baselines": {"title": "보강", "reason": "현장 추가", "amount": 1000000},
            },
            "rule_fields": [
                ["title", "set", "세금은 별도로 표시 부탁합니다", "세금은 별도로 표시 부탁합니다"],
                ["reason", "keep", None, ""],
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["title", "keep", None, "", "no_mention"],
            ["reason", "keep", None, "", "no_mention"],
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "set", "excluded", "세금은 별도로 표시", "explicit_change", "utterance"],
        ],
        "desired": {
            "title": "선반 고정",
            "reason": "벽체 앵커 이탈",
            "amount": 470000,
            "vat": "excluded",
        },
        "hold": [],
        "released": [],
        "retained": ["title", "reason", "amount"],
    },
    {
        "label": "extra-12-T7",
        "request": {
            "request_id": "fe16d0aa-6570-45dd-b4be-cbcc497f79c0",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "세금은 지금 입력된 값대로 둘게요",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "3db6f5cd-8163-48ff-bc0d-568db1406393",
                "text": "추가 항목은 창호 틈새 보수, 금액 55만원으로 제안\n사유는 실리콘 접착 불량으로 기재해 주세요\n부가세 제외로 후보 추가 부탁\n추가 금액 59만원으로 올릴게요\n항목명은 창호 기밀 보강으로 수정해주세요\n사유는 앞서 제안한 내용 그대로 유지",
                "fields": [
                    ["title", "set", "창호 기밀 보강", "항목명은 창호 기밀 보강으로 수정해주세요"],
                    [
                        "reason",
                        "set",
                        "실리콘 접착 불량",
                        "사유는 실리콘 접착 불량으로 기재해 주세요",
                    ],
                    ["amount", "set", 590000, "추가 금액 59만원으로 올릴게요"],
                    ["vat", "set", "excluded", "부가세 제외로"],
                ],
                "sources": {"title": "llm", "reason": "llm", "amount": "llm", "vat": "llm"},
                "origins": {
                    "title": "8f56b5bb-787a-48fa-bccc-16bb3313af1c",
                    "reason": "ba07b66d-f248-41bb-9bb7-0b02ddbbda5e",
                    "amount": "52e1cbc2-e705-4560-858d-6dcc233c9076",
                    "vat": "a2fd98d7-6454-426b-8afe-664cc18546fd",
                },
                "baselines": {
                    "title": "보강",
                    "reason": "현장 추가",
                    "amount": 1000000,
                    "vat": "included",
                },
            },
            "rule_fields": [
                [
                    "title",
                    "set",
                    "세금은 지금 입력된 값대로 둘게요",
                    "세금은 지금 입력된 값대로 둘게요",
                ],
                ["reason", "keep", None, ""],
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 120,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            ["title", "keep", None, "", "no_mention"],
            ["reason", "keep", None, "", "no_mention"],
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "keep", None, "세금은 지금 입력된 값대로 둘게요", "retain_current", "current"],
        ],
        "desired": {"title": "창호 기밀 보강", "amount": 590000, "reason": "실리콘 접착 불량"},
        "hold": [],
        "released": ["vat"],
        "retained": ["title", "reason", "amount"],
    },
]


def _intents(rows):
    names = ("field", "action", "value", "evidence", "intent_kind", "value_source", "correction")
    return [FieldIntent.model_validate(dict(zip(names, row, strict=False))) for row in rows]


def _snapshot(case):
    request = dict(case["request"])
    request["rule_fields"] = _intents(request["rule_fields"])
    if request["previous_candidate"] is not None:
        prior = dict(request["previous_candidate"])
        prior["fields"] = _intents(prior["fields"])
        request["previous_candidate"] = PreviousCandidate.model_validate(prior)
    opts = case["options"]
    context = AssistContext(
        today=date.fromisoformat(opts["today"]),
        base_month=date.fromisoformat(opts["baseMonth"]),
        days_max=opts["daysMax"],
        crew_max=opts["crewMax"],
        required_days=opts.get("requiredDays"),
        vat_modes=opts["vatModes"],
        no_weekend=opts.get("noWeekend", False),
        holidays=[date.fromisoformat(day) for day in opts.get("holidays", [])],
    )
    return AssistRequest.model_validate(request), context, _intents(case["model"])


def _run(monkeypatch, request, context, model, *, mode="model"):
    assert not settings.llm_live
    calls = []
    original = request.model_dump(mode="json")

    async def stub(data, _context):
        calls.append(data.model_copy(deep=True))
        if mode == "fallback":
            raise RuntimeError("offline fixture")
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(request, context, use_model=mode != "rules", trace=trace))
    assert len(calls) == int(mode != "rules")
    assert request.model_dump(mode="json") == original
    assert response.form == request.form and response.request_id == request.request_id
    assert response.source == ("llm" if mode == "model" else "rule")
    if mode == "fallback":
        assert FALLBACK_WARNING in response.warnings
    if calls:
        assert calls[0].current == original["current"]
        verified = calls[0].previous_candidate
        assert (verified.model_dump(mode="json") if verified else None) == trace[
            "previous_verified"
        ]
    if mode == "model":
        assert trace["model_raw"] == [item.model_dump(mode="json") for item in model]
    fields = {item.field: item for item in response.fields}
    assert set(fields) == set(FORM_FIELDS[request.form])
    # 잘못된 대안도 사용자에게 선택 가능하게 내보내면 안 된다.
    assert not any(item.alternatives for item in fields.values()), trace["decisions"]
    return fields, trace


def _offers(fields):
    return {
        field: item.value
        for field, item in fields.items()
        if item.status == "valid" and item.action in ("set", "clear")
    }


def _held(field):
    assert field.status in ("invalid", "uncertain"), field
    assert field.action == "uncertain" and field.value is None and not field.alternatives


def _current_kept(field, request_id):
    assert (field.status, field.action, field.intent_kind, field.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    )
    assert field.origin_request_id == request_id


def _no_mention(field):
    assert (field.status, field.action, field.intent_kind) == ("valid", "keep", "no_mention")


def _carried(field, prior):
    old = next(item for item in prior.fields if item.field == field.field)
    assert field.status == "valid" and field.action == old.action
    assert field.value == old.value and field.evidence == old.evidence
    assert field.source == prior.sources.get(field.field, "rule")
    assert field.origin_request_id == prior.origins.get(field.field, prior.request_id)
    if old.value_source == "derived" and old.correction is not None:
        assert field.value_source == "derived" and field.correction == old.correction


@pytest.mark.parametrize("case", _ARCHIVED, ids=lambda case: case["label"])
def test_live50_archived_service_contract(monkeypatch, case):
    req, context, model = _snapshot(case)
    fields, trace = _run(monkeypatch, req, context, model)
    desired = dict(case["desired"])
    holds = set(case["hold"])
    if case["label"] in ("quote-07-T1", "extra-09-T1"):
        # '안으로'는 상한 요청일 수 있다. 특정 숫자 확정을 강제하지 않는다.
        # 원래 기대값은 위 스냅숏에 그대로 보존되어 있다.
        desired.pop("amount")
        holds.add("amount")
    assert _offers(fields) == desired, trace["decisions"]
    for name in holds:
        _held(fields[name])
    for name in case["released"]:
        _current_kept(fields[name], req.request_id)
    for name in fields.keys() - desired.keys() - holds - set(case["released"]):
        _no_mention(fields[name])
    for name in case["retained"]:
        if name in desired and req.previous_candidate:
            _carried(fields[name], req.previous_candidate)
    if case["label"] in ("dates-09-T2", "period-13-T3"):
        name = "days" if req.form == "dates" else "excluded"
        field = fields[name]
        old = next(item for item in req.previous_candidate.fields if item.field == name)
        assert field.value_source == "derived"
        assert field.correction.kind == "replace"
        assert field.correction.rejected_value == old.value
        assert field.source == "rule" and field.origin_request_id == req.request_id


# 원본 평가와 겹치지 않는 새 문구로 동일한 상태 전이를 대조한다.
_CONTEXT = AssistContext(
    today=date(2026, 11, 24), base_month=date(2026, 12, 1), crew_max=6, days_max=120
)
_CURRENT = {
    "quote": {"amount": 4_550_000, "days": 3, "valid": "2026-12-17", "vat": "included"},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1_000_000, "vat": "included"},
}
_PAIR = {
    "quote": {
        "keep": ("amount", 6_180_000, "견적 금액 618만원", "금액"),
        "other": ("valid", "2026-12-22", "유효기간은 12월 22일", "견적서 만료일"),
        "next": ("2026-12-27", "견적서 만료일을 12월 27일로 변경해 주세요"),
        "replacement": (6_260_000, "공사비는 626만원으로 확정하겠습니다"),
        "manual": 5_000_000,
    },
    "period": {
        "keep": ("excluded", ["2026-12-09", "2026-12-18"], "제외일은 12월 9일과 18일", "제외일"),
        "other": ("end", "2026-12-27", "종료일은 12월 27일", "실측 종료일"),
        "next": ("2026-12-29", "실측 종료일을 12월 29일로 확정해 주세요"),
        "replacement": (["2026-12-11", "2026-12-19"], "제외일은 12월 11일과 19일로 확정합니다"),
        "manual": ["2026-12-12"],
    },
    "dates": {
        "keep": ("days", ["2026-12-08", "2026-12-17"], "작업일은 12월 8일과 17일", "작업일"),
        "other": ("crew", 3, "작업 인원은 3명", "작업자 수"),
        "next": (4, "작업자 수를 4명으로 확정합니다"),
        "replacement": (["2026-12-10", "2026-12-19"], "작업일은 12월 10일과 19일로 결정합니다"),
        "manual": ["2026-12-11", "2026-12-21"],
    },
    "extra": {
        "keep": ("title", "방문 손잡이 교체", "추가 항목명은 방문 손잡이 교체", "항목명"),
        "other": ("reason", "고정 나사 손상", "사유는 고정 나사 손상", "추가 사유"),
        "next": ("문틀 나사 손상", "추가 사유는 문틀 나사 손상으로 확정해 주세요"),
        "replacement": ("방문 경첩 교체", "작업명은 방문 경첩 교체로 확정합니다"),
        "manual": "현관 손잡이 보수",
    },
}


def _set(field, value, evidence, *, raw_rule=False, derived=False):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind=None if raw_rule else "explicit_change",
        value_source=None if raw_rule else "derived" if derived else "utterance",
    )


def _keep(field, evidence="", kind="no_mention"):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source={"retain_current": "current", "retain_previous": "previous"}.get(kind),
    )


def _uncertain(field, evidence):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=evidence, intent_kind="ambiguous"
    )


def _context(form):
    return _CONTEXT.model_copy(update={"required_days": 2}) if form == "dates" else _CONTEXT


def _request(form, text, *, prior=None, rules=(), current=None, request_id="r31-new"):
    return AssistRequest(
        request_id=request_id,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=_CURRENT[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )


def _next_prior(request, fields):
    # 다음 후보는 저장된 과거 응답을 끼워 넣지 않고 이번 실제 서비스 결과만 사용한다.
    old = request.previous_candidate
    items = [f for f in fields.values() if f.status == "valid" and f.action in ("set", "clear")]
    baselines = {}
    for f in items:
        inherited = old and f.origin_request_id != request.request_id and f.field in old.baselines
        baselines[f.field] = old.baselines[f.field] if inherited else request.current[f.field]
    return PreviousCandidate(
        request_id=request.request_id,
        text=(old.text + "\n" if old else "") + request.text,
        fields=[
            FieldIntent(
                field=f.field,
                action=f.action,
                value=f.value,
                evidence=f.evidence,
                intent_kind="explicit_change" if f.value_source == "derived" else None,
                value_source="derived" if f.value_source == "derived" else None,
                correction=f.correction if f.value_source == "derived" else None,
            )
            for f in items
        ],
        sources={f.field: f.source for f in items},
        origins={f.field: f.origin_request_id for f in items},
        baselines=baselines,
    )


def _seed(monkeypatch, form, *, derived=False):
    a, av, ae, _ = _PAIR[form]["keep"]
    b, bv, be, _ = _PAIR[form]["other"]
    text = ae + " 먼저 제안합니다. " + be + " 먼저 제안합니다"
    req = _request(form, text, request_id="r31-seed")
    fields, trace = _run(
        monkeypatch,
        req,
        _context(form),
        [_set(a, av, ae, derived=derived), _set(b, bv, be)],
    )
    assert _offers(fields) == {a: av, b: bv}, trace["decisions"]
    return _next_prior(req, fields)


@pytest.mark.parametrize("form", list(_PAIR))
@pytest.mark.parametrize("control", ["set", "inquiry", "current", "cancel"])
def test_unmentioned_verified_prior_survives_independent_field_transition(
    monkeypatch, form, control
):
    prior = _seed(monkeypatch, form)
    a, av, _, _ = _PAIR[form]["keep"]
    b, _, _, label = _PAIR[form]["other"]
    topic = label + ("는" if form in ("dates", "extra") else "은")
    new_value, change = _PAIR[form]["next"]
    if control == "set":
        text = change
        own = _set(b, new_value, text)
    elif control == "inquiry":
        text = topic + " 아직 미정이고 다음에 확인할게요"
        own = _uncertain(b, text)
    elif control == "current":
        text = topic + " 현재 입력된 값 그대로 둘게요"
        own = _keep(b, text, "retain_current")
    else:
        text = label + " 변경 요청은 철회하겠습니다"
        own = _keep(b, text, "retain_current")
    # 규칙 파서가 다른 필드 문장을 잘못 연결해도 적격 이전 후보는 유지한다.
    bad_value = text if a == "title" else av
    wrong_rule = _set(a, bad_value, text, raw_rule=True)
    req = _request(form, text, prior=prior, rules=[wrong_rule])
    fields, trace = _run(monkeypatch, req, _context(form), [_keep(a), own])
    assert not trace["previous_rejected"]
    _carried(fields[a], prior)
    expected = {a: av}
    if control == "set":
        expected[b] = new_value
        assert fields[b].origin_request_id == req.request_id
    elif control == "inquiry":
        _held(fields[b])
    else:
        _current_kept(fields[b], req.request_id)
    assert _offers(fields) == expected, trace["decisions"]


@pytest.mark.parametrize("form", list(_PAIR))
@pytest.mark.parametrize(
    "control", ["same-ambiguity", "same-change", "same-stale", "global-unbound"]
)
def test_competing_or_unbound_intent_cannot_revive_prior(monkeypatch, form, control):
    prior = _seed(monkeypatch, form)
    a, av, _, label = _PAIR[form]["keep"]
    b, bv, _, _ = _PAIR[form]["other"]
    current = dict(_CURRENT[form])
    if control == "same-ambiguity":
        text = label + "은 아직 미정입니다"
        model = [_uncertain(a, text), _keep(b)]
    elif control == "same-change":
        value, text = _PAIR[form]["replacement"]
        model = [_set(a, value, text), _keep(b)]
    elif control == "same-stale":
        current[a] = _PAIR[form]["manual"]
        text = label + "은 앞서 제안한 그대로 유지해 주세요"
        model = [_keep(a, text, "retain_previous"), _keep(b)]
    else:
        text = "가능하면 앞서 제안한 값 그대로 유지할까요?"
        model = [_keep(a), _keep(b)]
    req = _request(form, text, prior=prior, current=current)
    fields, trace = _run(monkeypatch, req, _context(form), model)
    if control == "global-unbound":
        _held(fields[a])
        _held(fields[b])
        assert not _offers(fields), trace["decisions"]
    else:
        _carried(fields[b], prior)
        if control == "same-change":
            assert _offers(fields) == {a: value, b: bv}, trace["decisions"]
            assert fields[a].origin_request_id == req.request_id
            assert fields[a].value != av
        else:
            _held(fields[a])
            assert _offers(fields) == {b: bv}
            if control == "same-stale":
                assert trace["previous_rejected"][a] == "stale_context"


@pytest.mark.parametrize("form", list(_PAIR))
@pytest.mark.parametrize("mode", ["rules", "fallback"])
def test_server_fallback_does_not_discard_verified_independent_candidate(monkeypatch, form, mode):
    prior = _seed(monkeypatch, form)
    a, av, _, _ = _PAIR[form]["keep"]
    b, _, _, _ = _PAIR[form]["other"]
    value, text = _PAIR[form]["next"]
    req = _request(form, text, prior=prior, rules=[_set(b, value, text, raw_rule=True)])
    fields, trace = _run(monkeypatch, req, _context(form), [], mode=mode)
    _carried(fields[a], prior)
    assert _offers(fields) == {a: av, b: value}, trace["decisions"]
    assert fields[b].source == "rule"
    assert fields[b].origin_request_id == req.request_id


@pytest.mark.parametrize(
    "field,value,text,contaminated",
    [
        (
            "reason",
            "배관 연결부 누수",
            "사유는 배관 연결부 누수로 초안 부탁드립니다",
            "배관 연결부 누수로 초안 부탁드립니다",
        ),
        (
            "title",
            "방충망 손잡이 교체",
            "추가 작업명 방충망 손잡이 교체",
            "추가 작업명 방충망 손잡이 교체",
        ),
    ],
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_unsafe_assignment_string_never_selectable(
    monkeypatch, field, value, text, contaminated, mode
):
    amount_text = "추가 금액 41만원으로 확정합니다"
    utterance = text + ", " + amount_text
    req = _request(
        "extra",
        utterance,
        rules=[
            _set(field, contaminated, utterance, raw_rule=True),
            _set("amount", 410_000, amount_text, raw_rule=True),
        ],
    )
    fields, trace = _run(
        monkeypatch,
        req,
        _context("extra"),
        [_set(field, value, text), _set("amount", 410_000, amount_text)],
        mode=mode,
    )
    assert fields["amount"].status == "valid" and fields["amount"].value == 410_000
    assert fields[field].value != contaminated
    if mode == "model":
        assert _offers(fields) == {"amount": 410_000, field: value}, trace["decisions"]
    elif fields[field].action == "set":
        assert fields[field].status == "valid" and fields[field].value == value
    else:
        _held(fields[field])


@pytest.mark.parametrize(
    "field,text,value",
    [
        ("title", "항목명은 초안 도면 보완으로 확정합니다", "초안 도면 보완"),
        ("reason", "사유는 작업명 표기 누락으로 확정합니다", "작업명 표기 누락"),
    ],
)
def test_actual_noun_inside_value_is_not_removed_as_directive(monkeypatch, field, text, value):
    intent = _set(field, value, text)
    req = _request("extra", text, rules=[intent])
    fields, trace = _run(monkeypatch, req, _context("extra"), [intent])
    assert _offers(fields) == {field: value}, trace["decisions"]


@pytest.mark.parametrize("label", ["dates-08-T4", "dates-11-T5", "period-13-T3"])
@pytest.mark.parametrize("control", ["same-cancel", "same-ambiguous", "stale"])
def test_derived_history_cannot_bypass_negative_state_transition(monkeypatch, label, control):
    case = next(c for c in _ARCHIVED if c["label"] == label)
    req, context, model = _snapshot(case)
    prior = req.previous_candidate
    field = "days" if req.form == "dates" else "excluded"
    name = "작업일" if req.form == "dates" else "제외일"
    current = dict(req.current)
    if control == "stale":
        current[field] = ["2026-12-11", "2026-12-19"] if req.form == "dates" else ["2026-12-11"]
    else:
        tail = name + (
            " 변경 요청은 취소하겠습니다" if control == "same-cancel" else "은 아직 미정입니다"
        )
        prior = prior.model_copy(update={"text": prior.text + "\n" + tail})
    req = req.model_copy(update={"current": current, "previous_candidate": prior})
    fields, trace = _run(monkeypatch, req, context, model)
    _held(fields[field])
    assert field in trace["previous_rejected"]
    if control == "stale":
        assert trace["previous_rejected"][field] == "stale_context"
    expected = {name: value for name, value in case["desired"].items() if name != field}
    assert _offers(fields) == expected, trace["decisions"]


@pytest.mark.parametrize("form,length", [("quote", 8), ("period", 16), ("dates", 16), ("extra", 8)])
def test_long_dynamic_independent_updates_preserve_original_candidate(monkeypatch, form, length):
    a, av, _, _ = _PAIR[form]["keep"]
    b, _, _, _ = _PAIR[form]["other"]
    prior = _seed(monkeypatch, form, derived=a in ("days", "excluded"))
    original = prior
    for turn in range(2, length + 1):
        if form == "quote":
            value = f"2026-12-{20 + turn:02d}"
            text = f"견적서 만료일을 12월 {20 + turn}일로 정합니다"
        elif form == "period":
            day = 24 + (turn % 7)
            value = f"2026-12-{day:02d}"
            text = f"실측 종료일은 12월 {day}일로 조정합니다"
        elif form == "dates":
            value = 1 + (turn % 6)
            text = f"작업 인원은 {value}명으로 바꿉니다"
        else:
            values = ["고정 나사 마모", "경첩 유격 발견", "부속품 변형", "손잡이 연결부 파손"]
            value = values[turn % len(values)]
            text = f"추가 사유는 {value}로 확정합니다"
        req = _request(form, text, prior=prior, request_id=f"r31-long-{turn}")
        fields, trace = _run(monkeypatch, req, _context(form), [_set(b, value, text), _keep(a)])
        assert _offers(fields) == {a: av, b: value}, (turn, trace["decisions"])
        _carried(fields[a], original)
        assert fields[b].origin_request_id == req.request_id
        assert not trace["previous_rejected"], (turn, trace["previous_rejected"])
        prior = _next_prior(req, fields)
        assert len(prior.text) <= 1000
        assert prior.baselines == {a: _CURRENT[form][a], b: _CURRENT[form][b]}


@pytest.mark.parametrize("form", ["quote", "extra"])
@pytest.mark.parametrize("limit", ["안으로", "내로", "범위에서"])
def test_amount_ceiling_is_not_exact_even_if_both_producers_agree(monkeypatch, form, limit):
    text = f"금액은 71만원 {limit} 제안해주세요. 부가세는 별도로 확정합니다"
    amount = _set("amount", 710000, f"금액은 71만원 {limit} 제안해주세요")
    vat = _set("vat", "excluded", "부가세는 별도로 확정합니다")
    req = _request(form, text, rules=[amount, vat])
    fields, trace = _run(monkeypatch, req, _context(form), [amount, vat])
    _held(fields["amount"])
    assert _offers(fields) == {"vat": "excluded"}, trace["decisions"]


@pytest.mark.parametrize(
    "reference",
    ["그 금액 제안 유지하고", "금액은 기존 제안 유지하고", "금액은 앞선 제안 유지하고"],
)
def test_proposal_reference_without_previous_cannot_be_current_keep(monkeypatch, reference):
    text = reference + " 공사기간 5일 넣어주세요"
    req = _request("quote", text)
    fields, trace = _run(
        monkeypatch,
        req,
        _context("quote"),
        [_keep("amount", reference, "retain_current"), _set("days", 5, "공사기간 5일")],
    )
    _held(fields["amount"])
    assert _offers(fields) == {"days": 5}, trace["decisions"]


def test_grouped_retention_expands_unique_subject_evidence(monkeypatch):
    prior = _seed(monkeypatch, "quote")
    text = "앞서 제안한 견적 금액과 유효기간 모두 그대로 유지해주세요"
    req = _request("quote", text, prior=prior)
    fields, _ = _run(
        monkeypatch,
        req,
        _context("quote"),
        [
            _keep("amount", "앞서 제안한 견적 금액", "retain_previous"),
            _keep("valid", text, "retain_previous"),
        ],
    )
    assert _offers(fields) == {item.field: item.value for item in prior.fields}
    for item in prior.fields:
        _carried(fields[item.field], prior)


@pytest.mark.parametrize(
    "tail",
    ["항목명은 이전 제안 유지", "다른 후보는 앞선 제안대로 유지"],
)
def test_new_amount_is_independent_of_verified_retention_clause(monkeypatch, tail):
    prior = _seed(monkeypatch, "extra")
    text = "추가금은 74만원으로 변경하고 " + tail
    req = _request("extra", text, prior=prior)
    fields, trace = _run(
        monkeypatch,
        req,
        _context("extra"),
        [
            _set("amount", 740000, "추가금은 74만원으로 변경"),
            _keep("title", tail, "retain_previous"),
        ],
    )
    assert _offers(fields) == {"amount": 740000, **{i.field: i.value for i in prior.fields}}, trace
    for item in prior.fields:
        _carried(fields[item.field], prior)


@pytest.mark.parametrize(
    "text",
    [
        "추가금은 74만원으로 변경하고 금액은 이전 제안 유지",
        "조건이 맞으면 추가금은 74만원으로 변경하고 항목명은 이전 제안 유지",
    ],
)
def test_retention_scope_does_not_hide_scalar_competition_or_condition(monkeypatch, text):
    prior = _seed(monkeypatch, "extra")
    amount = _set("amount", 740000, "추가금은 74만원으로 변경")
    req = _request("extra", text, prior=prior, rules=[amount])
    fields, _ = _run(monkeypatch, req, _context("extra"), [amount])
    _held(fields["amount"])


def test_exclusion_count_description_does_not_change_period_boundaries(monkeypatch):
    current = {**_CURRENT["period"], "start": "2026-12-10"}
    text = "실측 시작일 12월 3일, 종료일 12월 28일로 정합니다"
    req = _request("period", text, current=current, request_id="r31-period-seed")
    fields, trace = _run(
        monkeypatch,
        req,
        _context("period"),
        [
            _set("start", "2026-12-03", "실측 시작일 12월 3일"),
            _set("end", "2026-12-28", "종료일 12월 28일"),
        ],
    )
    assert _offers(fields) == {"start": "2026-12-03", "end": "2026-12-28"}, trace
    prior = _next_prior(req, fields)
    text = "제외일은 12월 6일, 19일 두 날짜로 확정합니다"
    req = _request("period", text, prior=prior, current=current, request_id="r31-period-next")
    fields, trace = _run(
        monkeypatch,
        req,
        _context("period"),
        [_set("excluded", ["2026-12-06", "2026-12-19"], text)],
    )
    assert _offers(fields) == {
        "start": "2026-12-03",
        "end": "2026-12-28",
        "excluded": ["2026-12-06", "2026-12-19"],
    }, trace
    _carried(fields["start"], prior)
    _carried(fields["end"], prior)


@pytest.mark.parametrize("form", ["period", "dates"])
def test_normalized_date_list_source_and_full_grounding(monkeypatch, form):
    name, value, _, _ = _PAIR[form]["keep"]
    sibling, _, _, _ = _PAIR[form]["other"]
    prior = _seed(monkeypatch, form, derived=True)
    value2, text = _PAIR[form]["next"]
    req = _request(form, text, prior=prior)
    fields, trace = _run(monkeypatch, req, _context(form), [_set(sibling, value2, text)])
    assert fields[name].value == value and fields[name].value_source == "derived"
    assert fields[name].correction is None
    _carried(fields[name], prior)
    assert name not in trace["previous_rejected"]
    damaged = prior.model_copy(
        update={
            "fields": [
                i.model_copy(update={"value": value[:1]}) if i.field == name else i
                for i in prior.fields
            ]
        }
    )
    req = _request(form, text, prior=damaged)
    fields, trace = _run(monkeypatch, req, _context(form), [_set(sibling, value2, text)])
    _held(fields[name])
    assert name in trace["previous_rejected"]


def test_date_correction_after_independent_valid_full_list_reset(monkeypatch):
    prior = _seed(monkeypatch, "dates", derived=True)
    invalid = "작업 날짜를 12월 8일, 14일, 17일로 변경합니다"
    req = _request("dates", invalid, prior=prior, request_id="r31-reset-invalid")
    fields, _ = _run(
        monkeypatch,
        req,
        _context("dates"),
        [_set("days", ["2026-12-08", "2026-12-14", "2026-12-17"], invalid, derived=True)],
    )
    _held(fields["days"])
    prior = _next_prior(req, fields)
    text = "작업 날짜는 12월 10일과 19일로 확정합니다"
    req = _request("dates", text, prior=prior, request_id="r31-reset-valid")
    fields, _ = _run(
        monkeypatch,
        req,
        _context("dates"),
        [_set("days", ["2026-12-10", "2026-12-19"], text, derived=True)],
    )
    prior = _next_prior(req, fields)
    text = "앞서 제안한 작업일 10일 대신 12일로 변경해 주세요"
    req = _request("dates", text, prior=prior, request_id="r31-reset-edit")
    fields, trace = _run(monkeypatch, req, _context("dates"), [_uncertain("days", text)])
    assert fields["days"].value == ["2026-12-12", "2026-12-19"], trace
    prior = _next_prior(req, fields)
    text = "인원은 4명으로 확정합니다"
    req = _request("dates", text, prior=prior, request_id="r31-reset-carry")
    fields, trace = _run(monkeypatch, req, _context("dates"), [_set("crew", 4, text)])
    assert _offers(fields) == {"days": ["2026-12-12", "2026-12-19"], "crew": 4}, trace
    _carried(fields["days"], prior)
    # 새로운 전체 목록의 확정과 이후 정정이 입증된 경우만 복구한다.
    damaged = prior.model_copy(update={"text": prior.text + "\n작업 날짜는 미정입니다"})
    req = _request("dates", text, prior=damaged)
    fields, trace = _run(monkeypatch, req, _context("dates"), [_set("crew", 4, text)])
    _held(fields["days"])
    assert "days" in trace["previous_rejected"]
