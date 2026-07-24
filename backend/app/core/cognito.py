"""Cognito user provisioning + auth (Stage 13).

Only ever called when settings.auth_mode == "cognito" — local dev keeps
using the 'pending-<uuid>' placeholder in profiles.cognito_sub and never
imports boto3 at all.

Two different provisioning shapes:

- Staff (doctor/CA/receptionist/admin): AdminCreateUser with no permanent
  password — Cognito auto-generates a temp one and emails it. First login
  hits Cognito's native NEW_PASSWORD_REQUIRED challenge (no custom code
  needed for that part — it's InitiateAuth's own response shape).
- Patients: multi-step signup wizard — sign_up_patient() creates the user
  with a throwaway internal password just so Cognito has something to
  create the account with, and sends the OTP to whichever channel (email or
  phone) the patient chose to sign up with. confirm_sign_up() verifies that
  OTP. set_patient_password() then overwrites the throwaway password with
  the real one the patient typed after verifying. The user pool has BOTH
  email and phone_number configured as alias attributes, so once a channel
  is verified it works as a login username regardless of which one was used
  at signup — get_attribute_verification_code()/verify_attribute() handle
  verifying the *other* channel post-signup, required before it becomes a
  valid login alias.
"""

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.core.exceptions import BusinessRuleError, PermissionError_

settings = get_settings()


@lru_cache
def _client():
    if settings.aws_profile:
        # SSO / any other named CLI profile — reads whatever `aws sso login
        # --profile <name>` (or `aws configure`) already cached on disk, no
        # keys needed here at all.
        return boto3.Session(profile_name=settings.aws_profile).client("cognito-idp", region_name=settings.cognito_region)
    return boto3.client(
        "cognito-idp",
        region_name=settings.cognito_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


def _secret_hash(username: str) -> str:
    """Required on every call when the app client has a secret (ours does —
    it's only ever called server-side, never from a browser)."""
    assert settings.cognito_app_client_id is not None
    assert settings.cognito_app_client_secret is not None
    msg = username + settings.cognito_app_client_id
    digest = hmac.new(settings.cognito_app_client_secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _require_cognito_mode() -> None:
    if settings.auth_mode != "cognito":
        raise BusinessRuleError("Cognito is not configured for this environment (auth_mode != 'cognito')", code="COGNITO_NOT_CONFIGURED")


# ─── Staff provisioning (unchanged — AdminCreateUser, temp password emailed) ──


def provision_staff_user(*, email: str, first_name: str, last_name: str, phone: str | None) -> str:
    """Cognito auto-generates + emails the temp password — nothing to pass in."""
    _require_cognito_mode()
    attrs = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
        {"Name": "given_name", "Value": first_name},
        {"Name": "family_name", "Value": last_name},
    ]
    if phone:
        attrs.append({"Name": "phone_number", "Value": phone})
        attrs.append({"Name": "phone_number_verified", "Value": "true"})
    try:
        resp = _client().admin_create_user(
            UserPoolId=settings.cognito_user_pool_id,
            Username=email,
            UserAttributes=attrs,
            DesiredDeliveryMediums=["EMAIL"],
        )
        return next(a["Value"] for a in resp["User"]["Attributes"] if a["Name"] == "sub")
    except ClientError as exc:
        raise BusinessRuleError(f"Could not create Cognito user: {exc}", code="COGNITO_PROVISIONING_FAILED") from exc


# ─── Patient signup wizard (SignUp/ConfirmSignUp — real OTP delivery) ─────────


def sign_up_patient(*, username: str, first_name: str, last_name: str, dob: str | None, gender: str | None) -> None:
    """username is the patient's chosen signup identifier — an email address
    or an E.164 phone number ("+91XXXXXXXXXX"), whichever field they filled
    in. Cognito auto-sends the OTP to that same channel; nothing further to
    do here — confirm_sign_up() is the next step once they enter the code.
    The password here is a throwaway the patient never sees — set_patient_
    password() overwrites it once they've verified and chosen their real one."""
    _require_cognito_mode()
    attrs = [{"Name": "given_name", "Value": first_name}, {"Name": "family_name", "Value": last_name}]
    attrs.append({"Name": "email" if "@" in username else "phone_number", "Value": username})
    if dob:
        attrs.append({"Name": "birthdate", "Value": dob})
    if gender:
        attrs.append({"Name": "gender", "Value": gender})
    # token_urlsafe's alphabet has no symbol char this pool's password
    # policy accepts — appended suffix guarantees upper/lower/digit/symbol.
    throwaway_password = secrets.token_urlsafe(24) + "aA1!"
    try:
        _client().sign_up(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(username),
            Username=username,
            Password=throwaway_password,
            UserAttributes=attrs,
        )
    except _client().exceptions.UsernameExistsException as exc:
        # Cognito raises this even for an abandoned signup (OTP never
        # entered, still UNCONFIRMED) — not a real conflict, so resend the
        # code and let the wizard continue instead of blocking the patient
        # from ever finishing. Only a genuinely CONFIRMED account is a
        # real "already exists".
        try:
            status = _client().admin_get_user(UserPoolId=settings.cognito_user_pool_id, Username=username)["UserStatus"]
        except ClientError:
            raise BusinessRuleError("An account with this email/phone already exists", code="ACCOUNT_ALREADY_EXISTS") from exc
        if status != "UNCONFIRMED":
            raise BusinessRuleError("An account with this email/phone already exists", code="ACCOUNT_ALREADY_EXISTS") from exc
        resend_confirmation_code(username)
    except ClientError as exc:
        raise BusinessRuleError(f"Could not start signup: {exc}", code="COGNITO_SIGNUP_FAILED") from exc


def resend_confirmation_code(username: str) -> None:
    _require_cognito_mode()
    try:
        _client().resend_confirmation_code(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(username),
            Username=username,
        )
    except ClientError as exc:
        raise BusinessRuleError(f"Could not resend code: {exc}", code="COGNITO_RESEND_FAILED") from exc


def confirm_sign_up(*, username: str, code: str) -> None:
    _require_cognito_mode()
    try:
        _client().confirm_sign_up(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(username),
            Username=username,
            ConfirmationCode=code,
        )
    except _client().exceptions.CodeMismatchException as exc:
        raise PermissionError_("Incorrect verification code", code="INVALID_OTP") from exc
    except _client().exceptions.ExpiredCodeException as exc:
        raise PermissionError_("Verification code expired — request a new one", code="OTP_EXPIRED") from exc
    except ClientError as exc:
        raise BusinessRuleError(f"Could not verify code: {exc}", code="COGNITO_CONFIRM_FAILED") from exc


def set_patient_password(*, username: str, password: str) -> str:
    """Overwrites the throwaway signup password with the patient's real
    chosen one, permanent immediately. Returns the user's real sub (fetched
    now since sign_up_patient's own response is never persisted anywhere —
    this whole wizard is stateless server-side, the frontend just carries
    the form fields across steps)."""
    _require_cognito_mode()
    try:
        _client().admin_set_user_password(
            UserPoolId=settings.cognito_user_pool_id,
            Username=username,
            Password=password,
            Permanent=True,
        )
        resp = _client().admin_get_user(UserPoolId=settings.cognito_user_pool_id, Username=username)
        return next(a["Value"] for a in resp["UserAttributes"] if a["Name"] == "sub")
    except ClientError as exc:
        raise BusinessRuleError(f"Could not set password: {exc}", code="COGNITO_SET_PASSWORD_FAILED") from exc


def add_and_verify_channel_start(*, access_token: str, attribute: str, value: str) -> None:
    """Adds the OTHER channel post-signup (attribute is 'email' or
    'phone_number') and triggers its verification code — e.g. a
    mobile-signup patient's Cognito user has no email attribute at all yet,
    so GetUserAttributeVerificationCode would fail (nothing to verify);
    UpdateUserAttributes sets the value AND auto-sends the code in one call
    (the pool has both as auto-verified attributes). Needs the caller's own
    Cognito access token, not just their sub, since this acts on the
    currently-authenticated user. Also works to resend a code for a value
    already set — re-setting the same value re-triggers delivery."""
    _require_cognito_mode()
    try:
        _client().update_user_attributes(
            AccessToken=access_token,
            UserAttributes=[{"Name": attribute, "Value": value}],
        )
    except ClientError as exc:
        raise BusinessRuleError(f"Could not send verification code: {exc}", code="COGNITO_VERIFY_CODE_FAILED") from exc


def verify_attribute(*, access_token: str, attribute: str, code: str) -> None:
    _require_cognito_mode()
    try:
        _client().verify_user_attribute(AccessToken=access_token, AttributeName=attribute, Code=code)
    except _client().exceptions.CodeMismatchException as exc:
        raise PermissionError_("Incorrect verification code", code="INVALID_OTP") from exc
    except _client().exceptions.ExpiredCodeException as exc:
        raise PermissionError_("Verification code expired — request a new one", code="OTP_EXPIRED") from exc
    except ClientError as exc:
        raise BusinessRuleError(f"Could not verify attribute: {exc}", code="COGNITO_VERIFY_ATTRIBUTE_FAILED") from exc


# ─── Login (works for staff and patients alike, email or phone alias) ────────


def initiate_auth(*, username: str, password: str) -> dict:
    """USER_PASSWORD_AUTH — username is email or phone, either works once
    that channel is a verified alias. Returns Cognito's AuthenticationResult
    (AccessToken/IdToken/RefreshToken/ExpiresIn). Raises PermissionError_ on
    bad credentials so the router doesn't need to know Cognito's exception
    shape; a NEW_PASSWORD_REQUIRED challenge (first staff login after
    AdminCreateUser) surfaces as a distinct code instead of a plain 401,
    since the frontend needs to show a different screen for it."""
    _require_cognito_mode()
    try:
        resp = _client().initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=settings.cognito_app_client_id,
            AuthParameters={"USERNAME": username, "PASSWORD": password, "SECRET_HASH": _secret_hash(username)},
        )
    except _client().exceptions.NotAuthorizedException as exc:
        raise PermissionError_("Incorrect email/phone or password", code="INVALID_CREDENTIALS") from exc
    except _client().exceptions.UserNotFoundException as exc:
        raise PermissionError_("Incorrect email/phone or password", code="INVALID_CREDENTIALS") from exc
    except ClientError as exc:
        raise PermissionError_(f"Login failed: {exc}", code="COGNITO_LOGIN_FAILED") from exc

    if "ChallengeName" in resp:
        raise BusinessRuleError(
            f"Password change required ({resp['ChallengeName']})",
            code="NEW_PASSWORD_REQUIRED",
            details=[{"session": resp["Session"]}],
        )
    return resp["AuthenticationResult"]


def respond_new_password(*, username: str, new_password: str, session: str) -> dict:
    """Completes the NEW_PASSWORD_REQUIRED challenge initiate_auth() raises
    on a staff account's first login (AdminCreateUser's temp password).
    Returns the same AuthenticationResult shape as initiate_auth()."""
    _require_cognito_mode()
    try:
        resp = _client().respond_to_auth_challenge(
            ClientId=settings.cognito_app_client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=session,
            ChallengeResponses={"USERNAME": username, "NEW_PASSWORD": new_password, "SECRET_HASH": _secret_hash(username)},
        )
    except _client().exceptions.InvalidPasswordException as exc:
        raise BusinessRuleError("Password does not meet requirements", code="INVALID_PASSWORD") from exc
    except _client().exceptions.NotAuthorizedException as exc:
        raise PermissionError_("Session expired — please log in again", code="CHALLENGE_SESSION_EXPIRED") from exc
    except ClientError as exc:
        raise BusinessRuleError(f"Could not set new password: {exc}", code="COGNITO_CHALLENGE_FAILED") from exc
    return resp["AuthenticationResult"]
