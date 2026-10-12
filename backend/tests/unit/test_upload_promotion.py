"""FileService.promote_upload: what each malware scan outcome does to a
patient upload row, and that a row is only ever decided once.

Also the download guard — a file that is still in quarantine, or was
rejected, is never handed out."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError
from app.integrations import s3
from app.modules.files import service as service_module
from app.modules.files.service import UPLOAD_ABANDONED_AFTER, FileService

UPLOAD_ID = uuid4()


class FakeRepo:
    def __init__(self, status="scanning", age=timedelta(minutes=2)):
        self.row = {
            "mhf_id": UPLOAD_ID,
            "patient_id": uuid4(),
            "clinic_id": uuid4(),
            "status": status,
            "s3_key": "regions/r/clinics/c/patients/p/patient_upload/x_report.pdf",
            "s3_version_id": None,
            "file_name": "report.pdf",
            "created_at": datetime.now(UTC) - age,
        }
        self.finished: list[dict] = []

    async def get_for_update(self, _id):
        return self.row

    async def get(self, _id):
        return self.row

    async def finish_scan(self, _id, **fields):
        self.finished.append(fields)
        self.row["status"] = fields["status"]
        return self.row


@pytest.fixture
def promote(monkeypatch):
    """promote(scan_result_dict, repo) -> (status, repo, copies_attempted)"""

    @asynccontextmanager
    async def no_rls(_session):
        yield

    monkeypatch.setattr(service_module, "as_system", no_rls)
    monkeypatch.setattr(service_module, "emit_event", AsyncMock())

    async def run(result: dict, repo: FakeRepo | None = None):
        calls = []
        monkeypatch.setattr(s3, "promote_if_clean", lambda upload_id, dest: calls.append((upload_id, dest)) or result)
        svc = FileService(session=None)
        svc.mhf = repo or FakeRepo()
        return await svc.promote_upload(UPLOAD_ID), svc.mhf, calls

    return run


async def test_clean_file_becomes_unverified_with_key_version_and_checksum(promote):
    status, repo, calls = await promote({"scan": "NO_THREATS_FOUND", "size": 5, "checksum": "abc", "version_id": "v1"})
    assert status == "unverified"
    assert calls == [(str(UPLOAD_ID), repo.row["s3_key"])]
    assert repo.finished == [
        {"status": "unverified", "scan_result": "NO_THREATS_FOUND", "file_size": 5, "checksum": "abc", "s3_version_id": "v1"}
    ]


@pytest.mark.parametrize("scan", ["THREATS_FOUND", "UNSUPPORTED", "ACCESS_DENIED", "FAILED", "SOMETHING_NEW"])
async def test_every_other_scan_status_is_rejected(promote, scan):
    status, repo, _ = await promote({"scan": scan})
    assert status == "rejected"
    assert repo.finished[0]["status"] == "rejected" and repo.finished[0]["scan_result"] == scan
    assert repo.finished[0]["s3_version_id"] is None


async def test_not_yet_scanned_stays_scanning(promote):
    status, repo, _ = await promote({"scan": s3.NOT_SCANNED})
    assert status == "scanning" and repo.finished == []


async def test_bytes_not_arrived_yet_stays_scanning(promote):
    status, repo, _ = await promote({"scan": s3.NOT_UPLOADED})
    assert status == "scanning" and repo.finished == []


async def test_upload_that_never_arrived_is_given_up_on(promote):
    old = FakeRepo(age=UPLOAD_ABANDONED_AFTER + timedelta(minutes=1))
    status, repo, _ = await promote({"scan": s3.NOT_UPLOADED}, old)
    assert status == "rejected" and repo.finished[0]["scan_result"] == s3.NOT_UPLOADED


@pytest.mark.parametrize("decided", ["unverified", "verified", "rejected"])
async def test_repeated_event_does_nothing_to_a_decided_row(promote, decided):
    status, repo, calls = await promote({"scan": "NO_THREATS_FOUND"}, FakeRepo(status=decided))
    assert status == decided
    assert calls == [] and repo.finished == []


@pytest.mark.parametrize("status", ["scanning", "rejected"])
async def test_unpromoted_file_cannot_be_downloaded(status):
    svc = FileService(session=None)
    svc.mhf = FakeRepo(status=status)
    with pytest.raises(ConflictError):
        await svc.download_url("medical_history", UPLOAD_ID)


@pytest.mark.parametrize("status", ["unverified", "verified"])
async def test_promoted_file_can_be_downloaded(monkeypatch, status):
    monkeypatch.setattr(s3, "presign_download", lambda key, **kw: f"signed:{key}")
    svc = FileService(session=None)
    svc.mhf = FakeRepo(status=status)
    assert (await svc.download_url("medical_history", UPLOAD_ID)).startswith("signed:regions/")
