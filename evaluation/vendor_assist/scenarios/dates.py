"""#179 공사 날짜·인원 자동 평가용 독립 시나리오.

표준 라이브러리만 사용하며 모델 응답·규칙 후보를 직접 생성하지 않는다.
기준일은 2026-11-23, 기준월은 2026-12-01이다. 별도로 지정하지 않은
시나리오에서는 공휴일·주말 제한을 두지 않는다.

각 단계의 ops는 모델 출력이 아닌, 사람이 의도한 폼 상태 변화의
방법 독립적 정답 스크립트다. 여러 턴은 중간 턴에서 제안만 유지하고
마지막 턴에서만 적용한다. 수동 수정은 해당 턴의 해석 전에 발생한다.
"""

from __future__ import annotations


def _days(*numbers: int) -> list[str]:
    """2026년 12월 날짜의 ISO 문자열 목록을 만든다."""
    return [f"2026-12-{number:02d}" for number in numbers]


def _scenario(
    name: str,
    days: tuple[int, ...],
    crew: int,
    steps: list[dict],
    *,
    context: dict | None = None,
) -> dict:
    return {
        "id": f"dates-{name}",
        "form": "dates",
        "current": {"days": _days(*days), "crew": crew},
        "steps": steps,
        "context": {"required_days": len(days), **(context or {})},
    }


def scenarios():
    """서로 다른 12개 독립 사례를 순서대로 반환한다."""

    # 날짜·인원을 함께 명시적으로 바꾸는 단일 턴.
    yield _scenario(
        "set-both",
        (1, 2, 3),
        2,
        [
            {
                "text": "이번 현장 작업 날짜는 12월 14일, 15일, 16일로 정하고 투입 인원은 4명으로 늘려 주세요.",
                "ops": [
                    ("set", "days", _days(14, 15, 16)),
                    ("set", "crew", 4),
                ],
            }
        ],
    )

    # 언급하지 않은 날짜 목록은 유지한다.
    yield _scenario(
        "crew-only",
        (12, 14, 15),
        2,
        [
            {
                "text": "정해 둔 작업 날짜는 건드리지 말고, 현장에 오는 사람 수만 3명으로 조정해 주세요.",
                "ops": [("set", "crew", 3)],
            }
        ],
    )

    # 명시적 삭제와 단순 '미정'은 다르다. 삭제 대상은 인원 칸 하나다.
    yield _scenario(
        "clear-crew",
        (17, 18, 19),
        4,
        [
            {
                "text": "공사 날짜는 남겨 두고 투입 인원 칸의 숫자만 삭제해 주세요. 배치할 사람은 아직 미정입니다.",
                "ops": [("clear", "crew")],
            }
        ],
    )

    # 부정된 삭제 명령은 clear가 아니다.
    yield _scenario(
        "deny-clear",
        (2, 3, 4),
        3,
        [
            {
                "text": "인원 칸을 지우지는 말아 주세요. 기존 공사 날짜도 이번에는 변경하지 않습니다.",
                "ops": [],
            }
        ],
    )

    # 바꿀 수도 있다는 발화는 확정된 새로운 작업일이 아니다.
    yield _scenario(
        "tentative-days",
        (9, 10, 11),
        2,
        [
            {
                "text": "작업 날짜를 12월 16일, 17일, 18일로 옮길까 생각 중이지만 아직 결정하지 않았어요.",
                "ops": [("hold", "days")],
            }
        ],
    )

    # 이 사례만 주말/공휴일 제약을 명시한다.
    yield _scenario(
        "weekday-context",
        (14, 15),
        2,
        [
            {
                "text": "주말에는 작업하지 않는 현장입니다. 공사 날짜를 12월 17일과 18일로 확정하고 인원은 그대로 유지할게요.",
                "ops": [("set", "days", _days(17, 18))],
            }
        ],
        context={"required_days": 2, "no_weekend": True, "holidays": ["2026-12-25"]},
    )

    # 앞 턴에 아직 적용하지 않은 전체 목록 중 한 날만 교체한다.
    yield _scenario(
        "pending-replace-one",
        (3, 4, 5),
        2,
        [
            {
                "text": "새 작업 계획은 12월 10일, 11일, 12일로 잡고 작업자는 3명으로 적어 주세요.",
                "ops": [
                    ("set", "days", _days(10, 11, 12)),
                    ("set", "crew", 3),
                ],
            },
            {
                "text": "지금 제안한 날 중 11일 대신 13일을 넣겠습니다. 인원 3명 제안은 그대로 둘게요.",
                "ops": [
                    ("replace", "days", "2026-12-11", "2026-12-13"),
                    ("previous", "crew"),
                ],
            },
        ],
    )

    # 직접 수정된 인원을 이전 제안의 값으로 덮어쓰면 안 된다.
    yield _scenario(
        "manual-crew-stale",
        (1, 2, 3),
        2,
        [
            {
                "text": "공사 날짜는 12월 7일, 8일, 9일로 제안하고 투입 인원은 4명으로 해주세요.",
                "ops": [
                    ("set", "days", _days(7, 8, 9)),
                    ("set", "crew", 4),
                ],
            },
            {
                "text": "인원은 제가 입력 칸에서 직접 5명으로 고쳐 뒀어요. 4명 제안은 버리고 앞에서 고른 날짜 제안만 유지해 주세요.",
                "ops": [
                    ("manual", "crew", 5),
                    ("reset", "crew"),
                    ("previous", "days"),
                ],
            },
        ],
    )

    # 모호한 후속 날짜 변경은 보류하되 다른 필드의 미적용 후보는 유지한다.
    yield _scenario(
        "hold-days-keep-crew",
        (6, 7, 8),
        2,
        [
            {
                "text": "현장 날짜를 12월 15일, 16일, 17일로 바꾸고 투입할 인원은 4명으로 설정해 주세요.",
                "ops": [
                    ("set", "days", _days(15, 16, 17)),
                    ("set", "crew", 4),
                ],
            },
            {
                "text": "날짜 변경안은 아직 고민 중이니 확정하지 않겠습니다. 인원 네 명 변경안은 그대로 적용해 주세요.",
                "ops": [
                    ("hold", "days"),
                    ("previous", "crew"),
                ],
            },
        ],
    )

    # 오래된 날짜 제안은 취소하고 이전 인원 제안은 새 값으로 덮는다.
    yield _scenario(
        "reset-days-supersede-crew",
        (20, 21, 22),
        2,
        [
            {
                "text": "작업할 날짜는 12월 24일, 26일, 28일로 바꾸고 인력은 세 명으로 적어 주세요.",
                "ops": [
                    ("set", "days", _days(24, 26, 28)),
                    ("set", "crew", 3),
                ],
            },
            {
                "text": "앞서 요청한 새 날짜 계획은 취소합니다. 인원만 다시 5명으로 바꿔 주세요.",
                "ops": [
                    ("reset", "days"),
                    ("set", "crew", 5),
                ],
            },
        ],
    )

    # 세 턴 동안 날짜·인원 후보의 출처와 값이 유지된다.
    yield _scenario(
        "three-turn-chain",
        (2, 3, 5),
        2,
        [
            {
                "text": "새 작업 날짜는 12월 4일, 6일, 8일로 잡아 주세요.",
                "ops": [("set", "days", _days(4, 6, 8))],
            },
            {
                "text": "작업 날짜 제안은 그대로 두고 현장 인원만 세 명으로 늘리겠습니다.",
                "ops": [
                    ("previous", "days"),
                    ("set", "crew", 3),
                ],
            },
            {
                "text": "마지막으로 그 날짜들 중 6일 대신 7일에 작업합니다. 인원은 앞서 정한 세 명 그대로요.",
                "ops": [
                    ("replace", "days", "2026-12-06", "2026-12-07"),
                    ("previous", "crew"),
                ],
            },
        ],
    )

    # 이전 날짜 후보가 수동 수정 때문에 무효여도 인원 후보까지 버리지 않는다.
    yield _scenario(
        "manual-days-stale",
        (10, 11, 12),
        2,
        [
            {
                "text": "예정된 공사 날짜를 12월 17일, 18일, 19일로 바꾸고 현장에는 3명 배치해 주세요.",
                "ops": [
                    ("set", "days", _days(17, 18, 19)),
                    ("set", "crew", 3),
                ],
            },
            {
                "text": "작업 날짜 입력란은 제가 직접 수정해 두었으니 앞의 날짜 제안은 폐기하세요. 인원 세 명 제안만 이어서 사용합니다.",
                "ops": [
                    ("manual", "days", _days(21, 22, 23)),
                    ("reset", "days"),
                    ("previous", "crew"),
                ],
            },
        ],
    )
