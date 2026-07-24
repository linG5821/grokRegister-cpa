"""chenyme grok2api：Web SSO 导入 + Build accounts/import（multipart file）。

永不调用 convert-to-build。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from curl_cffi import CurlMime, requests

from g2a_build_import import build_import_entry, single_account_payload

LogFn = Optional[Callable[[str], None]]

_token = ""
_token_expires_at: Optional[datetime] = None


def _log(log: LogFn, msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def normalize_base(base: str) -> str:
    return str(base or "").strip().rstrip("/")


def clear_token_cache() -> None:
    global _token, _token_expires_at
    _token = ""
    _token_expires_at = None


def login(base: str, username: str, password: str, log: LogFn = None) -> str:
    global _token, _token_expires_at
    root = normalize_base(base)
    if not root or not username or not password:
        raise RuntimeError("chenyme base/username/password 未配置")
    endpoint = f"{root}/api/admin/v1/auth/login"
    resp = requests.post(
        endpoint,
        headers={"Content-Type": "application/json"},
        json={"username": username, "password": password},
        timeout=30,
        proxies={},
    )
    resp.raise_for_status()
    payload = resp.json() if hasattr(resp, "json") else {}
    data = payload.get("data") if isinstance(payload, dict) else None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    access = ""
    expires_at = None
    if isinstance(tokens, dict):
        access = str(tokens.get("accessToken") or "").strip()
        raw_exp = tokens.get("accessTokenExpiresAt") or ""
        if raw_exp:
            try:
                text = str(raw_exp).replace("Z", "+00:00")
                expires_at = datetime.fromisoformat(text)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except Exception:
                expires_at = None
    if not access:
        raise RuntimeError("chenyme 登录响应缺少 accessToken")
    _token = access
    if expires_at is None:
        _token_expires_at = datetime.fromtimestamp(time.time() + 50 * 60, tz=timezone.utc)
    else:
        _token_expires_at = expires_at
    _log(log, "[chenyme] 登录成功")
    return access


def get_access_token(
    base: str,
    username: str,
    password: str,
    log: LogFn = None,
    force_refresh: bool = False,
) -> str:
    global _token, _token_expires_at
    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _token
        and _token_expires_at
        and (_token_expires_at - now).total_seconds() > 60
    ):
        return _token
    return login(base, username, password, log=log)


def _normalize_sso(raw: str) -> str:
    sso = str(raw or "").strip()
    if sso.lower().startswith("sso="):
        sso = sso[4:].strip()
    return sso


def _resp_preview(resp: Any, limit: int = 240) -> str:
    try:
        text = getattr(resp, "text", "") or ""
        return str(text).replace("\n", " ")[:limit]
    except Exception:
        return ""


def _http_error(resp: Any, what: str) -> RuntimeError:
    code = getattr(resp, "status_code", "?")
    if int(code or 0) == 401:
        return RuntimeError("chenyme unauthorized")
    return RuntimeError(f"{what} HTTP {code}: {_resp_preview(resp)}")


def _multipart_post(
    url: str,
    admin_token: str,
    field: str,
    filename: str,
    data: bytes,
    content_type: str,
    timeout: float,
) -> Any:
    """curl_cffi 不支持 files=，必须用 CurlMime multipart。"""
    mp = CurlMime()
    try:
        mp.addpart(
            name=field,
            content_type=content_type,
            filename=filename,
            data=data,
        )
        return requests.post(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            multipart=mp,
            timeout=timeout,
            proxies={},
        )
    finally:
        try:
            mp.close()
        except Exception:
            pass


def import_web_sso(
    base: str,
    admin_token: str,
    sso_token: str,
    log: LogFn = None,
) -> bool:
    """POST /api/admin/v1/accounts/web/import — multipart 纯 SSO 一行。"""
    root = normalize_base(base)
    sso = _normalize_sso(sso_token)
    if not root or not admin_token or not sso:
        raise RuntimeError("web/import 参数不完整")
    endpoint = f"{root}/api/admin/v1/accounts/web/import"
    content = (sso + "\n").encode("utf-8")
    # chenyme 管理端常见字段名为 files；失败时再试 file
    last_err: Optional[BaseException] = None
    for field in ("files", "file"):
        resp = _multipart_post(
            endpoint,
            admin_token,
            field=field,
            filename="grok-web-sso-tokens.txt",
            data=content,
            content_type="text/plain",
            timeout=60,
        )
        code = int(getattr(resp, "status_code", 0) or 0)
        if code == 401:
            raise RuntimeError("chenyme unauthorized")
        if 200 <= code < 300:
            _ = getattr(resp, "text", "") or ""
            _log(log, f"[chenyme] web/import SSO 完成 (field={field})")
            return True
        last_err = _http_error(resp, f"web/import field={field}")
        if code in (400, 404, 415, 422):
            continue
        break
    raise last_err or RuntimeError("web/import 失败")


def import_build_account(
    base: str,
    admin_token: str,
    entry: dict,
    log: LogFn = None,
) -> bool:
    """POST /api/admin/v1/accounts/import — multipart 字段 file，单号 accounts JSON。"""
    root = normalize_base(base)
    if not root or not admin_token or not entry:
        raise RuntimeError("accounts/import 参数不完整")
    endpoint = f"{root}/api/admin/v1/accounts/import"
    payload = single_account_payload(entry)
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    email = str(entry.get("email") or entry.get("name") or "")
    last_err: Optional[BaseException] = None
    # 用户实测字段名为 file；兼容 files
    for field in ("file", "files"):
        resp = _multipart_post(
            endpoint,
            admin_token,
            field=field,
            filename="grok-build-account.json",
            data=raw,
            content_type="application/json",
            timeout=120,
        )
        code = int(getattr(resp, "status_code", 0) or 0)
        if code == 401:
            raise RuntimeError("chenyme unauthorized")
        if 200 <= code < 300:
            preview = _resp_preview(resp, 120)
            _log(log, f"[chenyme] accounts/import Build 完成 ({email}) field={field} {preview}")
            return True
        last_err = _http_error(resp, f"accounts/import field={field}")
        if code in (400, 404, 415, 422):
            continue
        break
    raise last_err or RuntimeError("accounts/import 失败")


def maybe_import_web_sso(config: dict, sso: str, log: LogFn = None) -> None:
    if not config.get("chenyme_grok2api_enabled"):
        return
    base = normalize_base(config.get("chenyme_grok2api_base", ""))
    user = str(config.get("chenyme_grok2api_username", "") or "").strip()
    password = str(config.get("chenyme_grok2api_password", "") or "").strip()
    if not base or not user or not password:
        _log(log, "[!] chenyme SSO 导入已开但未配置 base/账号，跳过")
        return
    sso_n = _normalize_sso(sso)
    if not sso_n:
        _log(log, "[!] chenyme web/import 跳过：SSO 为空")
        return
    try:
        for attempt in range(2):
            try:
                token = get_access_token(base, user, password, log=log, force_refresh=(attempt > 0))
                import_web_sso(base, token, sso_n, log=log)
                return
            except RuntimeError as exc:
                if "unauthorized" in str(exc).lower() and attempt == 0:
                    clear_token_cache()
                    continue
                raise
    except Exception as exc:
        _log(log, f"[!] chenyme web/import 失败: {exc}")


def maybe_export_build_after_cpa(
    config: dict,
    cpa_record: dict,
    log: LogFn = None,
) -> None:
    """CPA record 成功后：本地累加文件 + 可选远程 multipart import。"""
    if not cpa_record:
        return
    try:
        entry = build_import_entry(cpa_record)
    except Exception as exc:
        _log(log, f"[!] Build 导入条目构造失败: {exc}")
        return
    if not entry.get("access_token") or not entry.get("refresh_token"):
        _log(log, "[!] Build 条目缺 token，跳过 g2a 导出")
        return

    if config.get("g2a_build_import_file_enabled"):
        try:
            from g2a_build_import import (
                DEFAULT_IMPORT_FILE,
                append_build_import,
                rebuild_import_from_cpa_dir,
            )

            file_cfg = config.get("g2a_build_import_file") or DEFAULT_IMPORT_FILE
            auth_dir = str(config.get("cpa_auth_dir") or "").strip()
            if auth_dir:
                # 有 CPA 目录：以目录为权威源全量同步（按邮箱去重，保证文件=全部最新）
                path, n = rebuild_import_from_cpa_dir(auth_dir, file_cfg, log=log)
                _log(log, f"[g2a] 本地导入文件已全量同步 {path}（{n} 个账号）")
            else:
                path = append_build_import(file_cfg, entry, log=log)
                _log(log, f"[g2a] 已按邮箱更新本地导入文件 {path}")
        except Exception as exc:
            _log(log, f"[!] 写本地 Build 导入文件失败: {exc}")

    if not config.get("g2a_build_remote_import_enabled"):
        return
    base = normalize_base(config.get("chenyme_grok2api_base", ""))
    user = str(config.get("chenyme_grok2api_username", "") or "").strip()
    password = str(config.get("chenyme_grok2api_password", "") or "").strip()
    if not base or not user or not password:
        _log(log, "[!] Build 远程导入已开但 chenyme 未配置，跳过")
        return
    try:
        for attempt in range(2):
            try:
                token = get_access_token(base, user, password, log=log, force_refresh=(attempt > 0))
                import_build_account(base, token, entry, log=log)
                return
            except RuntimeError as exc:
                if "unauthorized" in str(exc).lower() and attempt == 0:
                    clear_token_cache()
                    continue
                raise
    except Exception as exc:
        _log(log, f"[!] chenyme accounts/import 失败: {exc}")
