"""File storage abstraction. Local mode (default through Stage 12) writes to
disk under settings.local_file_storage_path; Stage 13 swaps this module's
internals for real boto3 S3 calls behind the SAME functions so no calling
code changes. This is the only module allowed to touch S3 keys/paths
directly — Section 4/13 of the architecture doc ("frontend/backend never
constructs S3 paths ad hoc")."""

import hashlib
import uuid
from pathlib import Path

from app.config import get_settings

settings = get_settings()


def _local_root() -> Path:
    root = Path(settings.local_file_storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_key(*, clinic_id: str, patient_id: str, category: str, filename: str) -> str:
    """Enforces the S3 folder convention from Architecture Section 4/13:
    regions/{region}/clinics/{clinic}/patients/{patient}/{category}/{file}.
    Region is omitted locally (not resolved at this layer) — Stage 13 adds it
    back when wiring the real bucket path."""
    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    return f"clinics/{clinic_id}/patients/{patient_id}/{category}/{safe_name}"


def presign_upload(key: str) -> str:
    """Real S3: returns a presigned PUT URL. Local mode: returns a path to
    this backend's own upload endpoint — the client PUTs bytes there instead."""
    return f"/api/v1/files/upload/{key}"


def presign_download(key: str) -> str:
    return f"/api/v1/files/download/{key}"


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
