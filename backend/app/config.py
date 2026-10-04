from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "local"

    # Database — database_url is what the running app (requests AND the
    # in-process workers) connects as: anava_app, a scoped role that is NOT
    # a superuser and does NOT bypass RLS (verified 2026-09-28). RLS is a
    # real backstop; the app-layer checks in app/core/scoping.py remain the
    # first line. migration_database_url is the RDS master user — only for
    # alembic/DDL, seed scripts, partition maintenance and the retention
    # purge. The API container should not need it at all: run partition
    # maintenance as a separate scheduled task and set
    # PARTITION_MAINTENANCE_ENABLED=false on the API.
    # No hardcoded fallback on purpose — a deploy with this unset should
    # fail to boot, not silently connect to a nonexistent local Postgres.
    database_url: str
    migration_database_url: str | None = None
    # Per-process ceiling = pool + overflow (API) + 5 (shared worker pool) + 1
    # (relay LISTEN). The RDS instance has 79 slots (5 reserved) shared by
    # the deployed API AND every developer's local backend — keep this small
    # by default; raise it via env only on a bigger instance or behind RDS Proxy.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # RDS requires/expects SSL; local Docker Postgres doesn't have it configured.
    db_require_ssl: bool = False
    # AWS's RDS certs chain up to Amazon's own root CAs, which aren't always
    # in the OS/Python default trust store (verification fails as "self-
    # signed certificate in certificate chain" otherwise) — this file is
    # AWS's official public bundle: https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
    db_ssl_ca_bundle: str | None = "certs/rds-global-bundle.pem"

    # Partition maintenance runs inside the API process (app/main.py lifespan),
    # so no separate worker deployment is needed. Off switch exists for tests
    # and for the case where it's moved to a dedicated scheduled task instead.
    partition_maintenance_enabled: bool = True

    # Appointments — the payment seam.
    #
    # payment_required = False (today): booking writes status 'paid' directly.
    # No hold is taken, hold_expires_at stays NULL, and the sweeper has nothing
    # to sweep. This is what lets the whole flow run before Razorpay is wired,
    # and it satisfies every constraint 31 added — chk_appointments_hold reads
    # ('paid' = 'selected') = (NULL IS NOT NULL), i.e. false = false.
    #
    # payment_required = True (once payment lands): booking writes 'selected'
    # with hold_expires_at = now + appointment_hold_minutes, the sweeper starts
    # releasing abandoned holds, and the payment webhook calls mark_paid().
    # No other code changes — that is the point of routing both paths through
    # the same service methods now rather than bolting payment on later.
    appointment_payment_required: bool = True
    # How long an unpaid 'selected' slot is held before the sweeper releases it.
    # 15 minutes matches a typical gateway checkout session: long enough to
    # finish paying, short enough that abandoned holds do not starve a calendar.
    appointment_hold_minutes: int = 15
    # How often the hold sweeper wakes. Kept well under the hold window so a
    # released slot is available again promptly rather than a whole window late.
    appointment_hold_sweep_interval_seconds: int = 60
    appointment_hold_sweeper_enabled: bool = True

    # No-show sweeper — auto-marks an unattended appointment 'no_show' instead
    # of leaving it stuck at 'paid'/'checked_in' forever with nobody noticing.
    # Two independent grace windows (see workers/no_show_sweeper.py):
    #   paid, never checked in        -> no_show after appointment_no_show_paid_grace_hours
    #   checked_in, session never started/finished -> no_show after
    #                                     appointment_no_show_checked_in_grace_hours
    appointment_no_show_paid_grace_hours: float = 2.0
    appointment_no_show_checked_in_grace_hours: float = 6.0
    # Hours, not minutes — no need to wake as often as the hold sweeper, which
    # is racing a 15-minute window; this one is racing multi-hour windows.
    appointment_no_show_sweep_interval_seconds: int = 900
    appointment_no_show_sweeper_enabled: bool = True
    # Outbox relay (app/workers/event_relay.py): turns outbox events into
    # notifications rows + live SSE pushes. Off = nobody is ever notified.
    event_relay_enabled: bool = True

    # Self-registered patients (Documents/design_signup_auto_approval.md).
    # Off = every self-registration waits for a receptionist, as before; the
    # risk checks still run and their flags are stored either way.
    auto_approve_self_registration: bool = False
    signup_start_limit_per_ip_per_hour: int = 15
    signup_contact_limit_per_hour: int = 10
    signup_otp_limit_per_ip_per_hour: int = 10
    signup_ip_velocity_limit_per_day: int = 24
    signup_min_wizard_seconds: int = 30
    signup_max_age_years: int = 150
    # First entry is assumed for a number typed without a country code.
    signup_allowed_phone_country_codes: list[str] = ["+91"]
    # Key for the one-way hash of emails/phones in ops.signup_security_log.
    # Required outside local/test (validated below).
    signup_hash_secret: str | None = None
    # Behind the load balancer every connection comes from the balancer, so
    # the caller's address has to be read from X-Forwarded-For. Turn on ONLY
    # where the API is reachable through the balancer alone — anyone who can
    # reach it directly could otherwise send that header themselves.
    trust_forwarded_for: bool = False

    # Auth — local dev uses a fake JWT issuer shaped like Cognito's tokens.
    # In Stage 13 (real AWS cutover) these get replaced with the real Cognito
    # pool region/id/client-id and JWKS validation switches on automatically.
    auth_mode: str = "local"  # "local" | "cognito"
    # No hardcoded fallback — required (validated below) only when
    # auth_mode == "local", so a deploy that forgets to set it fails fast
    # instead of silently signing tokens with a public placeholder secret.
    local_jwt_secret: str | None = None
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

    # Interactive API docs (/docs, /redoc, /openapi.json). None = only when
    # environment == "local": production must not publish the full API map.
    # Deploys verify the build through GET /health/version instead.
    api_docs_enabled: bool | None = None

    # API audit recorder (perf/api-audit): one JSON line per request to
    # api_audit_log (core/middleware.py::ApiAuditMiddleware). Off = the
    # middleware is not even registered, nothing is written.
    api_audit: bool = False
    api_audit_log: str = r"D:\PCS\Documents\API_Audit\raw\traffic.jsonl"

    # Payments — Razorpay test-mode keys, set once available (Stage 10).
    # Empty in early development; payments module runs in stub mode until set.
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    # Separate secret Razorpay signs webhook deliveries with (set in the
    # Razorpay dashboard's webhook config) — NOT the same value as
    # razorpay_key_secret, which only signs API requests.
    razorpay_webhook_secret: str | None = None

    # Session cookie (app/core/session_cookie.py) — the long-lived Cognito
    # refresh token lives here, httpOnly, so page scripts can never read it.
    # secure=None means "on everywhere except environment=local" (plain-http
    # localhost). samesite must be "none" (which browsers only honour with
    # secure) when the web app and the API sit on different registrable
    # domains; "lax" is right when they share one (app.x.com / api.x.com).
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: str = "lax"
    auth_cookie_domain: str | None = None
    refresh_cookie_max_age_days: int = 30
    # One-time ticket that lets the browser's EventSource (which cannot send
    # an Authorization header) open the live-notification stream without the
    # access token ever appearing in a URL.
    stream_ticket_ttl_seconds: int = 30

    # CORS
    cors_allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://staging-app.anavaclinics.com",
    ]

    # Clinical staff (doctor/CA/receptionist) must log in with an official
    # org email — patients are exempt, always use their own. Enforced at
    # staff profile creation time (see staff/service.py).
    staff_allowed_email_domains: list[str] = ["anavaclinics.com", "manahealthsciences.com", "pcsdatai.com"]

    @model_validator(mode="after")
    def _require_local_jwt_secret_in_local_mode(self) -> "Settings":
        if self.auth_mode == "local" and not self.local_jwt_secret:
            raise ValueError("local_jwt_secret must be set when auth_mode='local'")
        return self

    @model_validator(mode="after")
    def _require_signup_hash_secret_outside_local(self) -> "Settings":
        if self.environment not in ("local", "test") and not self.signup_hash_secret:
            raise ValueError("signup_hash_secret must be set outside local/test environments")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # pydantic-settings fills required fields from env; mypy can't see that


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
