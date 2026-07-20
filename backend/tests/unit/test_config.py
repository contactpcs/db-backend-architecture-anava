"""Regression test for the config.py fail-fast fix — previously
database_url/local_jwt_secret silently fell back to hardcoded dev
defaults if unset, so a misconfigured deploy would boot against fake
creds without error.

_env_file=None + monkeypatch.delenv keep these isolated from the repo's
real .env / OS environment (which does set both vars for local dev)."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch):
    for key in ("DATABASE_URL", "AUTH_MODE", "LOCAL_JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)


def _base_kwargs(**overrides):
    kwargs = dict(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db", auth_mode="local", local_jwt_secret="s3cr3t", _env_file=None
    )
    kwargs.update(overrides)
    return kwargs


def test_settings_requires_database_url():
    with pytest.raises(PydanticValidationError):
        Settings(**{k: v for k, v in _base_kwargs().items() if k != "database_url"})


def test_settings_requires_local_jwt_secret_in_local_mode():
    with pytest.raises(PydanticValidationError):
        Settings(**_base_kwargs(local_jwt_secret=None))


def test_settings_allows_missing_local_jwt_secret_in_cognito_mode():
    Settings(**_base_kwargs(auth_mode="cognito", local_jwt_secret=None))  # no raise


def test_settings_ok_with_all_required_fields_set():
    Settings(**_base_kwargs())  # no raise
