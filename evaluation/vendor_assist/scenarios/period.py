"""실측 가능 기간의 유지·변경·삭제 시나리오."""


def scenarios():
    """Yield independent period cases with complete baseline form values."""

    def case(name, steps, **current_overrides):
        current = {
            "start": "2026-12-05",
            "end": "2026-12-22",
            "excluded": ["2026-12-09", "2026-12-16"],
        }
        current.update(current_overrides)
        current["excluded"] = list(current["excluded"])
        return {
            "id": "period-" + name,
            "form": "period",
            "current": current,
            "steps": [{"text": text, "ops": ops} for text, ops in steps],
        }

    yield case(
        "clear-excluded",
        [
            (
                "공사 기간은 그대로 두고 제외할 날짜만 전부 지워 주세요.",
                [("clear", "excluded")],
            ),
        ],
    )

    yield case(
        "deny-clear-excluded",
        [
            (
                "제외 날짜를 삭제하라는 뜻이 아니에요. 지금 적힌 날짜 둘 다 그대로 두세요.",
                [],
            ),
        ],
    )

    yield case(
        "clear-end",
        [
            ("시작일은 놔두고 종료일만 비워 주세요.", [("clear", "end")]),
        ],
        excluded=[],
    )

    yield case(
        "deny-clear-end",
        [
            ("끝나는 날짜는 지우지 말고 기존 22일 그대로 유지해 주세요.", []),
        ],
    )

    yield case(
        "set-start",
        [
            (
                "기간 시작일을 12월 7일로 옮겨 주세요. 종료일과 제외일은 그대로요.",
                [("set", "start", "2026-12-07")],
            ),
        ],
    )

    yield case(
        "unsure-start",
        [
            (
                "시작일을 12월 7일로 할지 고민 중이에요. 아직 확정하지 않았어요.",
                [("hold", "start")],
            ),
        ],
    )

    yield case(
        "wrong-field-excluded",
        [
            (
                "12월 22일 종료일은 건드리지 마세요. 제외할 날 중 16일만 18일로 고쳐 주세요.",
                [("set", "excluded", ["2026-12-09", "2026-12-18"])],
            ),
        ],
    )

    yield case(
        "range-year-boundary",
        [
            (
                "올해 12월 30일부터 내년 1월 10일까지로 기간을 바꿔 주세요. 1월 2일 제외는 유지해요.",
                [("set", "start", "2026-12-30"), ("set", "end", "2027-01-10")],
            ),
        ],
        start="2026-12-28",
        end="2027-01-12",
        excluded=["2027-01-02"],
    )

    yield case(
        "pending-excluded-replacement",
        [
            (
                "제외할 날을 12월 9일과 16일로 변경해 주세요.",
                [("set", "excluded", ["2026-12-09", "2026-12-16"])],
            ),
            (
                "아까 정한 제외일 중 9일 대신 11일로 정정해 주세요. 16일은 유지요.",
                [("replace", "excluded", "2026-12-09", "2026-12-11")],
            ),
            (
                "제외일은 바로 전에 수정한 대로 유지해 주세요. 기간은 그대로 두고요.",
                [("previous", "excluded")],
            ),
        ],
        excluded=["2026-12-08"],
    )

    yield case(
        "pending-manual-stale",
        [
            (
                "시작은 12월 7일, 종료는 12월 24일로 각각 수정해 주세요.",
                [("set", "start", "2026-12-07"), ("set", "end", "2026-12-24")],
            ),
            (
                "시작일은 폼에서 제가 12월 6일로 직접 바꿨어요. 아까 말한 종료일 24일 변경만 살려 주세요.",
                [("manual", "start", "2026-12-06"), ("previous", "end")],
            ),
        ],
    )

    yield case(
        "pending-uncertain-end",
        [
            (
                "시작일을 12월 8일로, 종료일을 12월 25일로 옮겨 주세요.",
                [("set", "start", "2026-12-08"), ("set", "end", "2026-12-25")],
            ),
            (
                "종료일은 25일로 할지 27일로 할지 아직 모르겠어요. 시작일 8일 변경안은 유지해요.",
                [("hold", "end"), ("previous", "start")],
            ),
            (
                "끝나는 날은 원래 22일로 두고, 제외일은 9일, 15일, 16일로 바꿔 주세요. 시작일은 아까대로요.",
                [
                    ("set", "excluded", ["2026-12-09", "2026-12-15", "2026-12-16"]),
                    ("previous", "start"),
                ],
            ),
        ],
    )

    yield case(
        "reset-end-keep-excluded",
        [
            (
                "기간 종료일은 12월 26일로, 제외할 날은 12월 10일로 변경해 주세요.",
                [("set", "end", "2026-12-26"), ("set", "excluded", ["2026-12-10"])],
            ),
            (
                "아까 종료일을 26일로 바꾸자는 건 취소해 주세요. 제외일 10일 변경은 그대로 살려 주세요.",
                [("reset", "end"), ("previous", "excluded")],
            ),
        ],
    )
