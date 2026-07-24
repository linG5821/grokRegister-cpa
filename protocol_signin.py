"""协议对齐的账密重登：Playwright + 本机 Chrome（同 turnstile_mint 技术栈）。

登录页需要 Turnstile + Castle，纯 HTTP 难稳定伪造 Castle，
因此在 sign-in 页内完成人机组件后抓 sso cookie（与注册的 mint 浏览器一致）。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Callable, Optional

from curl_cffi import requests

LogFn = Optional[Callable[[str], None]]

SIGNIN_URL = "https://accounts.x.ai/sign-in"
SITE_URL = "https://accounts.x.ai"
_SSO_RE = re.compile(r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+")


def _log(log: LogFn, msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def is_session_sso(tok: str) -> bool:
    t = str(tok or "").strip()
    if not t.startswith("eyJ") or t.count(".") < 2:
        return False
    # session SSO 一般不含 access-token 风格过长 scope 包；宽松：JWT 即可
    return True


def probe_sso_alive(sso: str, proxy: str = "", timeout: float = 20) -> bool:
    """True=仍可能有效；False=明确跳到 sign-in 或无 token。"""
    token = str(sso or "").strip()
    if token.lower().startswith("sso="):
        token = token[4:].strip()
    if not is_session_sso(token):
        return False
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    try:
        resp = requests.get(
            SITE_URL + "/",
            headers={
                "Cookie": f"sso={token}; sso-rw={token}",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
            impersonate="chrome131",
        )
        url = str(getattr(resp, "url", "") or "").lower()
        if "sign-in" in url or "sign-up" in url or "login" in url:
            return False
        if resp.status_code in (401, 403):
            return False
        # 仍在 accounts 域且非登录页 → 视为活
        if "accounts.x.ai" in url and "sign" not in url:
            return True
        # 被重定到 grok 等也算活
        if "grok" in url or "x.ai" in url:
            return True
        return resp.status_code == 200
    except Exception:
        return False


def _find_chrome() -> str:
    # 与 turnstile_mint 共用查找逻辑
    root = os.path.dirname(os.path.abspath(__file__))
    mint = os.path.join(root, "scripts", "turnstile_mint.py")
    if os.path.isfile(mint):
        sys.path.insert(0, os.path.join(root, "scripts"))
        try:
            import turnstile_mint as tm  # type: ignore

            return tm.find_chrome() or ""
        except Exception:
            pass
        finally:
            try:
                sys.path.remove(os.path.join(root, "scripts"))
            except ValueError:
                pass
    for p in (
        os.environ.get("CHROME_PATH") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if p and os.path.exists(p):
            return p
    return ""


async def _login_async(
    email: str,
    password: str,
    proxy: str = "",
    timeout: float = 120,
    headless: bool = False,
    log: LogFn = None,
) -> str:
    from playwright.async_api import async_playwright

    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Chromium（与 Turnstile mint 相同依赖）")

    env_headless = (os.environ.get("GROK_TURNSTILE_HEADLESS") or "").strip().lower()
    if env_headless in ("1", "true", "yes", "on"):
        headless = True
    elif env_headless in ("0", "false", "no", "off"):
        headless = False

    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
    ]
    if not headless:
        args.extend(
            [
                "--window-position=-32000,-32000",
                "--window-size=900,700",
                "--start-minimized",
            ]
        )
    launch: dict = {"executable_path": chrome, "headless": bool(headless), "args": args}
    if proxy:
        launch["proxy"] = {"server": proxy}

    _log(log, f"[relogin] 打开登录页 chrome={chrome} headless={headless}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch)
        try:
            context = await browser.new_context(viewport={"width": 900, "height": 700})
            await context.add_init_script(
                'Object.defineProperty(navigator,"webdriver",{get:()=>undefined})'
            )
            page = await context.new_page()
            await page.goto(SIGNIN_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)

            # 可能先是邮箱步
            email_sel = 'input[type="email"], input[name="email"], input[autocomplete="username"]'
            pass_sel = 'input[type="password"], input[name="password"], input[autocomplete="current-password"]'

            async def fill_if(sel: str, value: str) -> bool:
                try:
                    el = page.locator(sel).first
                    if await el.count() == 0:
                        return False
                    await el.click(timeout=5000)
                    await el.fill(value, timeout=5000)
                    return True
                except Exception:
                    return False

            if not await fill_if(email_sel, email):
                # 有的 UI 用 text input
                if not await fill_if('input[type="text"]', email):
                    raise RuntimeError("登录页未找到邮箱输入框")

            # 若还没有密码框，点继续
            if await page.locator(pass_sel).count() == 0:
                for label in ("Continue", "继续", "Next", "下一步", "使用邮箱", "Email"):
                    try:
                        btn = page.get_by_role("button", name=re.compile(label, re.I))
                        if await btn.count():
                            await btn.first.click(timeout=3000)
                            await page.wait_for_timeout(1200)
                            break
                    except Exception:
                        pass
                # 再试一次邮箱（有的分两步）
                await fill_if(email_sel, email)

            if not await fill_if(pass_sel, password):
                raise RuntimeError("登录页未找到密码输入框")

            # 等 Turnstile（页面自带，Castle 也在页内）
            _log(log, "[relogin] 等待 Turnstile / 提交登录…")
            deadline = asyncio.get_event_loop().time() + timeout
            submitted = False
            while asyncio.get_event_loop().time() < deadline:
                # 尝试点登录
                if not submitted:
                    for label in ("Log in", "Login", "Sign in", "登录", "Continue", "继续"):
                        try:
                            btn = page.get_by_role("button", name=re.compile(label, re.I))
                            if await btn.count():
                                await btn.first.click(timeout=2000)
                                submitted = True
                                _log(log, f"[relogin] 已点击: {label}")
                                break
                        except Exception:
                            pass
                    if not submitted:
                        try:
                            await page.keyboard.press("Enter")
                            submitted = True
                        except Exception:
                            pass

                # cookie 里找 sso
                try:
                    cookies = await context.cookies()
                    for c in cookies:
                        if c.get("name") == "sso" and is_session_sso(str(c.get("value") or "")):
                            sso = str(c["value"])
                            _log(log, "[relogin] 已从 cookie 拿到 SSO")
                            return sso
                except Exception:
                    pass

                # URL hop
                url = (page.url or "").lower()
                if "cookie" in url or "set-cookie" in url or "exchange" in url or "sso" in url:
                    await page.wait_for_timeout(800)
                    try:
                        cookies = await context.cookies()
                        for c in cookies:
                            if c.get("name") == "sso" and is_session_sso(str(c.get("value") or "")):
                                return str(c["value"])
                    except Exception:
                        pass

                # 错误文案
                try:
                    body = (await page.inner_text("body")).lower()
                    for bad in (
                        "wrong password",
                        "incorrect password",
                        "invalid password",
                        "密码错误",
                        "couldn't find",
                        "no account",
                        "user not found",
                        "账号不存在",
                    ):
                        if bad in body:
                            raise RuntimeError(f"登录失败: {bad}")
                except RuntimeError:
                    raise
                except Exception:
                    pass

                await page.wait_for_timeout(700)

            # 最后再读一次 cookie
            cookies = await context.cookies()
            for c in cookies:
                if c.get("name") == "sso" and is_session_sso(str(c.get("value") or "")):
                    return str(c["value"])
            raise RuntimeError("登录超时：未拿到 SSO cookie")
        finally:
            await browser.close()


def login_one(
    email: str,
    password: str,
    proxy: str = "",
    log: LogFn = None,
    should_stop: Optional[Callable[[], bool]] = None,
    timeout: float = 120,
    headless: bool = False,
) -> str:
    """账密重登，返回新 sso JWT。"""
    email = str(email or "").strip()
    password = str(password or "")
    if not email or not password:
        raise RuntimeError("email/password 不能为空")
    if should_stop and should_stop():
        raise RuntimeError("用户已停止")
    sso = asyncio.run(
        _login_async(
            email,
            password,
            proxy=str(proxy or "").strip(),
            timeout=timeout,
            headless=headless,
            log=log,
        )
    )
    if not is_session_sso(sso):
        raise RuntimeError("重登结果不是有效 SSO")
    return sso
