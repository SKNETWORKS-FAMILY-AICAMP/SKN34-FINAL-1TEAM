"""운영 객체 저장소는 실제 AWS 호출 없이 key와 오류 처리를 검증한다."""

import io

import pytest
from botocore.exceptions import ClientError

from app.config import settings
from app.files import storage


class FakeS3:
    def __init__(self):
        self.items: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body):
        assert Bucket == "mitjip-test-files"
        self.items[Key] = Body

    def get_object(self, *, Bucket, Key):
        assert Bucket == "mitjip-test-files"
        if Key not in self.items:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        return {"Body": io.BytesIO(self.items[Key])}

    def delete_object(self, *, Bucket, Key):
        assert Bucket == "mitjip-test-files"
        self.items.pop(Key, None)


def test_s3_put_read_delete(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(settings, "files_s3_bucket", "mitjip-test-files")
    monkeypatch.setattr(storage, "_s3", lambda: fake)
    key = storage.save(b"test")
    assert key.count("/") == 2
    assert storage.read(key) == b"test"
    storage.remove(key)
    with pytest.raises(FileNotFoundError):
        storage.read(key)


def test_s3_missing_object_returns_file_not_found(monkeypatch):
    monkeypatch.setattr(settings, "files_s3_bucket", "mitjip-test-files")
    monkeypatch.setattr(storage, "_s3", FakeS3)
    with pytest.raises(FileNotFoundError):
        storage.read("2026/10/not-found")
