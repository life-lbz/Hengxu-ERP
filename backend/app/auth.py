from datetime import datetime, timedelta, timezone
from hashlib import scrypt, sha256
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe

from .domain import require
from .schema import SessionToken, User


def password_hash(password, salt=None):
    salt = salt or token_bytes(16).hex()
    digest = scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"{salt}.{digest.hex()}"


def token_hash(token):
    return sha256(token.encode()).hexdigest()


def login(session, company, username, password):
    user = session.get(User, f"{company}/{username}")
    encoded = user.password if user else "0" * 32 + "." + "0" * 128
    matches = compare_digest(password_hash(password, encoded.split(".")[0]), encoded)
    require(matches and user is not None, "LOGIN_FAILED", "账套、账号或密码无效", 401)
    token = token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=8)
    session.add(SessionToken(token_hash=token_hash(token), user_id=user.id, expires=expires))
    return token, user


def identify(session, token):
    record = session.get(SessionToken, token_hash(token))
    now = datetime.now(timezone.utc)
    require(record and record.expires.replace(tzinfo=timezone.utc) > now, "AUTH_REQUIRED", "请重新登录", 401)
    return session.get(User, record.user_id)


def account(user):
    return {
        "username": user.id.split("/", 1)[1],
        "name": user.name,
        "company": user.company,
        "role": user.role,
        "warehouses": user.warehouses,
    }
