from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from config import settings
from deps import DbSession, get_session
from models import Session, User
from schemas import (
    LoginRequest,
    RegisterRequest,
    SaltRequest,
    SaltResponse,
    SessionOut,
    UserOut,
)
from security import (
    derive_pseudo_salt,
    hash_client_hash,
    new_session_token,
    verify_client_hash,
)

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_USERNAME_LENGTH = 3


def _normalize(username: str) -> str:
    return username.strip().lower()


def _error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(http_status, detail={"code": code, "message": message})


async def _issue_session(db: DbSession, user: User) -> SessionOut:
    session = Session(
        token=new_session_token(),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    await db.commit()
    return SessionOut(token=session.token, user=UserOut.model_validate(user), expires_at=session.expires_at)


@router.post("/salt", response_model=SaltResponse)
async def get_salt(payload: SaltRequest, db: DbSession) -> SaltResponse:
    username = _normalize(payload.username)
    user = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    # Unknown users still get a stable-looking salt so this endpoint doesn't leak existence.
    return SaltResponse(salt=user.salt if user else derive_pseudo_salt(username))


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: DbSession) -> SessionOut:
    username = _normalize(payload.username)
    if len(username) < MIN_USERNAME_LENGTH:
        raise _error("VALIDATION", "Ник должен быть не короче 3 символов", status.HTTP_400_BAD_REQUEST)

    exists = (
        await db.execute(select(User.id).where(User.username == username))
    ).scalar_one_or_none()
    if exists is not None:
        raise _error("USERNAME_TAKEN", "Такой ник уже занят", status.HTTP_409_CONFLICT)

    user = User(
        username=username,
        salt=payload.salt,
        password_hash=hash_client_hash(payload.password_hash),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return await _issue_session(db, user)


@router.post("/login", response_model=SessionOut)
async def login(payload: LoginRequest, db: DbSession) -> SessionOut:
    username = _normalize(payload.username)
    user = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if user is None or not verify_client_hash(payload.password_hash, user.password_hash):
        raise _error(
            "INVALID_CREDENTIALS", "Неверный ник или пароль", status.HTTP_401_UNAUTHORIZED
        )
    return await _issue_session(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(db: DbSession, session: Session = Depends(get_session)) -> None:
    await db.delete(session)
    await db.commit()


@router.get("/session", response_model=SessionOut)
async def read_session(db: DbSession, session: Session = Depends(get_session)) -> SessionOut:
    return SessionOut(
        token=session.token,
        user=UserOut.model_validate(session.user),
        expires_at=session.expires_at,
    )
