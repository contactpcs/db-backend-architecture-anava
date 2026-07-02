from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.core.security import create_local_token
from app.modules.auth.schemas import LocalLoginRequest, TokenResponse

router = APIRouter()
settings = get_settings()


@router.post("/local-login", response_model=TokenResponse)
async def local_login(body: LocalLoginRequest) -> TokenResponse:
    """Dev/test-only endpoint — issues a token for a seeded profile without
    needing a Cognito account. Disabled once auth_mode='cognito' (Stage 13);
    real clients authenticate directly against Cognito instead."""
    if settings.auth_mode != "local":
        raise HTTPException(status_code=404, detail="Not found")
    token = create_local_token(sub=body.cognito_sub)
    return TokenResponse(access_token=token)
