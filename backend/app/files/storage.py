"""로컬 파일 저장소와 AWS S3 저장소. 앱에서는 같은 key를 사용한다."""

from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.config import settings
from app.shared.clock import now_utc
from app.shared.ids import uuid7


def _root() -> Path:
    return Path(settings.files_dir).resolve()

def _s3():
    return boto3.client("s3")


def save(data: bytes) -> str:
    """내용을 저장하고 저장 위치(key)를 돌려준다. 원래 파일 이름은 쓰지 않는다."""
    key = f"{now_utc():%Y/%m}/{uuid7().hex}"
    if settings.files_s3_bucket:
        _s3().put_object(Bucket=settings.files_s3_bucket, Key=key, Body=data)
        return key
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)  # 다 쓴 뒤에 이름을 바꿔서 반쯤 쓴 파일이 보이지 않게
    return key


def read(key: str) -> bytes:
    """S3 파일을 읽는다. 없는 파일은 로컬 저장소와 똑같이 FileNotFoundError를 돌려준다."""
    if not settings.files_s3_bucket:
        return path_of(key).read_bytes()
    try:
        obj = _s3().get_object(Bucket=settings.files_s3_bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            raise FileNotFoundError(key) from exc
        raise
    with obj["Body"] as body:
        return body.read()


def path_of(key: str) -> Path:
    path = (_root() / key).resolve()
    if _root() not in path.parents:
        raise ValueError("storage key outside root")
    return path


def remove(key: str) -> None:
    if settings.files_s3_bucket:
        _s3().delete_object(Bucket=settings.files_s3_bucket, Key=key)
        return
    try:
        path_of(key).unlink(missing_ok=True)
    except ValueError:
        pass
