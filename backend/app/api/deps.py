from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth_store import AuthStore, get_auth_store
from app.core.security import decode_access_token


AuthData = Annotated[AuthStore, Depends(get_auth_store)]
bearer = HTTPBearer(auto_error=False)


def get_current_user(
    store: AuthData,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已失效，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id = decode_access_token(credentials.credentials)
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise unauthorized from None
    user = store.get_user_by_id(user_id)
    if user is None or not user["is_active"]:
        raise unauthorized
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]
