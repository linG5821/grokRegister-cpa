"""协议对齐的账密重登：Playwright + 本机 Chrome（同 turnstile_mint 技术栈）。

登录页需要 Turnstile + Castle，纯 HTTP 难稳定伪造 Castle，
因此在 sign-in 页内完成人机组件后抓 sso cookie（与注册的 mint 浏览器一致）。

sign-in 首页只有 OAuth +「Login with email」按钮，必须先点
data-testid=continue-with-email 才会出现邮箱/密码框。
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

# 与 register_flow 对齐的候选选择器
_EMAIL_SELECTORS = [
    'input[data-testid="email"]',
    'input[name="email"]',
    'input[type="email"]',
    'input[autocomplete="email"]',
    'input[autocomplete="username"]',
    'input[placeholder*="mail" i]',
    'input[aria-label*="mail" i]',
    'input[placeholder*="邮箱"]',
    'input[aria-label*="邮箱"]',
]
_PASS_SELECTORS = [
    'input[data-testid="password"]',
    'input[name="password"]',
    'input[type="password"]',
    'input[autocomplete="current-password"]',
    'input[autocomplete="new-password"]',
]


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
        if "accounts.x.ai" in url and "sign" not in url:
            return True
        if "grok" in url or "x.ai" in url:
            return True
        return resp.status_code == 200
    except Exception:
        return False


def _find_chrome() -> str:
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


async def _click_continue_with_email(page, log: LogFn = None) -> bool:
    """sign-in 首页必须先点 Login with email，才会出现邮箱框。"""
    selectors = [
        '[data-testid="continue-with-email"]',
        'button:has-text("Login with email")',
        'button:has-text("Continue with email")',
        'button:has-text("Sign in with email")',
        'button:has-text("使用邮箱")',
        'button:has-text("邮箱登录")',
        'a:has-text("Login with email")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if not await loc.is_visible():
                continue
            await loc.click(timeout=5000)
            _log(log, f"[relogin] 已点邮箱入口: {sel}")
            await page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    # role + name 兜底
    for name in (
        r"Login with email",
        r"Continue with email",
        r"Sign in with email",
        r"使用邮箱",
        r"邮箱登录",
        r"Email",
    ):
        try:
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            if await btn.count():
                await btn.first.click(timeout=4000)
                _log(log, f"[relogin] 已点邮箱入口(role): {name}")
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    return False


async def _fill_by_selectors(page, selectors: list[str], value: str) -> bool:
    """优先 Playwright fill；失败则用 JS 写 React 受控输入。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if not await loc.is_visible():
                continue
            await loc.click(timeout=4000)
            await loc.fill("", timeout=3000)
            await loc.fill(value, timeout=5000)
            # 校验是否写进去
            got = await loc.input_value()
            if (got or "").strip() == value:
                return True
        except Exception:
            continue

    # JS 兜底（与 register_flow 同思路；Playwright evaluate 只接受一个 arg）
    js = """
    (arg) => {
      const value = arg.value;
      const preferEmail = !!arg.preferEmail;
      function isVisible(node) {
        if (!node) return false;
        const style = window.getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }
      function textOf(node) {
        return [
          node.innerText, node.textContent,
          node.getAttribute('aria-label'), node.getAttribute('title'),
          node.getAttribute('placeholder'), node.getAttribute('data-testid'),
          node.getAttribute('name'), node.getAttribute('id'),
          node.getAttribute('autocomplete'),
        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
      }
      function setValue(input, v) {
        input.focus(); input.click();
        const proto = input instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        const tracker = input._valueTracker;
        if (tracker) tracker.setValue('');
        if (setter) setter.call(input, v); else input.value = v;
        input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: v, inputType: 'insertText' }));
        input.dispatchEvent(new InputEvent('input', { bubbles: true, data: v, inputType: 'insertText' }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return (input.value || '').trim() === v;
      }
      const all = Array.from(document.querySelectorAll('input, textarea'));
      let candidates = [];
      if (preferEmail) {
        candidates = all.filter((n) => {
          if (!isVisible(n) || n.disabled || n.readOnly) return false;
          const type = (n.getAttribute('type') || '').toLowerCase();
          if (['hidden','submit','button','checkbox','radio','file','password','search'].includes(type)) return false;
          const meta = textOf(n);
          return type === 'email' || meta.includes('email') || meta.includes('mail')
            || meta.includes('邮箱') || n.getAttribute('autocomplete') === 'username'
            || n.getAttribute('data-testid') === 'email' || n.getAttribute('name') === 'email';
        });
      } else {
        candidates = all.filter((n) => {
          if (!isVisible(n) || n.disabled || n.readOnly) return false;
          const type = (n.getAttribute('type') || '').toLowerCase();
          const meta = textOf(n);
          return type === 'password' || meta.includes('password') || meta.includes('密码')
            || n.getAttribute('data-testid') === 'password' || n.getAttribute('name') === 'password';
        });
      }
      for (const node of candidates) {
        if (setValue(node, value)) return true;
      }
      return false;
    }
    """
    try:
        prefer_email = any("email" in s or "mail" in s or "username" in s for s in selectors)
        ok = await page.evaluate(js, {"value": value, "preferEmail": prefer_email})
        return bool(ok)
    except Exception:
        return False


async def _read_sso_cookie(context) -> str:
    try:
        cookies = await context.cookies()
        for c in cookies:
            if c.get("name") == "sso" and is_session_sso(str(c.get("value") or "")):
                return str(c["value"])
    except Exception:
        pass
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

            # 1) 首页只有 OAuth + Login with email
            email_ready = False
            for attempt in range(3):
                if await _fill_by_selectors(page, _EMAIL_SELECTORS, email):
                    email_ready = True
                    break
                clicked = await _click_continue_with_email(page, log=log)
                if not clicked and attempt == 0:
                    _log(log, "[relogin] 未看到 continue-with-email，继续探测输入框…")
                await page.wait_for_timeout(800)

            if not email_ready:
                # 诊断快照
                try:
                    snap = await page.evaluate(
                        """() => ({
                          url: location.href,
                          title: document.title,
                          testids: Array.from(document.querySelectorAll('[data-testid]'))
                            .map(n => n.getAttribute('data-testid')).slice(0, 20),
                          inputs: Array.from(document.querySelectorAll('input')).map(n => ({
                            type: n.type, name: n.name, testid: n.getAttribute('data-testid'),
                            ph: n.placeholder, visible: !!(n.offsetWidth||n.offsetHeight)
                          })).slice(0, 12),
                          buttons: Array.from(document.querySelectorAll('button'))
                            .map(n => (n.innerText||'').trim().slice(0,60)).filter(Boolean).slice(0, 12),
                        })"""
                    )
                    _log(log, f"[relogin] 页面诊断: {snap}")
                except Exception:
                    pass
                raise RuntimeError("登录页未找到邮箱输入框（需先点 Login with email）")

            _log(log, "[relogin] 邮箱已填")

            # 2) 若无密码框，点 Continue/下一步
            pass_ready = await _fill_by_selectors(page, _PASS_SELECTORS, password)
            if not pass_ready:
                for label in ("Continue", "继续", "Next", "下一步", "Log in", "Login", "Sign in", "登录"):
                    try:
                        btn = page.get_by_role("button", name=re.compile(label, re.I))
                        if await btn.count():
                            await btn.first.click(timeout=3000)
                            await page.wait_for_timeout(1200)
                            break
                    except Exception:
                        pass
                # submit 类型
                try:
                    sub = page.locator('button[type="submit"]').first
                    if await sub.count():
                        await sub.click(timeout=3000)
                        await page.wait_for_timeout(1200)
                except Exception:
                    pass
                pass_ready = await _fill_by_selectors(page, _PASS_SELECTORS, password)

            if not pass_ready:
                raise RuntimeError("登录页未找到密码输入框")
            _log(log, "[relogin] 密码已填，等待 Turnstile / 提交…")

            deadline = asyncio.get_event_loop().time() + timeout
            submitted = False
            while asyncio.get_event_loop().time() < deadline:
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
                            sub = page.locator('button[type="submit"]').first
                            if await sub.count():
                                await sub.click(timeout=2000)
                                submitted = True
                                _log(log, "[relogin] 已点击 submit")
                        except Exception:
                            pass
                    if not submitted:
                        try:
                            await page.keyboard.press("Enter")
                            submitted = True
                        except Exception:
                            pass

                sso = await _read_sso_cookie(context)
                if sso:
                    _log(log, "[relogin] 已从 cookie 拿到 SSO")
                    return sso

                url = (page.url or "").lower()
                if any(k in url for k in ("cookie", "set-cookie", "exchange", "sso", "grok.com")):
                    await page.wait_for_timeout(800)
                    sso = await _read_sso_cookie(context)
                    if sso:
                        return sso

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

            sso = await _read_sso_cookie(context)
            if sso:
                return sso
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
