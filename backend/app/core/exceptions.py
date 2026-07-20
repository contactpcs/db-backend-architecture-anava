"""Exception hierarchy — see Anava_Backend_Architecture_v1.md Section 17.

Every module raises these from the service layer, never from repositories.
A single global handler (registered in main.py) maps them to the standard
error envelope from Section 8.
"""


class AnavaException(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, code: str | None = None, details: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or []


class ValidationError(AnavaException):
    status_code = 422
    code = "VALIDATION_ERROR"


class PermissionError_(AnavaException):
    """Named with trailing underscore to avoid shadowing the builtin PermissionError."""

    status_code = 403
    code = "PERMISSION_DENIED"


class AuthenticationError(AnavaException):
    """Missing/invalid credentials (no token, malformed Authorization header)
    — distinct from PermissionError_, which is a valid caller lacking the
    right role/scope."""

    status_code = 401
    code = "AUTHENTICATION_REQUIRED"


class NotFoundError(AnavaException):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AnavaException):
    status_code = 409
    code = "CONFLICT"


class BusinessRuleError(AnavaException):
    status_code = 400
    code = "BUSINESS_RULE_VIOLATION"


class ExternalServiceError(AnavaException):
    """Retryable — repository/integration call wrapper retries these before
    ever surfacing to the client (Section 10)."""

    status_code = 503
    code = "EXTERNAL_SERVICE_UNAVAILABLE"


class FatalError(AnavaException):
    status_code = 500
    code = "INTERNAL_ERROR"
