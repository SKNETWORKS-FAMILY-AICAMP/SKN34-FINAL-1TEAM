"""로컬 시험 계정 (app/seed.py)"""

import asyncio

import pytest

from app import seed
from tests.conftest import query
from tests.helpers import PASSWORD


def test_seed_makes_four_accounts_once_and_they_can_log_in(api):
    first = asyncio.run(seed.seed(PASSWORD))
    assert [made for _, made in first] == [True, True, True, True]
    again = asyncio.run(seed.seed(PASSWORD))
    assert [made for _, made in again] == [False, False, False, False]

    roles = {}
    for email, *_ in seed.ACCOUNTS:
        api.cookies.clear()
        r = api.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200
        me = api.get("/me").json()
        roles[email] = (me["role"], me["vendor_status"])
    assert roles == {
        "admin@mitjip.local": ("admin", None),
        "customer@mitjip.local": ("customer", None),
        "vendor@mitjip.local": ("vendor", "approved"),
        "vendor-pending@mitjip.local": ("vendor", "pending"),
    }
    assert query("select count(*) n from files where kind = 'license'")[0]["n"] == 2


def test_seed_refuses_outside_local(monkeypatch):
    monkeypatch.setattr(seed.settings, "app_env", "production")
    with pytest.raises(SystemExit):
        seed.main([])
    seed.main(["--auto"])  # docker compose up 때는 멈추지 않고 넘어간다
    assert query("select count(*) n from users")[0]["n"] == 0


def test_auto_seed_skips_without_password(monkeypatch, capsys):
    monkeypatch.setattr(seed.settings, "app_env", "local")
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    seed.main(["--auto"])
    assert "SEED_PASSWORD" in capsys.readouterr().out
    assert query("select count(*) n from users")[0]["n"] == 0


def test_auto_seed_makes_accounts_with_password(monkeypatch):
    monkeypatch.setattr(seed.settings, "app_env", "local")
    monkeypatch.setenv("SEED_PASSWORD", PASSWORD)
    seed.main(["--auto"])
    assert query("select count(*) n from users")[0]["n"] == 4
