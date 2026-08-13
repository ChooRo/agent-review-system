from fastapi import APIRouter, HTTPException, status

from app.api.deps import AuthData, CurrentUser
from app.core.security import create_access_token, verify_password
from app.schemas.auth import LoginRequest, RoleOut, TokenOut, UserOut


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, store: AuthData) -> TokenOut:
    user = store.get_user_by_username(payload.username)
    if user is None or not user["is_active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return TokenOut(access_token=create_access_token(user["id"]), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> dict:
    return user


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: CurrentUser, store: AuthData) -> list[dict[str, str]]:
    return store.list_roles()


@router.get("/users", response_model=list[UserOut])
def list_users(_: CurrentUser, store: AuthData) -> list[dict]:
    return store.list_active_users()
