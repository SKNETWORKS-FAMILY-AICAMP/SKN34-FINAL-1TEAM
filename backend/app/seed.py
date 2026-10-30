"""로컬 개발용 시험 계정을 만든다. APP_ENV=local에서만 돈다.

.env에 SEED_PASSWORD가 있으면 docker compose up 때 migrate 뒤에 자동으로 돈다 (compose.override.yaml).
손으로 다시 돌릴 때: docker compose exec api python -m app.seed
관리자 · 고객 · 업체(승인) · 업체(승인 대기) 4개를 만들고, 이미 있으면 건너뛴다.
비밀번호는 넷이 같고 SEED_PASSWORD에서 읽는다. 손으로 돌릴 때 비어 있으면 입력받는다. 화면에 찍지 않는다.
"""

import asyncio
import getpass
import os
import sys

from app.auth import repository as auth_repo
from app.auth import service as auth_service
from app.config import settings
from app.db import SessionLocal, engine
from app.files import service as files_service
from app.shared.clock import now_utc
from app.vendor.models import Vendor

# (이메일, 이름, 역할, 업체 상태). 관리자를 먼저 만들어야 업체 승인자로 남길 수 있다.
ACCOUNTS = [
    ("admin@mitjip.local", "관리자", "admin", None),
    ("customer@mitjip.local", "시험 고객", "customer", None),
    ("vendor@mitjip.local", "시험 업체", "vendor", "approved"),
    ("vendor-pending@mitjip.local", "승인 대기 업체", "vendor", "pending"),
]


def _test_pdf() -> bytes:
    """업체 가입에 꼭 필요한 사업자등록증 자리를 채우는 한 쪽짜리 시험용 PDF ('TEST ONLY')."""
    text = b"BT /F1 18 Tf 30 70 Td (TEST ONLY - not a real business license) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 480 160] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(text), text),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


LICENSE = _test_pdf()


async def seed(password: str) -> list[tuple[str, bool]]:
    """계정마다 (이메일, 새로 만들었는지)를 돌려준다."""
    done: list[tuple[str, bool]] = []
    admin_id = None
    async with SessionLocal() as s:
        for email, name, role, status in ACCOUNTS:
            user = await auth_repo.user_by_email(s, email)
            if user is None:
                user = await auth_service.create_user(
                    s, email=email, name=name, password=password, role=role
                )
                if role == "vendor":
                    await _add_vendor(s, user.id, status, admin_id)
                await s.commit()
                done.append((email, True))
            else:
                done.append((email, False))
            if role == "admin":
                admin_id = user.id
    await engine.dispose()
    return done


async def _add_vendor(s, user_id, status: str, admin_id) -> None:
    lic = files_service.Prepared(
        kind="license",
        data=LICENSE,
        content_type="application/pdf",
        filename="시험용-사업자등록증.pdf",
    )
    row = await files_service.persist(s, user_id, lic)
    approved = status == "approved"
    try:
        s.add(
            Vendor(
                user_id=user_id,
                license_file_id=row.id,
                status=status,
                reviewed_by=admin_id if approved else None,
                reviewed_at=now_utc() if approved else None,
            )
        )
        await s.flush()
    except Exception:
        files_service.discard(row)  # 업체 행을 못 만들었으면 등록증 파일도 남기지 않는다
        raise


def main(argv: list[str]) -> None:
    """--auto: docker compose up 때 migrate가 부른다. 만들 수 없으면 알리고 넘어간다(api가 뜨는 것을 막지 않게)."""
    auto = "--auto" in argv

    def stop(msg: str) -> None:
        if not auto:
            raise SystemExit(msg)
        print(f"{msg} 시험 계정은 만들지 않았어요.")

    if settings.app_env != "local":
        return stop("시험 계정은 로컬(APP_ENV=local)에서만 만들 수 있어요.")
    password = os.environ.get("SEED_PASSWORD", "")
    if not password and auto:
        return stop(".env의 SEED_PASSWORD가 비어 있어요.")
    password = password or getpass.getpass("시험 계정 비밀번호 (8자 이상): ")
    if len(password) < 8:
        return stop("비밀번호는 8자 이상이어야 해요.")
    try:
        done = asyncio.run(seed(password))
    except Exception as exc:
        if not auto:
            raise
        return stop(f"시험 계정을 만들다 오류가 났어요 ({type(exc).__name__}).")
    for email, made in done:
        print(f"{'만들었어요' if made else '이미 있어요'}: {email}")


if __name__ == "__main__":
    main(sys.argv[1:])
