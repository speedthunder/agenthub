from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTED_PREFIX = "enc:v1:"


def validate_llm_base_url(url: str) -> None:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("Missing host in base_url")
    host_l = host.lower()
    if host_l in {"localhost", "localhost.localdomain"} or host_l.endswith(".localhost"):
        raise ValueError(f"Blocked internal host: {host}")
    try:
        ip = ipaddress.ip_address(host_l)
    except ValueError:
        # 非 IP 主機名，交由 DNS/網路層處理
        return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError(f"Blocked internal host: {host}")


def _load_master_key() -> bytes:
    secret = os.environ.get("APP_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("APP_SECRET_KEY is required for llm_api_key encryption")
    try:
        raw = base64.urlsafe_b64decode(secret.encode("utf-8"))
        if len(raw) == 32:
            return base64.urlsafe_b64encode(raw)
    except (binascii.Error, ValueError):
        pass
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_load_master_key())


def is_encrypted_llm_api_key(value: str | None) -> bool:
    return bool(value) and str(value).startswith(_ENCRYPTED_PREFIX)


def encrypt_llm_api_key(value: str | None) -> str:
    plain = (value or "").strip()
    if not plain:
        return ""
    token = _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")
    return _ENCRYPTED_PREFIX + token


def decrypt_llm_api_key(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not raw.startswith(_ENCRYPTED_PREFIX):
        # 舊資料相容：舊版明文資料允許讀取，更新時會轉為加密
        return raw
    token = raw[len(_ENCRYPTED_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Failed to decrypt llm_api_key") from e
