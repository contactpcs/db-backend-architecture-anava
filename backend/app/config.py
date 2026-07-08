from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "local"

    # Database — database_url is what the running app connects as (the
    # scoped anava_app role in real environments, not the master user, so
    # RLS policies actually apply). migration_database_url is what alembic
    # connects as instead — needs DDL privileges (CREATE/ALTER/DROP), so it's
    # the RDS master user in real environments. Defaults to database_url
    # (unset locally — Docker dev has one role for everything, no split).
    database_url: str = "postgresql+asyncpg://anava:anava_dev_password@localhost:5433/anava_dev"
    migration_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 5
    # RDS requires/expects SSL; local Docker Postgres doesn't have it configured.
    db_require_ssl: bool = False
    # AWS's RDS certs chain up to Amazon's own root CAs, which aren't always
    # in the OS/Python default trust store (verification fails as "self-
    # signed certificate in certificate chain" otherwise) — this file is
    # AWS's official public bundle: https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
    db_ssl_ca_bundle: str | None = "certs/rds-global-bundle.pem"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth — local dev uses a fake JWT issuer shaped like Cognito's tokens.
    # In Stage 13 (real AWS cutover) these get replaced with the real Cognito
    # pool region/id/client-id and JWKS validation switches on automatically.
    auth_mode: str = "local"  # "local" | "cognito"
    local_jwt_secret: str = "dev-only-insecure-secret-do-not-use-in-prod"
    cognito_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None
    cognito_app_client_secret: str | None = None

    # File storage — local dev writes to disk behind the same interface
    # integrations/s3.py exposes; Stage 13 swaps this for a real S3 bucket.
    file_storage_mode: str = "local"  # "local" | "s3"
    local_file_storage_path: str = "./.local_storage"
    s3_bucket_name: str | None = None

    # Queue — ElasticMQ speaks the real SQS protocol locally, so this is
    # just an endpoint override; boto3 SQS code never changes at cutover.
    sqs_endpoint_url: str | None = "http://localhost:9324"
    aws_region: str = "ap-south-1"
    # Real AWS IAM credentials — needed for Cognito's Admin* calls
    # (AdminCreateUser/AdminSetUserPassword/AdminGetUser), which require IAM
    # auth, unlike InitiateAuth (app-client-secret only, no IAM). Left unset
    # for real deployments (EC2/ECS/Lambda IAM role covers it there instead
    # — boto3 falls back to its default credential chain when these are
    # None), set explicitly here for local dev / anywhere without an
    # attached role.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    # SSO (IAM Identity Center) alternative to the two above — set this to
    # an AWS CLI profile name you've already run `aws sso login --profile
    # <name>` against instead of pasting long-lived keys here. Takes
    # priority over aws_access_key_id/secret when both are somehow set.
    aws_profile: str | None = None

    # Payments — Razorpay test-mode keys, set once available (Stage 10).
    # Empty in early development; payments module runs in stub mode until set.
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None

    # CORS
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Clinical staff (doctor/CA/receptionist) must log in with an official
    # org email — patients are exempt, always use their own. Enforced at
    # staff profile creation time (see staff/service.py).
    staff_allowed_email_domains: list[str] = ["anavaclinics.com", "manahealthsciences.com", "pcsdatai.com"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_ssl_context():
    """Shared by core/db.py and alembic/env.py — both need the exact same SSL
    setup since they connect to the same kind of endpoint (RDS). Verifies
    against AWS's own CA bundle when configured, since RDS's cert chain
    isn't in the plain OS/Python default trust store (fails as "self-signed
    certificate in certificate chain" otherwise, not because the cert is
    actually invalid). Falls back to create_default_context() with no cafile
    if the bundle isn't present, which still encrypts the connection even
    though hostname/CA verification may then fail against those roots."""
    settings = get_settings()
    if not settings.db_require_ssl:
        return None
    import ssl
    from pathlib import Path

    cafile = None
    if settings.db_ssl_ca_bundle:
        candidate = Path(__file__).resolve().parent.parent / settings.db_ssl_ca_bundle
        if candidate.is_file():
            cafile = str(candidate)
    return ssl.create_default_context(cafile=cafile)
