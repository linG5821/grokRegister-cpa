"""SSO 失效账密重登：输出到独立目录，可选再 Device Flow。"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from protocol_signin import is_session_sso, login_one, probe_sso_alive

LogFn = Optional[Callable[[str], None]]

APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = "relogin_accounts"
_LINE_RE = re.compile(
    r"^(?P<email>[^-\s][^\n]*?)----(?P<password>.*?)----(?P<sso>\S+)\s*$"
)


def _log(log: LogFn, msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def parse_account_line(line: str) -> Optional[tuple[str, str, str]]:
    text = str(line or "").strip()
    if not text or text.startswith("#"):
        return None
    m = _LINE_RE.match(text)
    if m:
        email = m.group("email").strip()
        password = m.group("password")
        sso = m.group("sso").strip()
        if sso.lower().startswith("sso="):
            sso = sso[4:].strip()
        return email, password, sso
    # 宽松：最多两段分隔
    parts = text.split("----")
    if len(parts) >= 3:
        email, password, sso = parts[0].strip(), parts[1], parts[-1].strip()
        if sso.lower().startswith("sso="):
            sso = sso[4:].strip()
        return email, password, sso
    return None


def scan_account_files(scan_dir: str | Path) -> list[tuple[str, str, str, str]]:
    """返回 (email, password, sso, source_file)。按 email 去重保留最后一条。"""
    root = Path(scan_dir)
    by_email: dict[str, tuple[str, str, str, str]] = {}
    files = sorted(root.glob("accounts_*.txt")) + sorted(root.glob("sso_pending.txt"))
    # 也扫 relogin 历史，避免重复但作为候选
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            parsed = parse_account_line(line)
            if not parsed:
                continue
            email, password, sso = parsed
            key = email.casefold()
            by_email[key] = (email, password, sso, str(path.name))
    return list(by_email.values())


def run_sso_relogin(
    *,
    scan_dir: str | Path | None = None,
    out_dir: str = DEFAULT_OUT_DIR,
    only_dead: bool = True,
    auto_device_flow: bool = True,
    cpa_auth_dir: str = "",
    cpa_remote_url: str = "",
    cpa_management_key: str = "",
    g2a_build_import_file: str | None = None,
    proxy: str = "",
    workers: int = 1,
    headless: bool = False,
    log: LogFn = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """扫描 accounts，失效则账密重登；新 SSO 写入 out_dir。"""
    root = Path(scan_dir or APP_DIR)
    out_path = Path(out_dir)
    if not out_path.is_absolute():
        out_path = APP_DIR / out_path
    out_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    success_file = out_path / f"relogin_{stamp}.txt"
    fail_file = out_path / "relogin_failed.txt"

    entries = scan_account_files(root)
    total = len(entries)
    _log(log, f"[relogin] 扫描到 {total} 个账号（email 去重），输出目录 {out_path}")
    if total == 0:
        return {"total": 0, "ok": 0, "skip_alive": 0, "fail": 0, "device_ok": 0}

    workers = max(1, min(int(workers or 1), 4))
    lock = threading.Lock()
    stats = {"ok": 0, "skip_alive": 0, "fail": 0, "device_ok": 0, "stopped": False}
    success_lines: list[str] = []

    def _append_fail(email: str, reason: str) -> None:
        line = f"{email}----{reason}----{int(time.time())}\n"
        with lock:
            try:
                with open(fail_file, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass
            stats["fail"] += 1

    def _process(i: int, email: str, password: str, sso: str, source: str) -> None:
        if should_stop and should_stop():
            with lock:
                stats["stopped"] = True
            return
        prefix = f"[{i}/{total}] {email}"
        alive = probe_sso_alive(sso, proxy=proxy)
        if only_dead and alive:
            _log(log, f"{prefix}: SSO 仍有效，跳过（only_dead）")
            with lock:
                stats["skip_alive"] += 1
            return
        if not password:
            _log(log, f"{prefix}: 无密码，无法重登")
            _append_fail(email, "no_password")
            return
        _log(log, f"{prefix}: SSO 失效，开始账密重登（源 {source}）…")
        try:
            new_sso = login_one(
                email,
                password,
                proxy=proxy,
                log=log,
                should_stop=should_stop,
                headless=headless,
            )
        except Exception as exc:
            _log(log, f"{prefix}: 重登失败: {exc}")
            _append_fail(email, f"relogin_fail:{exc}"[:200])
            return

        line = f"{email}----{password}----{new_sso}\n"
        with lock:
            success_lines.append(line)
            try:
                with open(success_file, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as exc:
                _log(log, f"{prefix}: 写成功文件失败: {exc}")
            stats["ok"] += 1
        _log(log, f"{prefix}: 重登成功，已写入 {success_file.name}")

        if not auto_device_flow:
            return
        if should_stop and should_stop():
            return
        try:
            import sso_to_auth_json as s2a

            result = s2a.convert_sso_entries(
                [(email, new_sso)],
                cpa_auth_dir=cpa_auth_dir or None,
                cpa_remote_url=cpa_remote_url or None,
                cpa_management_key=cpa_management_key or None,
                g2a_build_import_file=g2a_build_import_file,
                force_reconvert=True,
                proxy=proxy,
                workers=1,
                log=lambda m: _log(log, f"{prefix} [device] {m}"),
                should_stop=should_stop,
            )
            if int(result.get("ok") or 0) > 0:
                with lock:
                    stats["device_ok"] += 1
                _log(log, f"{prefix}: Device Flow / CPA 完成")
            else:
                _log(log, f"{prefix}: Device Flow 未成功 ok={result.get('ok')} fail={result.get('fail')}")
        except Exception as exc:
            _log(log, f"{prefix}: Device Flow 异常: {exc}")

    if workers <= 1:
        for i, (email, password, sso, source) in enumerate(entries, 1):
            if should_stop and should_stop():
                stats["stopped"] = True
                break
            _process(i, email, password, sso, source)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [
                pool.submit(_process, i, email, password, sso, source)
                for i, (email, password, sso, source) in enumerate(entries, 1)
            ]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as exc:
                    _log(log, f"[relogin] worker 异常: {exc}")

    _log(
        log,
        f"[relogin] 结束: 重登成功={stats['ok']} 跳过(仍活)={stats['skip_alive']} "
        f"失败={stats['fail']} device_ok={stats['device_ok']} "
        f"成功文件={success_file if stats['ok'] else '(无)'}",
    )
    return {
        "total": total,
        "ok": stats["ok"],
        "skip_alive": stats["skip_alive"],
        "fail": stats["fail"],
        "device_ok": stats["device_ok"],
        "stopped": stats["stopped"],
        "success_file": str(success_file) if stats["ok"] else "",
        "fail_file": str(fail_file),
        "out_dir": str(out_path),
    }
