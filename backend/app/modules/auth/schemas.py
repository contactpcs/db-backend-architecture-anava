from pydantic import BaseModel


class LocalLoginRequest(BaseModel):
    """Dev-only. Real login (Stage 13) goes through Cognito directly from the
    frontend — the backend never handles passwords, only validates the
    resulting Cognito JWT (see core/security.py)."""

    cognito_sub: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
