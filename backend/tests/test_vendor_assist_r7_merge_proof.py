"""독립 원문 대입의 증명과 모델 보류 병합을 실제 서비스에서 검사한다."""

import pytest
from test_vendor_assist_r6structure_state import day, exact, fresh, hold, keep, run, withheld


@pytest.mark.parametrize(
    "label,verb",
    [
        ("방문 가능한 마감일", "제안해 주세요"),
        ("실측 가능한 종료일", "선택해 주세요"),
        ("접수 마감일", "반영해 주십시오"),
        ("방문 종료 날짜", "입력해 주세요"),
        ("종료일", "결정합니다"),
        ("실측 마감일", "설정하겠습니다"),
    ],
)
def test_complete_owned_deadline_overcomes_model_ambiguity(monkeypatch, label, verb):
    text = f"{label}을 12월 28일로 {verb}."
    rule = fresh("end", day(28), text)
    fields, trace = run(monkeypatch, "period", text, [rule], mode="both", model=[hold("end", text)])
    exact(fields["end"], day(28))
    assert fields["end"].source == "rule" and fields["end"].origin_request_id == "new"
    assert fields["end"].evidence in text
    assert trace["model_reviewed"][1]["status"] == "uncertain"
    assert fields["start"].action == fields["excluded"].action == "keep"


@pytest.mark.parametrize(
    "text",
    [
        "가능하면 방문 마감일을 12월 28일로 제안해 주세요.",
        "방문 마감일을 12월 28일로 제안할까요?",
        "방문 마감일을 12월 28일로 잠정 제안합니다.",
        "방문 마감일을 12월 28일로 제안해 주세요라고 말했지만 취소합니다.",
        "방문 마감일을 12월 28일로 제안하지 마세요.",
        "방문 마감일은 12월 28일 또는 30일로 제안해 주세요.",
        "방문 마감일을 12월 28일로 제안해 주세요. 마감일은 12월 30일로 정합니다.",
        "방문 마감일은 기존 후보 그대로 두고 12월 28일로 확정합니다.",
        "방문 마감일을 12월 28일로 제안해 주세요. 그 요청은 철회합니다.",
        "12월 28일로 방문 마감일을 제안할지 검토해 주세요.",
        "방문 마감일을 12월 28일로 제안한 것으로 기억합니다.",
        "방문 마감일을 12월 28일로 제안해 주세요. 아직 미정입니다.",
    ],
)
def test_ambiguous_original_does_not_yield_to_rule(monkeypatch, text):
    fields, _ = run(
        monkeypatch,
        "period",
        text,
        [fresh("end", day(28), text)],
        mode="both",
        model=[hold("end", text)],
    )
    withheld(fields["end"])


def test_positive_rule_not_independent_when_evidence_wrong(monkeypatch):
    text = "실측 가능한 마감일을 12월 28일로 제안해 주세요."
    # 같은 값이라고 해도 실재하지 않는 근거로는 검증된 규칙이 아니다.
    fields, _ = run(
        monkeypatch,
        "period",
        text,
        [fresh("end", day(28), "종료일은 12월 28일")],
        mode="both",
        model=[hold("end", text)],
    )
    withheld(fields["end"])


def test_model_current_retention_not_overridden(monkeypatch):
    text = "실측 가능한 마감일을 12월 28일로 제안해 주세요."
    fields, _ = run(
        monkeypatch,
        "period",
        text,
        [fresh("end", day(28), text)],
        mode="both",
        model=[keep("end", "현재 종료일 유지", "current")],
    )
    withheld(fields["end"])


def test_independent_deadline_and_inquiry(monkeypatch):
    end = "방문 가능한 마감일을 12월 28일로 제안해 주세요."
    inquiry = "제외일은 12월 15일도 가능할까요?"
    fields, _ = run(
        monkeypatch,
        "period",
        end + " " + inquiry,
        [fresh("end", day(28), end)],
        mode="both",
        model=[hold("end", end), hold("excluded", inquiry)],
    )
    exact(fields["end"], day(28))
    withheld(fields["excluded"])


def test_deadline_cannot_override_business_constraint(monkeypatch):
    text = "방문 가능한 마감일을 12월 18일로 제안해 주세요."
    fields, _ = run(
        monkeypatch,
        "period",
        text,
        [fresh("end", day(18), text)],
        mode="both",
        model=[hold("end", text)],
    )
    withheld(fields["end"])


def test_invalid_start_does_not_remove_independent_deadline(monkeypatch):
    end = "방문 가능한 마감일을 12월 28일로 제안해 주세요."
    start = "시작일은 12월 14일로 지정합니다."
    fields, _ = run(
        monkeypatch,
        "period",
        start + " " + end,
        [fresh("start", day(14), start), fresh("end", day(28), end)],
        mode="both",
        model=[hold("end", end), fresh("start", day(14), start)],
    )
    exact(fields["end"], day(28))
    withheld(fields["start"])
