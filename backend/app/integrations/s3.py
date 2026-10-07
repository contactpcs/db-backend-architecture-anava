"""File storage abstraction. Local mode writes to disk under
settings.local_file_storage_path; "s3" mode talks to the real buckets behind
the SAME functions, so no calling code changes between the two. This is the
only module allowed to touch S3 keys/paths directly — Section 4/13 of the
architecture doc ("frontend/backend never constructs S3 paths ad hoc").

Three buckets, named by what they are for (BACKEND_S3_PROMPT.md):

    records      patient records: EEG, medical history, and patient uploads
                 that passed the malware scan
    quarantine   where a patient upload lands first. GuardDuty scans it and
                 tags it; only promote_if_clean() ever reads from here
    compliance   signed consent PDFs and clinic licenses

Nothing here deletes or overwrites: every key carries a fresh UUIDv7, the
buckets are versioned, and the app's IAM role has no delete permission.
Deletes belong to the separate retention-purge role. Local mode keeps all
three "buckets" under one directory; the key prefixes never collide."""

import hashlib
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from botocore.exceptions import ClientError

from app.config import get_settings
from app.core.exceptions import ConflictError, ExternalServiceError, ValidationError

settings = get_settings()
_s3_client = None

Bucket = Literal["records", "quarantine", "compliance"]
RECORD_CATEGORIES = ("eeg", "medical_history", "patient_upload")

# Tag GuardDuty Malware Protection puts on every object it scans.
SCAN_TAG = "GuardDutyMalwareScanStatus"
SCAN_CLEAN = "NO_THREATS_FOUND"
# Not GuardDuty values — what promote_if_clean() reports when there is no tag
# to read yet, or no object at all.
NOT_SCANNED = "NOT_SCANNED"
NOT_UPLOADED = "NOT_UPLOADED"


def _local_root() -> Path:
    root = Path(settings.local_file_storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def local_path(key: str) -> Path:
    """Where a key lives on disk in local mode. Refuses a key that would
    resolve outside the storage directory ('../' in a URL path)."""
    root = _local_root().resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValidationError("Invalid file key", code="INVALID_FILE_KEY")
    return path


def _client():
    """Same credential-fallback order as core/cognito.py's _client(): a named
    profile (local dev against a real bucket before an IAM role exists) takes
    priority, otherwise fall back to explicit keys / boto3's default chain
    (IAM role attached to the ECS task, in real deployments)."""
    global _s3_client
    if _s3_client is None:
        import boto3
        from botocore.config import Config

        # Force the regional endpoint: boto3's default virtual-hosted URL
        # resolves to the global s3.amazonaws.com host for non-us-east-1
        # buckets, which S3 302/307-redirects to the regional host — and
        # since sigv4 signs the Host header, that redirect breaks presigned
        # URLs handed to clients. Pinning endpoint_url avoids the redirect.
        endpoint_url = f"https://s3.{settings.aws_region}.amazonaws.com"
        boto_config = Config(signature_version="s3v4")
        if settings.aws_profile:
            _s3_client = boto3.Session(profile_name=settings.aws_profile).client(
                "s3", region_name=settings.aws_region, endpoint_url=endpoint_url, config=boto_config
            )
        else:
            _s3_client = boto3.client(
                "s3",
                region_name=settings.aws_region,
                endpoint_url=endpoint_url,
                config=boto_config,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
    return _s3_client


def _bucket(bucket: Bucket) -> str:
    name = {
        "records": settings.s3_bucket_name,
        "quarantine": settings.s3_quarantine_bucket_name,
        "compliance": settings.s3_compliance_bucket_name,
    }[bucket]
    if not name:
        raise ExternalServiceError(f"The {bucket} bucket is not configured", code="S3_BUCKET_NOT_CONFIGURED")
    return name


def _sse(bucket: Bucket) -> dict:
    """Explicit SSE-KMS parameters for a server-side write. Empty when no key
    is configured, which leaves it to the bucket's default encryption."""
    arn = settings.s3_kms_key_arn_compliance if bucket == "compliance" else settings.s3_kms_key_arn_phi
    return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": arn} if arn else {}


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit Unix milliseconds, then random bits — 128 bits,
    and keys list in upload order. (uuid.uuid7 is in the stdlib from 3.14.)"""
    value = (time.time_ns() // 1_000_000) << 80 | secrets.randbits(80)
    value = value & ~(0xF << 76) | (0x7 << 76)  # version 7
    value = value & ~(0x3 << 62) | (0x2 << 62)  # RFC variant
    return uuid.UUID(int=value)


def _slug(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9-]+", "_", value or "").strip("_")


def _sanitize_filename(filename: str | None) -> str:
    """The client's file name is untrusted: drop any directory part, strip to
    ASCII alnum/dot/dash/underscore and cap the length. Unicode (emoji,
    non-Latin scripts) or spaces in the key survive upload but can fail sigv4
    verification when a client's URL-encoding of the path differs from what
    was signed — the original name is preserved separately in the DB's
    file_name column, this is purely for the S3 key."""
    filename = re.split(r"[\\/]", filename or "")[-1]
    stem, dot, ext = filename.rpartition(".")
    name = stem if dot else filename
    ext = f".{ext}" if dot else ""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:100] or "file"
    safe_ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:10]
    return f"{safe}.{safe_ext}" if safe_ext else safe


def build_key(
    kind: Literal["records", "consent", "license", "quarantine"],
    *,
    region_id: str | None = None,
    clinic_id: str | None = None,
    patient_id: str | None = None,
    category: str | None = None,
    filename: str | None = None,
    template: str | None = None,
    license_type: str | None = None,
    version: int | None = None,
    upload_id: str | None = None,
) -> str:
    """The four key shapes. Region first, tenant next, record type last:

    records     regions/{region}/clinics/{clinic}/patients/{patient}/{category}/{uuidv7}_{filename}
    consent     regions/{region}/clinics/{clinic}/consents/patients/{patient}/{template}_v{n}_{uuidv7}.pdf
    license     regions/{region}/clinics/{clinic}/licenses/{license_type}/v{n}_{uuidv7}_{filename}
    quarantine  incoming/{upload_id}   (no patient or clinic identifiers)

    records lives in the patient records bucket, consent and license in the
    compliance bucket. Every call returns a new key (fresh UUIDv7), so a
    correction, a renewal or a re-signature is always a new object."""

    def need(**parts) -> None:
        missing = [name for name, value in parts.items() if value in (None, "")]
        if missing:
            raise ValueError(f"build_key({kind!r}) needs {', '.join(missing)}")

    if kind == "quarantine":
        need(upload_id=upload_id)
        return f"incoming/{upload_id}"

    need(region_id=region_id, clinic_id=clinic_id)
    base = f"regions/{region_id}/clinics/{clinic_id}"
    if kind == "records":
        need(patient_id=patient_id, filename=filename)
        if category not in RECORD_CATEGORIES:
            raise ValueError(f"category must be one of {RECORD_CATEGORIES}, got {category!r}")
        return f"{base}/patients/{patient_id}/{category}/{uuid7()}_{_sanitize_filename(filename)}"
    if kind == "consent":
        need(patient_id=patient_id, template=_slug(template), version=version)
        return f"{base}/consents/patients/{patient_id}/{_slug(template)}_v{int(version or 0)}_{uuid7()}.pdf"
    if kind == "license":
        need(license_type=_slug(license_type), version=version, filename=filename)
        return f"{base}/licenses/{_slug(license_type)}/v{int(version or 0)}_{uuid7()}_{_sanitize_filename(filename)}"
    raise ValueError(f"Unknown key kind {kind!r}")


def presign_upload(key: str, *, content_type: str, bucket: Bucket = "records", expires_in: int = 300) -> dict:
    """Returns {"upload_url", "upload_fields"}.

    Real S3: a presigned POST, short-lived (5 min default) — the client sends
    upload_fields plus the file as multipart form data straight to upload_url.
    The policy S3 enforces caps the size (content-length-range, which a
    presigned PUT cannot do) and pins the Content-Type to the declared one,
    which must be on the allowed list.
    Local mode: upload_fields is None and upload_url is this backend's own
    upload endpoint — the client PUTs the bytes there instead."""
    if content_type not in settings.upload_allowed_content_types:
        raise ValidationError(
            f"Files of type {content_type!r} are not accepted. Allowed: {', '.join(settings.upload_allowed_content_types)}",
            code="CONTENT_TYPE_NOT_ALLOWED",
        )
    if settings.file_storage_mode != "s3":
        return {"upload_url": f"/api/v1/files/upload/{key}", "upload_fields": None}
    try:
        post = _client().generate_presigned_post(
            Bucket=_bucket(bucket),
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, settings.upload_max_bytes]],
            ExpiresIn=expires_in,
        )
    except ClientError as exc:
        raise ExternalServiceError(f"Could not generate upload URL: {exc}", code="S3_PRESIGN_FAILED") from exc
    return {"upload_url": post["url"], "upload_fields": post["fields"]}


def presign_download(key: str, *, filename: str, version_id: str | None = None, bucket: Bucket = "records", expires_in: int = 300) -> str:
    """Always served as an attachment, so a browser never renders an uploaded
    HTML or SVG file inside the portal session. version_id pins the exact
    version the database row refers to."""
    if settings.file_storage_mode != "s3":
        return f"/api/v1/files/download/{key}"
    params = {
        "Bucket": _bucket(bucket),
        "Key": key,
        "ResponseContentDisposition": f'attachment; filename="{_sanitize_filename(filename)}"',
    }
    if version_id:
        params["VersionId"] = version_id
    try:
        return _client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)
    except ClientError as exc:
        raise ExternalServiceError(f"Could not generate download URL: {exc}", code="S3_PRESIGN_FAILED") from exc


def save_bytes(key: str, content: bytes, *, bucket: Bucket = "records") -> tuple[int, str, str | None]:
    """Writes a NEW object and returns (size, sha256, S3 VersionId). Refuses
    to write over an existing key. A signed consent PDF also gets Object Lock
    retention when consent_object_lock_days is configured."""
    checksum = hashlib.sha256(content).hexdigest()
    if settings.file_storage_mode != "s3":
        path = local_path(key)
        if path.exists():
            raise ConflictError("A file already exists at this key", code="FILE_KEY_EXISTS")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return len(content), checksum, None
    extra = _sse(bucket)
    if bucket == "compliance" and "/consents/" in key and settings.consent_object_lock_days:
        extra["ObjectLockMode"] = settings.consent_object_lock_mode
        extra["ObjectLockRetainUntilDate"] = datetime.now(UTC) + timedelta(days=settings.consent_object_lock_days)
    try:
        put = _client().put_object(Bucket=_bucket(bucket), Key=key, Body=content, IfNoneMatch="*", **extra)
    except ClientError as exc:
        raise ExternalServiceError(f"Could not upload to S3: {exc}", code="S3_UPLOAD_FAILED") from exc
    return len(content), checksum, put.get("VersionId")


def read_bytes(key: str, *, bucket: Bucket = "records", version_id: str | None = None) -> bytes:
    if settings.file_storage_mode != "s3":
        return local_path(key).read_bytes()
    params = {"Bucket": _bucket(bucket), "Key": key}
    if version_id:
        params["VersionId"] = version_id
    try:
        return _client().get_object(**params)["Body"].read()
    except ClientError as exc:
        raise ExternalServiceError(f"Could not read from S3: {exc}", code="S3_READ_FAILED") from exc


def head(key: str, *, bucket: Bucket = "records") -> dict | None:
    """{"size", "version_id"} of the current object at key, or None if there
    is none."""
    if settings.file_storage_mode != "s3":
        path = local_path(key)
        return {"size": path.stat().st_size, "version_id": None} if path.exists() else None
    try:
        obj = _client().head_object(Bucket=_bucket(bucket), Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise ExternalServiceError(f"Could not check S3 object: {exc}", code="S3_HEAD_FAILED") from exc
    return {"size": obj["ContentLength"], "version_id": obj.get("VersionId")}


def promote_if_clean(upload_id: str, dest_key: str) -> dict:
    """The malware gate: copies incoming/{upload_id} from the quarantine
    bucket to dest_key in the patient records bucket, but ONLY when the
    object's own scan tag says NO_THREATS_FOUND. Whatever an event claimed is
    not trusted — the tag is read here, immediately before the copy.

    Returns {"scan": <status>} and, when the copy happened, also "size",
    "checksum" and "version_id" of the new object. scan is the tag value,
    NOT_SCANNED (no tag yet) or NOT_UPLOADED (no object). The quarantine
    object is left alone either way; the bucket's lifecycle rule removes it.

    Needs the promotion job's role, not the API's: the API role cannot read
    the quarantine bucket. Local mode has no scanner, so a file that arrived
    counts as clean."""
    src = build_key("quarantine", upload_id=upload_id)
    if settings.file_storage_mode != "s3":
        path = local_path(src)
        if not path.exists():
            return {"scan": NOT_UPLOADED}
        size, checksum, version_id = save_bytes(dest_key, path.read_bytes())
        return {"scan": SCAN_CLEAN, "size": size, "checksum": checksum, "version_id": version_id}

    client = _client()
    source: dict = {"Bucket": _bucket("quarantine"), "Key": src}
    try:
        tagging = client.get_object_tagging(**source)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return {"scan": NOT_UPLOADED}
        raise ExternalServiceError(f"Could not read the scan result: {exc}", code="S3_SCAN_STATUS_FAILED") from exc
    scan = next((tag["Value"] for tag in tagging.get("TagSet", []) if tag["Key"] == SCAN_TAG), NOT_SCANNED)
    if scan != SCAN_CLEAN:
        return {"scan": scan}

    # Copy the exact version the tag was read from. The presigned POST stays
    # valid for a few minutes, so a second upload could put a new, not yet
    # scanned version at the same key between the tag check and the copy.
    if tagging.get("VersionId"):
        source["VersionId"] = tagging["VersionId"]
    try:
        content = client.get_object(**source)["Body"].read()
        # TaggingDirective=REPLACE with no tags: the default (COPY) would carry
        # the scan tag over, which needs s3:PutObjectTagging on the records
        # bucket. The scan result is kept in the database row instead.
        copied = client.copy_object(
            CopySource=source, Bucket=_bucket("records"), Key=dest_key, TaggingDirective="REPLACE", **_sse("records")
        )
    except ClientError as exc:
        raise ExternalServiceError(f"Could not promote the upload: {exc}", code="S3_PROMOTE_FAILED") from exc
    return {
        "scan": scan,
        "size": len(content),
        "checksum": hashlib.sha256(content).hexdigest(),
        "version_id": copied.get("VersionId"),
    }
