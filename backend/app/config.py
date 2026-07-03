from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "local"

    # Database
    database_url: str = "postgresql+asyncpg://anava:anava_dev_password@localhost:5433/anava_dev"
    db_pool_size: int = 10
    db_max_overflow: int = 5

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

    # File storage — local dev writes to disk behind the same interface
    # integrations/s3.py exposes; Stage 13 swaps this for a real S3 bucket.
    file_storage_mode: str = "local"  # "local" | "s3"
    local_file_storage_path: str = "./.local_storage"
    s3_bucket_name: str | None = None

    # Queue — ElasticMQ speaks the real SQS protocol locally, so this is
    # just an endpoint override; boto3 SQS code never changes at cutover.
    sqs_endpoint_url: str | None = "http://localhost:9324"
    aws_region: str = "ap-south-1"

    # Payments — Razorpay test-mode keys, set once available (Stage 10).
    # Empty in early development; payments module runs in stub mode until set.
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None

    # CORS
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Clinical staff (doctor/CA/receptionist) must log in with an official
    # org email — patients are exempt, always use their own. Enforced at
    # staff profile creation time (see staff/service.py).
    staff_allowed_email_domains: list[str] = ["anavaclinic.com", "anavaclinics.com", "manahealthsciences.com"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
