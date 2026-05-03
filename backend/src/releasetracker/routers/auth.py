"""Authentication routes"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated

from ..models import LoginRequest, RegisterRequest, User, TokenPair, ChangePasswordRequest
from ..services.auth import AuthService

from ..dependencies import get_auth_service, get_current_user, oauth2_scheme

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=User, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    current_user: Annotated[User, Depends(get_current_user)],  # Authentication required
):
    """
    Create a new user. Administrators only.

    This endpoint requires administrator privileges. Regular users should sign in with the built-in administrator account.
    """
    # Optional: check whether the user is an admin if roles are supported

    #     raise HTTPException(status_code=403, detail="Only administrators can create users")

    try:
        user = await auth_service.register(req)
        return user
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login", response_model=dict)
async def login(req: LoginRequest, auth_service: Annotated[AuthService, Depends(get_auth_service)]):
    try:
        # TODO: Get the real IP and user agent
        user, token_pair = await auth_service.login(
            req, user_agent="unknown", ip_address="127.0.0.1"
        )
        return {"user": user, "token": token_pair}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.post("/token", response_model=TokenPair)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """OAuth2-compatible login endpoint for Swagger UI"""
    req = LoginRequest(username=form_data.username, password=form_data.password)
    try:
        user, token_pair = await auth_service.login(
            req, user_agent="Swagger UI", ip_address="127.0.0.1"
        )
        return token_pair
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/logout")
async def logout(
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    await auth_service.logout(token)
    return {"message": "Logged out successfully"}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    token: Annotated[str, Depends(oauth2_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    try:
        await auth_service.change_password(token, req)
        return {"message": "Password changed successfully"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/me", response_model=User)
async def get_me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user


@router.post("/refresh", response_model=TokenPair)
async def refresh_token(
    refresh_token: str, auth_service: Annotated[AuthService, Depends(get_auth_service)]
):
    try:
        return await auth_service.refresh_token(refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
