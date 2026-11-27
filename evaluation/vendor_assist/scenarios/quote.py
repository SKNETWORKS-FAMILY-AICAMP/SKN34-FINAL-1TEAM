"""견적 네 필드와 미확정 후보의 연속 편집 시나리오."""


def scenarios():
    """Yield independent quote scenarios with complete current and per-turn action ops."""

    def case(name, steps, *, current=None, context=None):
        original = {
            "amount": 3_200_000,
            "vat": "excluded",
            "days": 2,
            "valid": None,
        }
        if current:
            original.update(current)
        item = {
            "id": "quote-" + name,
            "form": "quote",
            "current": original,
            "steps": [{"text": text, "ops": ops} for text, ops in steps],
        }
        if context is not None:
            item["context"] = dict(context)
        return item

    yield case(
        "explicit-multi-field",
        [
            (
                "견적 금액을 370만 원으로 적고 부가세는 포함, 공사 기간은 4일로 설정해 주세요.",
                [
                    ("set", "amount", 3_700_000),
                    ("set", "vat", "included"),
                    ("set", "days", 4),
                ],
            ),
        ],
    )

    yield case(
        "clear-valid-only",
        [
            (
                "견적 유효기간만 비워 주세요. 금액은 건드리지 마세요.",
                [("clear", "valid")],
            ),
        ],
        current={"valid": "2026-12-24"},
    )

    yield case(
        "deny-valid-clear",
        [
            (
                "유효기간 삭제 요청이 아니라 확인차 말씀드린 겁니다. 지금 값은 지우지 말아 주세요.",
                [],
            ),
        ],
        current={"valid": "2026-12-24"},
    )

    yield case(
        "undecided-amount-vat",
        [
            (
                (
                    "견적 금액은 410만 원인지 440만 원인지 아직 못 정했고, "
                    "부가세도 별도와 포함 중 어느 쪽인지 고민 중입니다."
                ),
                [("hold", "amount"), ("hold", "vat")],
            ),
        ],
    )

    yield case(
        "cancel-pending-amount",
        [
            (
                "견적을 520만 원으로 변경하는 안을 제시해 주세요.",
                [("set", "amount", 5_200_000)],
            ),
            (
                (
                    "앞서 제안한 520만 원 변경은 취소합니다. 현재 폼 금액은 그대로 두고 "
                    "공사 기간만 5일로 고쳐 주세요."
                ),
                [("reset", "amount"), ("set", "days", 5)],
            ),
        ],
    )

    yield case(
        "manual-amount-stales-old",
        [
            (
                "견적은 510만 원, 공사 기간은 3일로 제안해 주세요.",
                [("set", "amount", 5_100_000), ("set", "days", 3)],
            ),
            (
                (
                    "금액 칸은 제가 직접 다시 고쳤으니 이전 금액 제안은 덮어쓰지 말고, "
                    "아까 제안한 공사 기간만 그대로 사용해 주세요."
                ),
                [("manual", "amount", 4_800_000), ("previous", "days")],
            ),
        ],
    )

    yield case(
        "three-turn-provenance",
        [
            (
                "금액은 590만 원, 부가세는 별도로 제안해 주세요.",
                [("set", "amount", 5_900_000), ("set", "vat", "excluded")],
            ),
            (
                "금액은 방금 제안한 대로 유지하고 부가세 조건만 포함으로 정정해 주세요.",
                [("previous", "amount"), ("set", "vat", "included")],
            ),
            (
                "견적 금액과 부가세는 앞서 정리한 안으로 두고, 공사 기간은 3일로 조정해 주세요.",
                [("previous", "amount"), ("previous", "vat"), ("set", "days", 3)],
            ),
        ],
        current={"vat": None},
    )

    yield case(
        "correct-pending-amount",
        [
            (
                "견적은 420만 원으로, 작업 기간은 3일로 제안해 주세요.",
                [("set", "amount", 4_200_000), ("set", "days", 3)],
            ),
            (
                (
                    "앞서 420만 원이라고 했는데 475만 원으로 정정하겠습니다. "
                    "공사 기간은 그때 제안한 3일 그대로요."
                ),
                [("set", "amount", 4_750_000), ("previous", "days")],
            ),
        ],
    )

    yield case(
        "december-validity-followup",
        [
            (
                "이 견적의 유효기간을 12월 28일까지로 바꿔 주세요.",
                [("set", "valid", "2026-12-28")],
            ),
            (
                "방금 지정한 유효기간은 유지하고, 공사 기간만 5일로 변경해 주세요.",
                [("previous", "valid"), ("set", "days", 5)],
            ),
        ],
        current={"valid": "2026-12-18"},
        context={"days_max": 7},
    )

    yield case(
        "clear-then-reassign-validity",
        [
            (
                "이 견적의 유효기간은 일단 지워 주세요.",
                [("clear", "valid")],
            ),
            (
                "아니요, 방금 지우자는 건 취소하고 유효기간을 12월 31일까지로 지정해 주세요.",
                [("set", "valid", "2026-12-31")],
            ),
        ],
        current={"valid": "2026-12-21"},
    )

    yield case(
        "hold-pending-but-keep-vat",
        [
            (
                "견적 금액은 440만 원으로 제안하고, 부가세는 면세로 처리해 주세요.",
                [("set", "amount", 4_400_000), ("set", "vat", "exempt")],
            ),
            (
                (
                    "금액은 440만 원인지 490만 원인지 확실하지 않으니 잠시 보류하고, "
                    "면세 조건은 앞에서 말한 대로 유지해 주세요."
                ),
                [("hold", "amount"), ("previous", "vat")],
            ),
        ],
    )
