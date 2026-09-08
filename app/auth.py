"""Accounts: self-service signup + login.

Passwords are never stored — only a salted PBKDF2-SHA256 hash (stdlib, no extra
dependency). Consequently nobody can look a password up; a rater who forgets
theirs gets a new one via `python -m app.manage reset-password <email>`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Tuple

from config import ADMIN_EMAILS, DATA_DIR

ACCOUNTS_PATH = DATA_DIR / "accounts.json"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8
_PBKDF2_ITER = 200_000


# ---- password hashing -------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITER).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        cand = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters)).hex()
        return hmac.compare_digest(cand, digest)
    except (ValueError, AttributeError):
        return False


# ---- account store ----------------------------------------------------------
def load_accounts() -> dict:
    if not ACCOUNTS_PATH.exists():
        return {}
    try:
        return json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_accounts(accounts: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(accounts, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ACCOUNTS_PATH)


def create_account(email: str, name: str, password: str, position: str = "", experience_years: str = "") -> Tuple[bool, str]:
    email = email.strip().lower()
    name = name.strip()
    if not EMAIL_RE.match(email):
        return False, "Please enter a valid email address."
    if not name:
        return False, "Please enter a name."
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"The password must be at least {MIN_PASSWORD_LEN} characters."
    accounts = load_accounts()
    if email in accounts:
        return False, "An account already exists for this email — please log in instead."
    accounts[email] = {
        "name": name,
        "password_hash": hash_password(password),
        "role": "admin" if email in ADMIN_EMAILS else "rater",
        "position": position or "",
        "experience_years": str(experience_years or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_accounts(accounts)
    return True, "Account created — you can log in now."


def check_login(email: str, password: str) -> Tuple[bool, str, str]:
    """Returns (ok, name_or_error, role)."""
    email = email.strip().lower()
    acc = load_accounts().get(email)
    if not acc or not verify_password(password, acc.get("password_hash", "")):
        return False, "Email or password is incorrect.", ""
    role = "admin" if (acc.get("role") == "admin" or email in ADMIN_EMAILS) else "rater"
    return True, acc.get("name") or email, role


def set_role(email: str, role: str) -> bool:
    email = email.strip().lower()
    accounts = load_accounts()
    if email not in accounts:
        return False
    accounts[email]["role"] = role
    _save_accounts(accounts)
    return True


def set_password(email: str, password: str) -> bool:
    email = email.strip().lower()
    accounts = load_accounts()
    if email not in accounts:
        return False
    accounts[email]["password_hash"] = hash_password(password)
    _save_accounts(accounts)
    return True


def user_slug(email: str) -> str:
    """Filesystem-safe identifier derived from the email, used for annotation filenames."""
    return re.sub(r"[^a-z0-9_.-]", "_", email.strip().lower())
