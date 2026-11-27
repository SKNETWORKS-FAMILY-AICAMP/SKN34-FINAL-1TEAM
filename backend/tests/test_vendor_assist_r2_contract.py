"""R2 원본 요청·모델 응답 재생 및 의도 검증 대조 시험.

동결 G100 결과의 요청과 model_raw를 보존한 독립 스냅숏이다.
외부 평가 파일이나 실 API 없이 실제 서비스 검증·병합을 호출한다.
"""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CandidateField,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

# qa179-generalization100-results.json SHA256:
# 21c12177c65d430d7c65838c56761436572473421ced9f697ebbb8e0d430fd3d
# qa179-generalization100-frozen.json SHA256:
# 303cee7d272e6958a791e23478d699735ee205a8ae5978efcc5c5999999f70bc
# 필드 순서: 이름, 동작, 값, 근거, 의도, 값 출처, 정정 관계(끝의 null은 생략).
_ARCHIVED = [
    {
        "label": "dates-10-T1",
        "request": {
            "request_id": "35ca2520-4ae9-4236-83e3-b5b6260efd05",
            "form": "dates",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "12월 12일과 20일에 작업 가능할지 확인 부탁드려요. 인원은 4명으로 확정",
            "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            "previous_candidate": None,
            "rule_fields": [
                ["days", "keep", None, ""],
                [
                    "crew",
                    "set",
                    4,
                    "12월 12일과 20일에 작업 가능할지 확인 부탁드려요. 인원은 4명으로 확정",
                ],
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
            [
                "days",
                "set",
                ["2026-12-12", "2026-12-20"],
                "12월 12일과 20일에 작업 가능할지 확인",
                "explicit_change",
                "derived",
            ],
            ["crew", "set", 4, "인원은 4명으로 확정", "explicit_change", "utterance"],
        ],
        "desired": {"crew": 4},
        "hold": ["days"],
        "released": [],
        "retained": [],
    },
    {
        "label": "period-10-T1",
        "request": {
            "request_id": "9b2d758b-5100-4802-84c4-1da5932c05e5",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "제외일을 12월 12일로 해도 될까요? 종료일은 12월 30일로 변경해주세요",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": None,
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
            [
                "end",
                "set",
                "2026-12-30",
                "종료일은 12월 30일로 변경해주세요",
                "explicit_change",
                "utterance",
            ],
            [
                "excluded",
                "set",
                ["2026-12-12"],
                "제외일을 12월 12일로 해도 될까요?",
                "explicit_change",
                "utterance",
            ],
        ],
        "desired": {"end": "2026-12-30"},
        "hold": ["excluded"],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-16-T1",
        "request": {
            "request_id": "79fb1827-be45-4e45-be0d-75fbf131cc9e",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "추가 항목명은 선반 보강, 사유는 벽체 고정 불량으로 제안",
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
                    "선반 보강",
                    "추가 항목명은 선반 보강, 사유는 벽체 고정 불량으로 제안",
                ],
                [
                    "reason",
                    "set",
                    "벽체 고정 불량으로 제안",
                    "추가 항목명은 선반 보강, 사유는 벽체 고정 불량으로 제안",
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
            [
                "title",
                "set",
                "선반 보강",
                "추가 항목명은 선반 보강",
                "explicit_change",
                "utterance",
            ],
            [
                "reason",
                "set",
                "벽체 고정 불량",
                "사유는 벽체 고정 불량으로 제안",
                "explicit_change",
                "utterance",
            ],
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"title": "선반 보강", "reason": "벽체 고정 불량"},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "extra-24-T1",
        "request": {
            "request_id": "7df1d5e4-3293-48b5-987c-d1e8fa50b722",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "추가 항목명은 주방 문짝 조절로 확정",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": None,
            "rule_fields": [
                ["title", "set", "주방 문짝 조절로 확정", "추가 항목명은 주방 문짝 조절로 확정"],
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
            [
                "title",
                "set",
                "주방 문짝 조절",
                "추가 항목명은 주방 문짝 조절로 확정",
                "explicit_change",
                "utterance",
            ],
            ["reason", "keep", None, "", "no_mention"],
            ["amount", "keep", None, "", "no_mention"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"title": "주방 문짝 조절"},
        "hold": [],
        "released": [],
        "retained": [],
    },
    {
        "label": "quote-18-T2",
        "request": {
            "request_id": "b068c8d5-1115-4980-9c97-b77644e618b5",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "공사 일수만 10일로 바꾸고 유효기간은 앞서 제안한 날짜 그대로",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "a47dede6-1e49-484e-af90-823865e61761",
                "text": "공사 일수 12일, 유효기간 12월 23일로 제안",
                "fields": [
                    ["days", "set", 12, "공사 일수 12일"],
                    ["valid", "set", "2026-12-23", "유효기간 12월 23일"],
                ],
                "sources": {"days": "llm", "valid": "llm"},
                "origins": {
                    "days": "a47dede6-1e49-484e-af90-823865e61761",
                    "valid": "a47dede6-1e49-484e-af90-823865e61761",
                },
                "baselines": {"days": 3, "valid": "2026-12-17"},
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
            ["days", "set", 10, "공사 일수만 10일로 바꾸고", "explicit_change", "utterance"],
            [
                "valid",
                "keep",
                None,
                "유효기간은 앞서 제안한 날짜 그대로",
                "retain_previous",
                "previous",
            ],
        ],
        "desired": {"days": 10, "valid": "2026-12-23"},
        "hold": [],
        "released": [],
        "retained": ["valid"],
    },
    {
        "label": "quote-23-T4",
        "request": {
            "request_id": "a0703bf5-88da-45d5-81cd-5e4f0184830f",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "견적과 부가세는 앞서 제안한 대로 유지하고 공사는 4일로 확정",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "2c0a21ab-767d-432d-9b96-f7139afbe90c",
                "text": "견적 527만원과 세금 별도 제안 부탁드립니다\n금액은 550만 원인지 590만 원인지 아직 미정이에요. 세금은 이전 제안 그대로\n견적 금액은 561만원으로 결정했습니다. 부가세는 앞서 정한 방식대로",
                "fields": [
                    ["amount", "set", 5610000, "견적 금액은 561만원으로 결정했습니다"],
                    ["vat", "set", "excluded", "세금 별도"],
                ],
                "sources": {"amount": "llm", "vat": "llm"},
                "origins": {
                    "amount": "2c0a21ab-767d-432d-9b96-f7139afbe90c",
                    "vat": "2ea98baf-6693-43dc-9ec9-89001ea37ef8",
                },
                "baselines": {"amount": 4550000, "vat": "included"},
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
            [
                "amount",
                "keep",
                None,
                "견적과 부가세는 앞서 제안한 대로 유지",
                "retain_previous",
                "previous",
            ],
            [
                "vat",
                "keep",
                None,
                "견적과 부가세는 앞서 제안한 대로 유지",
                "retain_previous",
                "previous",
            ],
            ["days", "set", 4, "공사는 4일로 확정", "explicit_change", "utterance"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 5610000, "vat": "excluded", "days": 4},
        "hold": [],
        "released": [],
        "retained": ["amount", "vat"],
    },
    {
        "label": "period-21-T4",
        "request": {
            "request_id": "64fbda42-2719-4bb5-aebb-c72c107d6a3c",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "시작일과 종료일, 제외일은 앞서 제안한 값으로 유지해 주세요",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "b95a8c32-ac99-4536-9e71-ee04c2f7ab89",
                "text": "12월 3일부터 30일까지를 실측 기간으로 제안\n제외일 12월 9일과 21일을 추가로 제안해 주세요\n앞서 제안한 제외일 중 21일 대신 23일로 옮겨 주세요. 기간은 앞서 제안한 그대로",
                "fields": [
                    ["start", "set", "2026-12-03", "12월 3일부터"],
                    ["end", "set", "2026-12-30", "30일까지"],
                    [
                        "excluded",
                        "set",
                        ["2026-12-09", "2026-12-23"],
                        "앞서 제안한 제외일 중 21일 대신 23일로 옮겨 주세요. 기간은 앞서 제안한 그대로",
                        "explicit_change",
                        "derived",
                        {
                            "kind": "replace",
                            "rejected_value": ["2026-12-09", "2026-12-21"],
                            "rejected_evidence": "21일",
                            "accepted_evidence": "23일",
                        },
                    ],
                ],
                "sources": {"start": "llm", "end": "llm", "excluded": "rule"},
                "origins": {
                    "start": "6a539cfb-bf14-40a2-bbab-d6db871c7dfd",
                    "end": "6a539cfb-bf14-40a2-bbab-d6db871c7dfd",
                    "excluded": "b95a8c32-ac99-4536-9e71-ee04c2f7ab89",
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
            [
                "start",
                "keep",
                None,
                "시작일과 종료일, 제외일은 앞서 제안한 값으로 유지해 주세요",
                "retain_previous",
                "previous",
            ],
            [
                "end",
                "keep",
                None,
                "시작일과 종료일, 제외일은 앞서 제안한 값으로 유지해 주세요",
                "retain_previous",
                "previous",
            ],
            [
                "excluded",
                "keep",
                None,
                "시작일과 종료일, 제외일은 앞서 제안한 값으로 유지해 주세요",
                "retain_previous",
                "previous",
            ],
        ],
        "desired": {
            "start": "2026-12-03",
            "end": "2026-12-30",
            "excluded": ["2026-12-09", "2026-12-23"],
        },
        "hold": [],
        "released": [],
        "retained": ["start", "end", "excluded"],
    },
    {
        "label": "extra-21-T2",
        "request": {
            "request_id": "4e35be91-daa3-46c0-b427-0eb866c051b1",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "추가금만 71만원으로 바꾸고 항목명과 사유는 앞서 제안한 대로 유지",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "63f277f1-39e2-4edf-b90b-a4a82c1580bf",
                "text": "항목은 문틀 재고정, 사유는 고정철물 손상, 추가 금액 66만원으로 제안",
                "fields": [
                    ["title", "set", "문틀 재고정", "항목은 문틀 재고정"],
                    ["reason", "set", "고정철물 손상", "사유는 고정철물 손상"],
                    ["amount", "set", 660000, "추가 금액 66만원"],
                ],
                "sources": {"title": "llm", "reason": "llm", "amount": "llm"},
                "origins": {
                    "title": "63f277f1-39e2-4edf-b90b-a4a82c1580bf",
                    "reason": "63f277f1-39e2-4edf-b90b-a4a82c1580bf",
                    "amount": "63f277f1-39e2-4edf-b90b-a4a82c1580bf",
                },
                "baselines": {"title": "보강", "reason": "현장 추가", "amount": 1000000},
            },
            "rule_fields": [
                ["title", "keep", None, ""],
                ["reason", "keep", None, ""],
                [
                    "amount",
                    "set",
                    710000,
                    "추가금만 71만원으로 바꾸고 항목명과 사유는 앞서 제안한 대로 유지",
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
            [
                "title",
                "keep",
                None,
                "항목명과 사유는 앞서 제안한 대로 유지",
                "retain_previous",
                "previous",
            ],
            [
                "reason",
                "keep",
                None,
                "항목명과 사유는 앞서 제안한 대로 유지",
                "retain_previous",
                "previous",
            ],
            ["amount", "set", 710000, "추가금만 71만원으로 바꾸고", "explicit_change", "utterance"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"title": "문틀 재고정", "reason": "고정철물 손상", "amount": 710000},
        "hold": [],
        "released": [],
        "retained": ["title", "reason"],
    },
    {
        "label": "quote-21-T3",
        "request": {
            "request_id": "d647b501-bd70-4aee-95e2-aba4b5585d22",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "부가세 별도로 정하고 나머지는 그대로 유지해 주세요",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "85bd0fdc-780f-46e3-9ceb-3697a01a4fb8",
                "text": "견적 764만원, 공사 6일로 우선 제안\n금액은 783만원으로 올려주세요. 일수는 앞서 정한 대로",
                "fields": [
                    ["amount", "set", 7830000, "금액은 783만원으로 올려주세요"],
                    ["days", "set", 6, "공사 6일"],
                ],
                "sources": {"amount": "llm", "days": "llm"},
                "origins": {
                    "amount": "85bd0fdc-780f-46e3-9ceb-3697a01a4fb8",
                    "days": "593f6102-f905-4386-b67d-119b62ca6040",
                },
                "baselines": {"amount": 4550000, "days": 3},
            },
            "rule_fields": [
                ["amount", "keep", None, ""],
                ["vat", "set", "excluded", "부가세 별도로 정하고 나머지는 그대로 유지해 주세요"],
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
            ["amount", "keep", None, "나머지는 그대로 유지해 주세요", "retain_current", "current"],
            ["vat", "set", "excluded", "부가세 별도로 정하고", "explicit_change", "utterance"],
            ["days", "keep", None, "나머지는 그대로 유지해 주세요", "retain_current", "current"],
            ["valid", "keep", None, "나머지는 그대로 유지해 주세요", "retain_current", "current"],
        ],
        "desired": {"amount": 7830000, "days": 6, "vat": "excluded"},
        "hold": [],
        "released": [],
        "retained": ["amount", "days"],
    },
    {
        "label": "quote-22-T3",
        "request": {
            "request_id": "668d5ebd-524b-4996-85d2-04482c9e19f7",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "견적 금액은 현재 입력한 값대로 두겠습니다. 기간은 아까 정한 그대로 유지",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "c5274d56-cc08-49ac-99dc-820bcf71cbe2",
                "text": "작업 기간 14일, 견적 1230만원으로 제안\n기간을 15일로 바꾸고 견적은 유지해 주세요",
                "fields": [
                    ["amount", "set", 12300000, "견적 1230만원"],
                    ["days", "set", 15, "기간을 15일로 바꾸고"],
                ],
                "sources": {"amount": "llm", "days": "llm"},
                "origins": {
                    "amount": "005c07b9-a60a-446a-b324-1e4d9d7603e2",
                    "days": "c5274d56-cc08-49ac-99dc-820bcf71cbe2",
                },
                "baselines": {"amount": 4550000, "days": 3},
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
            [
                "amount",
                "keep",
                None,
                "견적 금액은 현재 입력한 값대로 두겠습니다",
                "retain_current",
                "current",
            ],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "keep", None, "기간은 아까 정한 그대로 유지", "retain_previous", "previous"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"days": 15},
        "hold": [],
        "released": ["amount"],
        "retained": ["days"],
    },
    {
        "label": "quote-25-T2",
        "request": {
            "request_id": "3482982f-999b-4bcc-94b2-b50a8ce15f36",
            "form": "quote",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "공사 기간 19일로 바꾸고 견적 금액은 그대로 유지해 주세요",
            "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
            "previous_candidate": {
                "request_id": "dfe0c512-af38-4c12-9c24-176fe7a6941b",
                "text": "견적 239만원, 작업기간 2일로 제안",
                "fields": [
                    ["amount", "set", 2390000, "견적 239만원"],
                    ["days", "set", 2, "작업기간 2일로 제안"],
                ],
                "sources": {"amount": "llm", "days": "llm"},
                "origins": {
                    "amount": "dfe0c512-af38-4c12-9c24-176fe7a6941b",
                    "days": "dfe0c512-af38-4c12-9c24-176fe7a6941b",
                },
                "baselines": {"amount": 4550000, "days": 3},
            },
            "rule_fields": [
                ["amount", "keep", None, ""],
                ["vat", "keep", None, ""],
                ["days", "set", 19, "공사 기간 19일로 바꾸고 견적 금액은 그대로 유지해 주세요"],
                ["valid", "keep", None, ""],
            ],
        },
        "options": {
            "today": "2026-11-24",
            "baseMonth": "2026-12-01",
            "daysMax": 15,
            "crewMax": 6,
            "vatModes": ["included", "excluded", "exempt"],
        },
        "model": [
            [
                "amount",
                "keep",
                None,
                "견적 금액은 그대로 유지해 주세요",
                "retain_current",
                "current",
            ],
            ["vat", "keep", None, "", "no_mention"],
            ["days", "set", 19, "공사 기간 19일로 바꾸고", "explicit_change", "utterance"],
            ["valid", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 2390000},
        "hold": ["days"],
        "released": [],
        "retained": ["amount"],
    },
    {
        "label": "period-16-T2",
        "request": {
            "request_id": "ba6f7901-cfed-4360-8e18-a78057e3b806",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "시작일만 12월 9일로 바꾸고 종료일은 앞서 제안한 대로 유지",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "a20ebefa-858f-4502-9bea-e24e4d225e97",
                "text": "실측 기간 12월 6일부터 25일까지 먼저 제안해 주세요",
                "fields": [
                    ["start", "set", "2026-12-06", "12월 6일부터"],
                    ["end", "set", "2026-12-25", "25일까지"],
                ],
                "sources": {"start": "llm", "end": "llm"},
                "origins": {
                    "start": "a20ebefa-858f-4502-9bea-e24e4d225e97",
                    "end": "a20ebefa-858f-4502-9bea-e24e4d225e97",
                },
                "baselines": {"start": "2026-12-02", "end": "2026-12-24"},
            },
            "rule_fields": [
                [
                    "start",
                    "set",
                    "2026-12-09",
                    "시작일만 12월 9일로 바꾸고 종료일은 앞서 제안한 대로 유지",
                ],
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
            [
                "start",
                "set",
                "2026-12-09",
                "시작일만 12월 9일로 바꾸고",
                "explicit_change",
                "utterance",
            ],
            ["end", "keep", None, "종료일은 앞서 제안한 대로 유지", "retain_previous", "previous"],
            ["excluded", "keep", None, "", "no_mention"],
        ],
        "desired": {"start": "2026-12-09", "end": "2026-12-25"},
        "hold": [],
        "released": [],
        "retained": ["end"],
    },
    {
        "label": "period-25-T4",
        "request": {
            "request_id": "20227906-6d93-4ff1-a7c7-add3bf3e54b0",
            "form": "period",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "실측 기간과 제외일은 앞서 제안한 그대로 유지해주세요",
            "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
            "previous_candidate": {
                "request_id": "b7f07c76-9b68-401c-bfd2-4b12ffa0368b",
                "text": "실측 시작일 12월 11일, 종료일 12월 31일로 제안\n제외일은 12월 7일로 제안해주세요\n제외일은 12월 15일로 바꾸고 앞서 정한 기간 유지",
                "fields": [
                    ["start", "set", "2026-12-11", "실측 시작일 12월 11일"],
                    ["end", "set", "2026-12-31", "종료일 12월 31일"],
                    ["excluded", "set", ["2026-12-15"], "제외일은 12월 15일로 바꾸고"],
                ],
                "sources": {"start": "llm", "end": "llm", "excluded": "llm"},
                "origins": {
                    "start": "6c135bfe-4f51-4430-bde9-b5f411c9dd38",
                    "end": "6c135bfe-4f51-4430-bde9-b5f411c9dd38",
                    "excluded": "b7f07c76-9b68-401c-bfd2-4b12ffa0368b",
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
            [
                "start",
                "keep",
                None,
                "실측 기간과 제외일은 앞서 제안한 그대로 유지해주세요",
                "retain_previous",
                "previous",
            ],
            [
                "end",
                "keep",
                None,
                "실측 기간과 제외일은 앞서 제안한 그대로 유지해주세요",
                "retain_previous",
                "previous",
            ],
            [
                "excluded",
                "keep",
                None,
                "실측 기간과 제외일은 앞서 제안한 그대로 유지해주세요",
                "retain_previous",
                "previous",
            ],
        ],
        "desired": {"start": "2026-12-11", "end": "2026-12-31", "excluded": ["2026-12-15"]},
        "hold": [],
        "released": [],
        "retained": ["start", "end", "excluded"],
    },
    {
        "label": "dates-22-T2",
        "request": {
            "request_id": "454a2054-a14c-4303-a921-c88ff7f93928",
            "form": "dates",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "작업자는 두 명으로 수정하고 날짜는 아까 정한 값으로 유지",
            "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            "previous_candidate": {
                "request_id": "6abe0e6a-577a-4a92-b4f1-2feeaf276a6c",
                "text": "작업일 12월 7일하고 28일, 작업 인원 4명으로 제안",
                "fields": [
                    [
                        "days",
                        "set",
                        ["2026-12-07", "2026-12-28"],
                        "작업일 12월 7일하고 28일",
                        "explicit_change",
                        "derived",
                    ],
                    ["crew", "set", 4, "작업 인원 4명"],
                ],
                "sources": {"days": "llm", "crew": "llm"},
                "origins": {
                    "days": "6abe0e6a-577a-4a92-b4f1-2feeaf276a6c",
                    "crew": "6abe0e6a-577a-4a92-b4f1-2feeaf276a6c",
                },
                "baselines": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            },
            "rule_fields": [
                ["days", "keep", None, ""],
                ["crew", "set", 2, "작업자는 두 명으로 수정하고 날짜는 아까 정한 값으로 유지"],
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
            ["days", "keep", None, "날짜는 아까 정한 값으로 유지", "retain_previous", "previous"],
            ["crew", "set", 2, "작업자는 두 명으로 수정", "explicit_change", "utterance"],
        ],
        "desired": {"days": ["2026-12-07", "2026-12-28"], "crew": 2},
        "hold": [],
        "released": [],
        "retained": ["days"],
    },
    {
        "label": "dates-25-T2",
        "request": {
            "request_id": "bd05ee90-0ac3-48c6-bbae-25623e926831",
            "form": "dates",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "작업일은 12월 3일, 18일, 27일로 변경하고 인원은 앞서 제안한 그대로 유지",
            "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            "previous_candidate": {
                "request_id": "2be428ff-7918-40ec-99b5-a2ad7a4e0895",
                "text": "작업 날짜는 12월 3일하고 18일, 인원 2명으로 제안해주세요",
                "fields": [
                    ["days", "set", ["2026-12-03", "2026-12-18"], "작업 날짜는 12월 3일하고 18일"],
                    ["crew", "set", 2, "인원 2명"],
                ],
                "sources": {"days": "llm", "crew": "llm"},
                "origins": {
                    "days": "2be428ff-7918-40ec-99b5-a2ad7a4e0895",
                    "crew": "2be428ff-7918-40ec-99b5-a2ad7a4e0895",
                },
                "baselines": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
            },
            "rule_fields": [
                [
                    "days",
                    "set",
                    ["2026-12-03", "2026-12-18", "2026-12-27"],
                    "작업일은 12월 3일, 18일, 27일로 변경하고 인원은 앞서 제안한 그대로 유지",
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
            [
                "days",
                "set",
                ["2026-12-03", "2026-12-18", "2026-12-27"],
                "작업일은 12월 3일, 18일, 27일로 변경",
                "explicit_change",
                "utterance",
            ],
            ["crew", "keep", None, "인원은 앞서 제안한 그대로 유지", "retain_previous", "previous"],
        ],
        "desired": {"crew": 2},
        "hold": ["days"],
        "released": [],
        "retained": ["crew"],
    },
    {
        "label": "extra-19-T2",
        "request": {
            "request_id": "b83bceac-caf6-4964-957a-8222481c05c4",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "항목명 변경 요청은 취소하겠습니다. 추가 금액은 앞서 제안한 그대로 유지해 주세요",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "561376af-dbc3-4619-9f56-aed484643a94",
                "text": "항목명은 창호 고정, 추가금은 53만원으로 제안해 주세요",
                "fields": [
                    ["title", "set", "창호 고정", "항목명은 창호 고정"],
                    ["amount", "set", 530000, "추가금은 53만원"],
                ],
                "sources": {"title": "llm", "amount": "llm"},
                "origins": {
                    "title": "561376af-dbc3-4619-9f56-aed484643a94",
                    "amount": "561376af-dbc3-4619-9f56-aed484643a94",
                },
                "baselines": {"title": "보강", "amount": 1000000},
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
            [
                "title",
                "keep",
                None,
                "항목명 변경 요청은 취소하겠습니다",
                "retain_current",
                "current",
            ],
            ["reason", "keep", None, "", "no_mention"],
            [
                "amount",
                "keep",
                None,
                "추가 금액은 앞서 제안한 그대로 유지해 주세요",
                "retain_previous",
                "previous",
            ],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"amount": 530000},
        "hold": [],
        "released": ["title"],
        "retained": ["amount"],
    },
    {
        "label": "extra-22-T2",
        "request": {
            "request_id": "b17da19b-2237-4c1a-9424-872538e803e9",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "사유는 바닥 하부 균열로 정정하고 금액은 앞서 제안한 그대로",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "dfafb001-5e6a-4ca3-9a43-f2c96b62f06c",
                "text": "사유는 바닥 모서리 파손, 추가금 34만원으로 제안",
                "fields": [
                    ["reason", "set", "바닥 모서리 파손", "사유는 바닥 모서리 파손"],
                    ["amount", "set", 340000, "추가금 34만원"],
                ],
                "sources": {"reason": "llm", "amount": "llm"},
                "origins": {
                    "reason": "dfafb001-5e6a-4ca3-9a43-f2c96b62f06c",
                    "amount": "dfafb001-5e6a-4ca3-9a43-f2c96b62f06c",
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
            [
                "reason",
                "set",
                "바닥 하부 균열",
                "사유는 바닥 하부 균열로 정정",
                "explicit_change",
                "utterance",
            ],
            ["amount", "keep", None, "금액은 앞서 제안한 그대로", "retain_previous", "previous"],
            ["vat", "keep", None, "", "no_mention"],
        ],
        "desired": {"reason": "바닥 하부 균열", "amount": 340000},
        "hold": [],
        "released": [],
        "retained": ["amount"],
    },
    {
        "label": "extra-25-T2",
        "request": {
            "request_id": "454ce877-1733-4742-9236-fe24498fc8a4",
            "form": "extra",
            "resource_id": "00000000-0000-4000-8000-000000000179",
            "text": "부가세는 면세로 선택하고 항목명과 금액은 앞서 제안한 대로 유지",
            "current": {
                "title": "보강",
                "reason": "현장 추가",
                "amount": 1000000,
                "vat": "included",
            },
            "previous_candidate": {
                "request_id": "71aac11b-5184-4b75-813e-5ff0623abe72",
                "text": "추가 항목은 배관 지지대 교체, 금액 93만원으로 제안",
                "fields": [
                    ["title", "set", "배관 지지대 교체", "추가 항목은 배관 지지대 교체"],
                    ["amount", "set", 930000, "금액 93만원"],
                ],
                "sources": {"title": "llm", "amount": "llm"},
                "origins": {
                    "title": "71aac11b-5184-4b75-813e-5ff0623abe72",
                    "amount": "71aac11b-5184-4b75-813e-5ff0623abe72",
                },
                "baselines": {"title": "보강", "amount": 1000000},
            },
            "rule_fields": [
                ["title", "keep", None, ""],
                ["reason", "keep", None, ""],
                ["amount", "keep", None, ""],
                [
                    "vat",
                    "set",
                    "exempt",
                    "부가세는 면세로 선택하고 항목명과 금액은 앞서 제안한 대로 유지",
                ],
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
            [
                "title",
                "keep",
                None,
                "항목명과 금액은 앞서 제안한 대로 유지",
                "retain_previous",
                "previous",
            ],
            ["reason", "keep", None, "", "no_mention"],
            [
                "amount",
                "keep",
                None,
                "항목명과 금액은 앞서 제안한 대로 유지",
                "retain_previous",
                "previous",
            ],
            ["vat", "set", "exempt", "부가세는 면세로 선택", "explicit_change", "utterance"],
        ],
        "desired": {"title": "배관 지지대 교체", "amount": 930000},
        "hold": ["vat"],
        "released": [],
        "retained": ["title", "amount"],
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


def _run(monkeypatch, request, context, model, *, rule_only=False):
    assert not settings.llm_live
    calls = []

    async def stub(data, _context):
        calls.append(data)
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(request, context, use_model=not rule_only, trace=trace))
    assert len(calls) == int(not rule_only)
    assert result.request_id == request.request_id and result.form == request.form
    assert result.source == ("rule" if rule_only else "llm")
    if calls:
        # 실제 폼 값은 미적용 후보로 바뀌지 않은 채 모델에 전달되어야 한다.
        assert calls[0].current == request.current
        assert trace["model_raw"] == [item.model_dump(mode="json") for item in model]
    return {item.field: item for item in result.fields}, trace


def _offers(fields):
    return {
        field: item.value
        for field, item in fields.items()
        if item.status == "valid" and item.action in ("set", "clear")
    }


def _assert_held(item: CandidateField):
    assert item.status in ("invalid", "uncertain"), item
    assert item.action == "uncertain" and item.value is None and not item.alternatives, item


@pytest.mark.parametrize("case", _ARCHIVED, ids=lambda case: case["label"])
def test_archived_r2_service_contract(monkeypatch, case):
    request, context, model = _snapshot(case)
    fields, trace = _run(monkeypatch, request, context, model)
    assert _offers(fields) == case["desired"], trace["decisions"]
    # 잘린 문자열 및 문의값은 충돌 대안으로도 제공하면 안 된다.
    assert not any(item.alternatives for item in fields.values()), trace["decisions"]
    for name in case["hold"]:
        _assert_held(fields[name])
    for name in case["released"]:
        item = fields[name]
        assert (item.status, item.action, item.intent_kind, item.value_source) == (
            "valid",
            "keep",
            "retain_current",
            "current",
        )
        assert item.origin_request_id == request.request_id
    if request.previous_candidate:
        previous = request.previous_candidate
        for name in case["retained"]:
            item = fields[name]
            old = next(f for f in previous.fields if f.field == name)
            assert item.origin_request_id == previous.origins.get(name, previous.request_id)
            assert item.source == previous.sources.get(name, "rule")
            assert item.evidence == old.evidence
            if old.value_source == "derived" and old.correction is not None:
                assert item.value_source == "derived"
                assert item.correction == old.correction


# 별도 반례는 원본 정답을 바꾸지 않고 동일한 서비스에 입력한다.
_CONTEXT = AssistContext(
    today=date(2026, 11, 24),
    base_month=date(2026, 12, 1),
    crew_max=6,
    required_days=2,
)
_CURRENT = {
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "quote": {"amount": 4_550_000, "days": 3, "valid": "2026-12-17", "vat": "included"},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1_000_000, "vat": "included"},
}


def _request(form, text, *, rules=(), prior=None, current=None):
    return AssistRequest(
        request_id="r2-contract-new",
        form=form,
        resource_id=UUID(int=179),
        text=text,
        current=_CURRENT[form] if current is None else current,
        rule_fields=list(rules),
        previous_candidate=prior,
    )


def _set(field, value, evidence):
    return FieldIntent(field=field, action="set", value=value, evidence=evidence)


def _keep(field, evidence, *, kind="retain_previous"):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source="previous" if kind == "retain_previous" else "current",
    )


@pytest.mark.parametrize("rule_only", [False, True])
@pytest.mark.parametrize(
    "form,text,field,value,other,other_value,other_evidence",
    [
        (
            "period",
            "제외일은 12월 12일로 해도 될까요? 종료일은 12월 30일로 변경해주세요",
            "excluded",
            ["2026-12-12"],
            "end",
            "2026-12-30",
            "종료일은 12월 30일로 변경해주세요",
        ),
        (
            "dates",
            "12월 12일과 20일에 작업 가능할지 확인 부탁드려요. 인원은 4명으로 확정",
            "days",
            ["2026-12-12", "2026-12-20"],
            "crew",
            4,
            "인원은 4명으로 확정",
        ),
    ],
)
def test_question_with_grounded_dates_is_not_selectable(
    monkeypatch, rule_only, form, text, field, value, other, other_value, other_evidence
):
    # 짧게 잘린 근거에 물음표가 없어도 전체 절의 문의를 확정으로 바꾸지 않는다.
    clipped = text.split("로 해도")[0] if form == "period" else "12월 12일과 20일"
    rejected = _set(field, value, clipped)
    accepted = _set(other, other_value, other_evidence)
    request = _request(form, text, rules=[rejected, accepted])
    fields, _ = _run(monkeypatch, request, _CONTEXT, [rejected, accepted], rule_only=rule_only)
    _assert_held(fields[field])
    assert _offers(fields) == {other: other_value}


@pytest.mark.parametrize(
    "form,text,field,value,other,other_value,other_evidence",
    [
        (
            "period",
            "제외일은 12월 12일로 확정해 주세요. 종료일은 12월 30일로 변경해주세요",
            "excluded",
            ["2026-12-12"],
            "end",
            "2026-12-30",
            "종료일은 12월 30일로 변경해주세요",
        ),
        (
            "dates",
            "12월 12일과 20일에 작업해 주세요. 인원은 4명으로 확정",
            "days",
            ["2026-12-12", "2026-12-20"],
            "crew",
            4,
            "인원은 4명으로 확정",
        ),
    ],
)
def test_definite_dates_and_polite_request_are_allowed(
    monkeypatch, form, text, field, value, other, other_value, other_evidence
):
    candidate = _set(field, value, text.split(".")[0])
    accepted = _set(other, other_value, other_evidence)
    request = _request(form, text, rules=[candidate, accepted])
    fields, _ = _run(monkeypatch, request, _CONTEXT, [candidate, accepted])
    assert _offers(fields) == {field: value, other: other_value}
    assert not any(item.alternatives for item in fields.values())


@pytest.mark.parametrize(
    "field,label,value",
    [
        ("title", "항목명", "확정 도면 보완"),
        ("reason", "사유", "제안 도면과 실제 치수 차이"),
    ],
)
def test_real_noun_containing_assignment_word_is_preserved(monkeypatch, field, label, value):
    text = f"{label}은 {value}로 확정해 주세요"
    good = _set(field, value, text)
    bad = _set(field, value + "로 확정해 주세요", text)
    request = _request("extra", text, rules=[bad])
    fields, _ = _run(monkeypatch, request, _CONTEXT, [good])
    assert _offers(fields) == {field: value}
    assert not fields[field].alternatives


def _quote_prior():
    return PreviousCandidate(
        request_id="r2-old",
        text="견적 527만원과 세금 별도 제안 부탁드립니다",
        fields=[
            _set("amount", 5_270_000, "견적 527만원"),
            _set("vat", "excluded", "세금 별도"),
        ],
        sources={"amount": "llm", "vat": "rule"},
        origins={"amount": "r2-origin-amount", "vat": "r2-origin-vat"},
        baselines={"amount": 4_550_000, "vat": "included"},
    )


@pytest.mark.parametrize("variant", ["stale", "missing"])
def test_grouped_retention_cannot_recover_unverified_prior(monkeypatch, variant):
    prior = _quote_prior()
    current = dict(_CURRENT["quote"])
    if variant == "stale":
        current["amount"] = 4_800_000
    else:
        prior = prior.model_copy(
            update={
                "fields": [prior.fields[1]],
                "sources": {"vat": "rule"},
                "origins": {"vat": "r2-origin-vat"},
                "baselines": {"vat": "included"},
            }
        )
    retained = "견적과 부가세는 앞서 제안한 대로 유지"
    text = retained + "하고 공사는 4일로 확정"
    model = [
        _keep("amount", retained),
        _keep("vat", retained),
        _set("days", 4, "공사는 4일로 확정"),
    ]
    fields, _ = _run(
        monkeypatch, _request("quote", text, prior=prior, current=current), _CONTEXT, model
    )
    _assert_held(fields["amount"])
    assert _offers(fields) == {"vat": "excluded", "days": 4}
    assert fields["vat"].origin_request_id == "r2-origin-vat"
    assert fields["vat"].source == "rule"


@pytest.mark.parametrize(
    "tail",
    [
        "하지만 견적 금액은 아직 미정이에요",
        "견적 금액을 유지할지 아직 모르겠어요",
        "견적 금액 변경 요청은 취소할까요?",
    ],
)
def test_same_field_later_uncertainty_prevents_retention(monkeypatch, tail):
    retained = "견적은 앞서 제안한 대로 유지"
    text = retained + ". " + tail + ". 부가세는 앞서 제안한 대로 유지"
    model = [_keep("amount", retained), _keep("vat", "부가세는 앞서 제안한 대로 유지")]
    fields, _ = _run(monkeypatch, _request("quote", text, prior=_quote_prior()), _CONTEXT, model)
    _assert_held(fields["amount"])
    assert _offers(fields) == {"vat": "excluded"}


def test_explicit_current_retention_never_promotes_pending_value(monkeypatch):
    prior = _quote_prior()
    text = "견적 금액은 현재 입력한 값대로 두겠습니다. 부가세는 앞서 제안한 대로 유지"
    model = [
        _keep("amount", "견적 금액은 현재 입력한 값대로 두겠습니다", kind="retain_current"),
        _keep("vat", "부가세는 앞서 제안한 대로 유지"),
    ]
    fields, _ = _run(monkeypatch, _request("quote", text, prior=prior), _CONTEXT, model)
    assert _offers(fields) == {"vat": "excluded"}
    assert (fields["amount"].status, fields["amount"].action, fields["amount"].value_source) == (
        "valid",
        "keep",
        "current",
    )
    assert fields["amount"].origin_request_id == "r2-contract-new"
    assert fields["vat"].origin_request_id == "r2-origin-vat"
