from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import HTTPException


def _secret() -> bytes:
    s = os.getenv("FILES_SIGNING_SECRET", "")
    return s.encode("utf-8") if s else b""


def signing_enabled() -> bool:
    return bool(_secret())


def make_token(file_id: str, expires_at: int) -> str:
    sec = _secret()
    if not sec:
        return ""
    msg = f"{file_id}:{expires_at}".encode("utf-8")
    return hmac.new(sec, msg, hashlib.sha256).hexdigest()


def verify_token(file_id: str, expires_at: int, token: Optional[str]) -> None:
    sec = _secret()
    if not sec:
        # If not configured, skip validation entirely.
        return

    now = int(time.time())
    if expires_at < now:
        raise HTTPException(status_code=403, detail="Signed URL expired.")

    expected = make_token(file_id=file_id, expires_at=expires_at)
    if not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid signed URL token.")
