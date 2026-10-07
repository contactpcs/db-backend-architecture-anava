"""integrations/s3.py: the four key shapes, file name sanitizing, the limits
written into a presigned upload, forced-attachment downloads, and the malware
gate (promote_if_clean) — an object is copied out of the quarantine bucket
only when its own tag says NO_THREATS_FOUND.

No AWS: S3 mode is switched on with fake bucket names and a fake client."""

import io
import re

import pytest
from botocore.exceptions import ClientError

from app.core.exceptions import ConflictError, ValidationError
from app.integrations import s3

UUID7 = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
BASE = "regions/reg-1/clinics/clin-1"


def _records_key(filename: str = "scan.pdf", category: str = "eeg") -> str:
    return s3.build_key("records", region_id="reg-1", clinic_id="clin-1", patient_id="pat-1", category=category, filename=filename)


# ── build_key ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("category", ["eeg", "medical_history", "patient_upload"])
def test_records_key_shape(category):
    assert re.fullmatch(f"{BASE}/patients/pat-1/{category}/{UUID7}_scan.pdf", _records_key(category=category))


def test_records_key_rejects_unknown_category():
    with pytest.raises(ValueError):
        _records_key(category="invoices")


def test_consent_key_shape():
    key = s3.build_key("consent", region_id="reg-1", clinic_id="clin-1", patient_id="pat-1", template="tdcs consent", version=3)
    assert re.fullmatch(f"{BASE}/consents/patients/pat-1/tdcs_consent_v3_{UUID7}\\.pdf", key)


def test_license_key_shape_has_no_patient_segment():
    key = s3.build_key(
        "license", region_id="reg-1", clinic_id="clin-1", license_type="clinical_establishment", version=2, filename="lic.pdf"
    )
    assert re.fullmatch(f"{BASE}/licenses/clinical_establishment/v2_{UUID7}_lic\\.pdf", key)
    assert "patients" not in key


def test_quarantine_key_carries_no_patient_or_clinic():
    assert s3.build_key("quarantine", upload_id="abc") == "incoming/abc"


def test_build_key_refuses_a_missing_part():
    with pytest.raises(ValueError, match="region_id"):
        s3.build_key("records", clinic_id="clin-1", patient_id="pat-1", category="eeg", filename="a.pdf")
    with pytest.raises(ValueError, match="version"):
        s3.build_key("consent", region_id="reg-1", clinic_id="clin-1", patient_id="pat-1", template="t")


def test_every_call_returns_a_new_key():
    assert _records_key() != _records_key()


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\scan report (1).pdf", "scan_report_1.pdf"),
        ("r\u00e9sum\u00e9 \U0001f9e0.PDF", "r_sum.PDF"),
        ("..", "file"),
        ("", "file"),
        ("report.p/d\\f", "f"),
    ],
)
def test_filename_is_sanitized(given, expected):
    assert s3._sanitize_filename(given) == expected


def test_filename_length_is_capped():
    name = s3._sanitize_filename("a" * 500 + "." + "b" * 50)
    assert name == "a" * 100 + "." + "b" * 10
    assert "/" not in _records_key(filename="x/" * 200 + "end.pdf").rsplit("/", 1)[1]


def test_uuid7_is_version_7_and_time_ordered():
    first, second = s3.uuid7(), s3.uuid7()
    assert first.version == 7 and first.variant == "specified in RFC 4122"
    assert first.int >> 80 <= second.int >> 80  # leading 48 bits are the clock


# ── S3 mode, fake client ─────────────────────────────────────────────────


class FakeS3:
    def __init__(self, scan=None, version="q-v1", missing=False, missing_code="NoSuchKey"):
        self.scan, self.version, self.missing, self.missing_code = scan, version, missing, missing_code
        self.calls: list[tuple[str, dict]] = []

    def get_object_tagging(self, **kw):
        self.calls.append(("get_object_tagging", kw))
        if self.missing:
            raise ClientError({"Error": {"Code": self.missing_code}}, "GetObjectTagging")
        tags = [{"Key": "unrelated", "Value": "x"}]
        if self.scan:
            tags.append({"Key": s3.SCAN_TAG, "Value": self.scan})
        return {"TagSet": tags, "VersionId": self.version}

    def get_object(self, **kw):
        self.calls.append(("get_object", kw))
        return {"Body": io.BytesIO(b"hello")}

    def copy_object(self, **kw):
        self.calls.append(("copy_object", kw))
        return {"VersionId": "r-v9"}

    def generate_presigned_post(self, **kw):
        self.calls.append(("generate_presigned_post", kw))
        return {"url": "https://bucket.example", "fields": dict(kw["Fields"], key=kw["Key"])}

    def generate_presigned_url(self, op, **kw):
        self.calls.append((op, kw))
        return "https://signed.example"

    def names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def s3_mode(monkeypatch):
    def use(client: FakeS3) -> FakeS3:
        for name, value in {
            "file_storage_mode": "s3",
            "s3_bucket_name": "records-bkt",
            "s3_quarantine_bucket_name": "quarantine-bkt",
            "s3_kms_key_arn_phi": "arn:aws:kms:key-a",
            "s3_promoter_role_arn": None,
            "upload_max_bytes": 1000,
            "upload_allowed_content_types": ["application/pdf"],
        }.items():
            monkeypatch.setattr(s3.settings, name, value)
        monkeypatch.setattr(s3, "_client", lambda: client)
        return client

    return use


@pytest.mark.parametrize("scan", ["THREATS_FOUND", "UNSUPPORTED", "ACCESS_DENIED", "FAILED", "SOMETHING_NEW"])
def test_anything_but_no_threats_found_is_never_copied(s3_mode, scan):
    client = s3_mode(FakeS3(scan=scan))
    assert s3.promote_if_clean("u1", "dest/key") == {"scan": scan}
    assert client.names() == ["get_object_tagging"]


def test_untagged_object_is_not_copied(s3_mode):
    client = s3_mode(FakeS3(scan=None))
    assert s3.promote_if_clean("u1", "dest/key") == {"scan": s3.NOT_SCANNED}
    assert client.names() == ["get_object_tagging"]


def test_missing_object_is_reported_not_raised(s3_mode):
    s3_mode(FakeS3(missing=True))
    assert s3.promote_if_clean("u1", "dest/key") == {"scan": s3.NOT_UPLOADED}


def test_delete_marker_counts_as_not_uploaded(s3_mode):
    s3_mode(FakeS3(missing=True, missing_code="MethodNotAllowed"))
    assert s3.promote_if_clean("u1", "dest/key") == {"scan": s3.NOT_UPLOADED}


def test_promotion_uses_the_assumed_role_and_reuses_its_credentials(s3_mode, monkeypatch):
    from datetime import UTC, datetime, timedelta

    s3_mode(FakeS3())
    monkeypatch.setattr(s3.settings, "s3_promoter_role_arn", "arn:aws:iam::1:role/anava-upload-promoter")
    monkeypatch.setattr(s3, "_promoter_client_cache", None)
    assumed = []

    class FakeSts:
        def assume_role(self, **kw):
            assumed.append(kw)
            expiry = datetime.now(UTC) + timedelta(hours=1)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c", "Expiration": expiry}}

    class FakeSession:
        def client(self, name, **kw):
            return FakeSts()

    promoter = FakeS3(scan="NO_THREATS_FOUND")
    monkeypatch.setattr(s3, "_session", lambda: FakeSession())
    monkeypatch.setattr(s3, "_regional_client", lambda session: promoter)
    assert s3.promote_if_clean("u1", "dest/key")["scan"] == "NO_THREATS_FOUND"
    s3.promote_if_clean("u2", "dest/key2")
    assert len(assumed) == 1 and assumed[0]["RoleArn"].endswith("anava-upload-promoter")
    assert "copy_object" in promoter.names()


def test_clean_object_is_copied_from_the_scanned_version(s3_mode):
    client = s3_mode(FakeS3(scan="NO_THREATS_FOUND"))
    result = s3.promote_if_clean("u1", "dest/key")
    assert result["scan"] == "NO_THREATS_FOUND"
    assert result["size"] == 5 and result["version_id"] == "r-v9" and len(result["checksum"]) == 64
    copy = dict(client.calls)["copy_object"]
    assert copy["CopySource"] == {"Bucket": "quarantine-bkt", "Key": "incoming/u1", "VersionId": "q-v1"}
    assert (copy["Bucket"], copy["Key"]) == ("records-bkt", "dest/key")
    assert copy["SSEKMSKeyId"] == "arn:aws:kms:key-a"


def test_presigned_upload_is_a_post_with_size_and_type_limits(s3_mode):
    client = s3_mode(FakeS3())
    out = s3.presign_upload("incoming/u1", content_type="application/pdf", bucket="quarantine")
    assert out["upload_url"] == "https://bucket.example"
    assert out["upload_fields"]["Content-Type"] == "application/pdf"
    post = dict(client.calls)["generate_presigned_post"]
    assert post["Bucket"] == "quarantine-bkt"
    assert ["content-length-range", 1, 1000] in post["Conditions"]
    assert {"Content-Type": "application/pdf"} in post["Conditions"]


def test_presigned_upload_refuses_a_type_off_the_list(s3_mode):
    client = s3_mode(FakeS3())
    with pytest.raises(ValidationError):
        s3.presign_upload("k", content_type="text/html")
    assert client.calls == []


def test_download_is_an_attachment_pinned_to_the_version(s3_mode):
    client = s3_mode(FakeS3())
    s3.presign_download("k", filename='evil".html', version_id="v7")
    params = dict(client.calls)["get_object"]["Params"]
    assert params["ResponseContentDisposition"] == 'attachment; filename="evil.html"'
    assert params["VersionId"] == "v7"


def test_module_cannot_delete():
    assert not hasattr(s3, "delete")


# ── local mode ───────────────────────────────────────────────────────────


@pytest.fixture
def local_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(s3.settings, "file_storage_mode", "local")
    monkeypatch.setattr(s3.settings, "local_file_storage_path", str(tmp_path))


def test_local_save_never_overwrites(local_mode):
    s3.save_bytes("a/b.pdf", b"one")
    with pytest.raises(ConflictError):
        s3.save_bytes("a/b.pdf", b"two")
    assert s3.read_bytes("a/b.pdf") == b"one"


def test_local_key_cannot_escape_the_storage_directory(local_mode):
    with pytest.raises(ValidationError):
        s3.local_path("../outside.txt")


def test_local_promotion_copies_an_arrived_file(local_mode):
    assert s3.promote_if_clean("u1", "dest/x.pdf") == {"scan": s3.NOT_UPLOADED}
    s3.save_bytes("incoming/u1", b"data")
    assert s3.promote_if_clean("u1", "dest/x.pdf")["scan"] == s3.SCAN_CLEAN
    assert s3.head("dest/x.pdf") == {"size": 4, "version_id": None}
