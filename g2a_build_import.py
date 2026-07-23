"""CPA token record → grok2api Build 导入条目 + 本地累加文件。"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# xAI Grok CLI / Device Flow 固定 client_id（与 sso_to_auth_json.CLIENT_ID 一致）
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
# 默认放在项目 exports/ 下，避免污染仓库根目录
DEFAULT_IMPORT_FILE = os.path.join("exports", "grok2api_build_import.json")
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

_write_lock = threading.Lock()
LogFn = Optional[Callable[[str], None]]


def resolve_import_path(file_path: str = "") -> str:
    """相对路径按项目根解析；空则用 DEFAULT_IMPORT_FILE。"""
    path = str(file_path or "").strip() or DEFAULT_IMPORT_FILE
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(_PROJECT_ROOT, path))


def _log(log: LogFn, msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def decode_jwt_payload(token: str) -> dict:
    text = str(token or "").strip()
    if not text or text.count(".") < 2:
        return {}
    try:
        segment = text.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        raw = base64.urlsafe_b64decode(segment)
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return {}


def build_import_entry(cpa_record: dict) -> dict:
    """CPA xai 记录 → grok2api Build 导入条目（无 team_id）。"""
    rec = dict(cpa_record or {})
    access = str(rec.get("access_token") or "").strip()
    refresh = str(rec.get("refresh_token") or "").strip()
    email = str(rec.get("email") or "").strip().lower()
    id_token = str(rec.get("id_token") or "").strip()
    claims = decode_jwt_payload(access)

    client_id = str(claims.get("client_id") or CLIENT_ID).strip() or CLIENT_ID
    sub = str(claims.get("sub") or claims.get("principal_id") or rec.get("sub") or "").strip()

    expires_in = 0
    try:
        expires_in = int(rec.get("expires_in") or 0)
    except Exception:
        expires_in = 0
    expires_at = str(rec.get("expired") or "").strip()
    if not expires_at:
        exp = int(claims.get("exp") or 0)
        if exp:
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif expires_in > 0:
            expires_at = datetime.fromtimestamp(
                time.time() + expires_in, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not expires_in and claims.get("exp"):
        try:
            expires_in = max(int(claims["exp"]) - int(time.time()), 0)
        except Exception:
            pass

    entry = {
        "provider": "grok_build",
        "name": email,
        "email": email,
        "client_id": client_id,
        "access_token": access,
        "refresh_token": refresh,
        "id_token": id_token,
        "token_type": str(rec.get("token_type") or "Bearer"),
        "expires_at": expires_at,
        "expires_in": expires_in,
    }
    if sub:
        entry["user_id"] = sub
        entry["principal_id"] = sub
    return entry


def single_account_payload(entry: dict) -> dict:
    """远程 multipart 用：每号一文件。"""
    return {"accounts": [entry]}


def _load_accounts(path: str) -> tuple[list[dict], bool]:
    if not os.path.exists(path):
        return [], False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return [], True
    if not isinstance(data, dict):
        return [], True
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return [], True
    return [a for a in accounts if isinstance(a, dict)], False


def _atomic_write(path: str, accounts: list[dict]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".g2a-import-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"accounts": accounts}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except Exception:
            pass
        os.replace(temp_path, path)
        temp_path = None
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def append_build_import(file_path: str, entry: dict, log: LogFn = None) -> None:
    """累加写入；同 email 覆盖。路径默认 exports/ 下，相对路径相对项目根。"""
    path = resolve_import_path(file_path)
    email = str((entry or {}).get("email") or (entry or {}).get("name") or "").strip().lower()
    with _write_lock:
        accounts, corrupted = _load_accounts(path)
        if corrupted:
            bak = path + ".bak"
            try:
                shutil.copy2(path, bak)
                _log(log, f"[g2a] 导入文件损坏，已备份 {bak}")
            except Exception as exc:
                _log(log, f"[g2a] 导入文件损坏且备份失败: {exc}")
            accounts = []
        replaced = False
        if email:
            for i, acc in enumerate(accounts):
                existing = str(acc.get("email") or acc.get("name") or "").strip().lower()
                if existing == email:
                    accounts[i] = entry
                    replaced = True
                    break
        if not replaced:
            accounts.append(entry)
        _atomic_write(path, accounts)
