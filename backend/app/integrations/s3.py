"""File storage abstraction. Local mode (default through Stage 12) writes to
disk under settings.local_file_storage_path; Stage 13 swaps this module's
internals for real boto3 S3 calls behind the SAME functions so no calling
code changes. This is the only module allowed to touch S3 keys/paths
directly — Section 4/13 of the architecture doc ("frontend/backend never
constructs S3 paths ad hoc")."""

import hashlib
import uuid
from pathlib import Path

from botocore.exceptions import ClientError

from app.config import get_settings
from app.core.exceptions import ExternalServiceError

settings = get_settings()
_s3_client = None


def _local_root() -> Path:
    root = Path(settings.local_file_storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _client():
    """Same credential-fallback order as core/cognito.py's _client(): a named
    profile (local dev against a real bucket before an IAM role exists) takes
    priority, otherwise fall back to explicit keys / boto3's default chain
    (IAM role attached to the ECS task, in real deployments)."""
    global _s3_client
    if _s3_client is None:
        import boto3
        from botocore.config import Config

        boto_config = Config(signature_version="s3v4")
        if settings.aws_profile:
            _s3_client = boto3.Session(profile_name=settings.aws_profile).client("s3", region_name=settings.aws_region, config=boto_config)
        else:
            _s3_client = boto3.client(
                "s3",
                region_name=settings.aws_region,
                config=boto_config,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
    return _s3_client


def build_key(*, clinic_id: str, patient_id: str, category: str, filename: str) -> str:
    """Enforces the S3 folder convention from Architecture Section 4/13:
    regions/{region}/clinics/{clinic}/patients/{patient}/{category}/{file}.
    Region is omitted locally (not resolved at this layer) — Stage 13 adds it
    back when wiring the real bucket path."""
    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    return f"clinics/{clinic_id}/patients/{patient_id}/{category}/{safe_name}"


def presign_upload(key: str, *, content_type: str, expires_in: int = 300) -> str:
    """Real S3 (file_storage_mode='s3'): returns a presigned PUT URL, short-lived
    (5 min default) — the client is expected to request this right before
    uploading, not cache it. Local mode: returns a path to this backend's own
    upload endpoint — the client PUTs bytes there instead."""
    if settings.file_storage_mode != "s3":
        return f"/api/v1/files/upload/{key}"
    try:
        return _client().generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )
    except ClientError as exc:
        raise ExternalServiceError(f"Could not generate upload URL: {exc}", code="S3_PRESIGN_FAILED") from exc


def presign_download(key: str, *, expires_in: int = 300) -> str:
    if settings.file_storage_mode != "s3":
        return f"/api/v1/files/download/{key}"
    try:
        return _client().generate_presigned_url("get_object", Params={"Bucket": settings.s3_bucket_name, "Key": key}, ExpiresIn=expires_in)
    except ClientError as exc:
        raise ExternalServiceError(f"Could not generate download URL: {exc}", code="S3_PRESIGN_FAILED") from exc


def save_bytes(key: str, content: bytes) -> tuple[int, str]:
    path = _local_root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    checksum = hashlib.sha256(content).hexdigest()
    return len(content), checksum


def read_bytes(key: str) -> bytes:
    return (_local_root() / key).read_bytes()


def delete(key: str) -> None:
    path = _local_root() / key
    if path.exists():
        path.unlink()


def exists(key: str) -> bool:
    return (_local_root() / key).exists()
