"""공사 날짜 테스트 자료는 실행 서버의 시간대에 영향을 받지 않는다."""

import runpy
from datetime import datetime
from pathlib import Path

import pytest

from app.shared import clock


@pytest.mark.parametrize(
    ("at", "start", "end"),
    [
        ("2026-11-23T14:59:00+00:00", "2026-12-23", "2026-12-27"),
        ("2026-11-23T15:00:00+00:00", "2026-12-24", "2026-12-28"),
    ],
)
def test_project_fixture_uses_seoul_day_at_utc_boundary(monkeypatch, at, start, end):
    monkeypatch.setattr(clock, "now_utc", lambda: datetime.fromisoformat(at))
    # 별도 모듈 문맥에서 읽어 다른 테스트가 가져간 SOON 상수는 바꾸지 않는다.
    fixture = runpy.run_path(str(Path(__file__).with_name("test_project.py")))
    assert fixture["SOON"] == start
    assert fixture["SOON_END"] == end
