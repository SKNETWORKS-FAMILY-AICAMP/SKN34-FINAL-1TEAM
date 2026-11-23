"""추가금·사유·항목과 부가세의 편집 시나리오."""


def scenarios():
    def case(name, steps, **overrides):
        current = {
            "title": "걸레받이 보수",
            "reason": "철거 중 파손 발견",
            "amount": 180000,
            "vat": "excluded",
        }
        current.update(overrides)
        return {
            "id": "extra-" + name,
            "form": "extra",
            "current": current,
            "steps": [{"text": text, "ops": ops} for text, ops in steps],
        }

    yield case(
        "clear-reason",
        [
            ("추가금 사유만 지워 주세요.", [("clear", "reason")]),
        ],
    )
    yield case(
        "deny-clear-reason",
        [
            ("추가금 사유는 지우지 말아 주세요.", []),
        ],
    )
    yield case(
        "wrong-field",
        [
            (
                "사유 말고 공사 제목을 몰딩 재시공으로 바꿔 주세요.",
                [("set", "title", "몰딩 재시공")],
            ),
        ],
    )
    yield case(
        "price-correction",
        [
            (
                "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 세금 조건은 그대로요.",
                [("set", "amount", 215000)],
            ),
        ],
    )
    yield case(
        "price-uncertain",
        [
            (
                "18만 원을 21만 5천 원으로 할까 고민 중인데 아직 결정 못 했어요.",
                [("hold", "amount")],
            ),
        ],
    )
    yield case(
        "vat-included",
        [
            (
                "금액은 그대로 두고 부가세 포함으로만 바꿔 주세요.",
                [("set", "vat", "included")],
            ),
        ],
    )
    yield case(
        "vat-exempt",
        [
            (
                "면세로 정정하고 사유는 기존 내용 그대로 남겨 주세요.",
                [("set", "vat", "exempt")],
            ),
        ],
        vat="included",
    )
    yield case(
        "pending-correction",
        [
            (
                "금액은 24만 원, 부가세 포함으로 수정해 주세요.",
                [("set", "amount", 240000), ("set", "vat", "included")],
            ),
            (
                "아, 금액만 26만 원으로 다시 정정할게요. 아까 세금 조건은 유지해 주세요.",
                [("set", "amount", 260000), ("previous", "vat")],
            ),
            (
                "사유도 자재 추가 소요로 바꿔 주세요.",
                [("set", "reason", "자재 추가 소요")],
            ),
        ],
    )
    yield case(
        "pending-manual-stale",
        [
            (
                "제목은 문틀 보강, 금액은 30만 원으로 변경해 주세요.",
                [("set", "title", "문틀 보강"), ("set", "amount", 300000)],
            ),
            (
                "제가 직접 입력한 금액은 그대로 두고 부가세 포함으로 바꿔 주세요. 아까 제목 변경도 유지해 주세요.",
                [
                    ("manual", "amount", 280000),
                    ("previous", "title"),
                    ("set", "vat", "included"),
                ],
            ),
        ],
    )
    yield case(
        "pending-hold",
        [
            (
                "사유는 현장 요청으로 추가 작업, 금액은 35만 원으로 바꿔 주세요.",
                [
                    ("set", "reason", "현장 요청으로 추가 작업"),
                    ("set", "amount", 350000),
                ],
            ),
            (
                "금액은 35만 원인지 38만 원인지 아직 확정 못 했어요. 사유는 아까 바꾼 걸로 두세요.",
                [("hold", "amount"), ("previous", "reason")],
            ),
        ],
    )
