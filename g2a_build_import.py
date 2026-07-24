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


def _entry_email_key(entry: dict) -> str:
    return str((entry or {}).get("email") or (entry or {}).get("name") or "").strip().lower()


def _entry_user_key(entry: dict) -> str:
    return str(
        (entry or {}).get("user_id")
        or (entry or {}).get("principal_id")
        or ""
    ).strip().lower()


def _upsert_by_identity(accounts: list[dict], entry: dict) -> list[dict]:
    """按 email 优先、否则 user_id 去重；命中则覆盖为最新 entry。"""
    email = _entry_email_key(entry)
    uid = _entry_user_key(entry)
    out: list[dict] = []
    replaced = False
    for acc in accounts:
        acc_email = _entry_email_key(acc)
        acc_uid = _entry_user_key(acc)
        same = False
        if email and acc_email and email == acc_email:
            same = True
        elif (not email or not acc_email) and uid and acc_uid and uid == acc_uid:
            same = True
        if same:
            if not replaced:
                out.append(entry)
                replaced = True
            # 丢弃后续重复旧条
            continue
        out.append(acc)
    if not replaced:
        out.append(entry)
    return out


def _dedupe_accounts(accounts: list[dict]) -> list[dict]:
    """全量去重：同 email / 同 user_id 只保留最后一条（后写覆盖先写）。"""
    # 从后往前扫，先遇到的是更新版本
    kept_rev: list[dict] = []
    seen_email: set[str] = set()
    seen_uid: set[str] = set()
    for acc in reversed(list(accounts or [])):
        if not isinstance(acc, dict):
            continue
        if not str(acc.get("access_token") or "").strip() and not str(
            acc.get("refresh_token") or ""
        ).strip():
            continue
        email = _entry_email_key(acc)
        uid = _entry_user_key(acc)
        if email and email in seen_email:
            continue
        if uid and uid in seen_uid:
            # 无 email 时用 uid；有 email 的条已按 email 去重
            if not email:
                continue
        if email:
            seen_email.add(email)
        if uid:
            seen_uid.add(uid)
        kept_rev.append(acc)
    kept_rev.reverse()
    return kept_rev


def append_build_import(file_path: str, entry: dict, log: LogFn = None) -> str:
    """累加写入；同 email（或 user_id）覆盖。返回实际写入路径。"""
    path = resolve_import_path(file_path)
    if not entry or not (
        str(entry.get("access_token") or "").strip() or str(entry.get("refresh_token") or "").strip()
    ):
        return path
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
        accounts = _upsert_by_identity(accounts, entry)
        accounts = _dedupe_accounts(accounts)
        _atomic_write(path, accounts)
    return path


def rebuild_import_from_cpa_dir(
    cpa_auth_dir: str,
    import_file: str = "",
    log: LogFn = None,
) -> tuple[str, int]:
    """从本地 CPA auth 目录全量重建导入文件（按 email 去重，保留全部最新号）。

    返回 (path, account_count)。
    """
    auth_dir = str(cpa_auth_dir or "").strip()
    path = resolve_import_path(import_file)
    if not auth_dir or not os.path.isdir(auth_dir):
        _log(log, f"[g2a] CPA auth 目录不存在，跳过全量重建: {auth_dir}")
        return path, 0

    entries: list[dict] = []
    try:
        names = sorted(os.listdir(auth_dir))
    except OSError as exc:
        _log(log, f"[g2a] 读取 CPA auth 目录失败: {exc}")
        return path, 0

    for name in names:
        if not name.lower().endswith(".json"):
            continue
        # 常见 xai-*.json；也接受目录内其它 oauth json
        fpath = os.path.join(auth_dir, name)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as handle:
                rec = json.load(handle)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        # CPA 扁平记录：有 access/refresh；type 可选
        if not str(rec.get("access_token") or "").strip() and not str(rec.get("refresh_token") or "").strip():
            continue
        try:
            entry = build_import_entry(rec)
        except Exception:
            continue
        if not entry.get("access_token") and not entry.get("refresh_token"):
            continue
        entries.append(entry)

    with _write_lock:
        # 与现有文件合并后再去重：CPA 目录为准覆盖同 email，文件中 CPA 没有的号保留
        existing, corrupted = _load_accounts(path)
        if corrupted:
            bak = path + ".bak"
            try:
                shutil.copy2(path, bak)
                _log(log, f"[g2a] 导入文件损坏，已备份 {bak}")
            except Exception:
                pass
            existing = []
        merged = list(existing)
        for entry in entries:
            merged = _upsert_by_identity(merged, entry)
        merged = _dedupe_accounts(merged)
        # 再按 email 排序，稳定输出
        merged.sort(key=lambda a: (_entry_email_key(a) or _entry_user_key(a) or ""))
        _atomic_write(path, merged)
        n = len(merged)
    _log(log, f"[g2a] 已从 CPA 目录全量同步导入文件 {path}（{n} 个账号）")
    return path, n
