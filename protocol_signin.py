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


async def _hide_browser_windows(browser, log: LogFn = None) -> None:
    """与注册 browser_session 一致：Windows 下最小化/移出屏幕/隐藏；其它平台仅启动参数屏外。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        # Playwright 不直接暴露 root pid；用 CDP / 进程树不可靠时枚举标题含 Chromium/Chrome 的新窗
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        SW_MINIMIZE = 6
        SW_HIDE = 0
        flags_move = 0x0010 | 0x4000  # SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
        flags_bottom = 0x0001 | 0x0002 | 0x0010 | 0x4000
        browser_hwnds: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                    # 仍收集可能刚创建的
                    pass
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = (buf.value or "").lower()
                # 重登窗口标题通常含 Sign In / x.ai / Chrome
                if any(
                    k in title
                    for k in (
                        "sign in",
                        "x.ai",
                        "spacexai",
                        "accounts.x.ai",
                        "chrome",
                        "chromium",
                    )
                ):
                    browser_hwnds.append(int(hwnd))
            except Exception:
                pass
            return True

        user32.EnumWindows(_enum, 0)
        moved = 0
        for hwnd in browser_hwnds:
            try:
                user32.ShowWindow(hwnd, SW_MINIMIZE)
                user32.SetWindowPos(
                    hwnd, wintypes.HWND(1), -32000, -32000, 1, 1, flags_move
                )
                user32.SetWindowPos(hwnd, wintypes.HWND(1), 0, 0, 0, 0, flags_bottom)
                user32.ShowWindow(hwnd, SW_HIDE)
                moved += 1
            except Exception:
                pass
        if moved and log:
            _log(log, f"[relogin] 已隐藏浏览器窗口数={moved}")
    except Exception as exc:
        _log(log, f"[relogin] 隐藏窗口失败(忽略): {exc}")


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
    """sign-in 首页必须先点 Login with email，才会出现邮箱框。

    页面文案为英文 "Login with email"（data-testid=continue-with-email）。
    屏外/最小化时 is_visible 常为 false，必须 force / JS 点击。
    """
    selectors = [
        '[data-testid="continue-with-email"]',
        'button[data-testid="continue-with-email"]',
        'button:has-text("Login with email")',
        'button:has-text("Log in with email")',
        'button:has-text("Continue with email")',
        'button:has-text("Sign in with email")',
        'button:has-text("Login with Email")',
        'button:has-text("使用邮箱")',
        'button:has-text("邮箱登录")',
        'a:has-text("Login with email")',
        'a:has-text("Log in with email")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            # 屏外窗口不要依赖 is_visible
            try:
                await loc.click(timeout=5000, force=True)
            except Exception:
                await loc.evaluate("el => el.click()")
            _log(log, f"[relogin] 已点邮箱入口: {sel}")
            await page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    # role + name（含 Login with / Log in with）
    for name in (
        r"Login with email",
        r"Log in with email",
        r"Continue with email",
        r"Sign in with email",
        r"Login with Email",
        r"使用邮箱",
        r"邮箱登录",
        r"Login with",
        r"Log in with",
    ):
        try:
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            if await btn.count():
                try:
                    await btn.first.click(timeout=4000, force=True)
                except Exception:
                    await btn.first.evaluate("el => el.click()")
                _log(log, f"[relogin] 已点邮箱入口(role): {name}")
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    # JS 兜底：按文案模糊匹配（Login with email / Log in with email 等）
    try:
        clicked = await page.evaluate(
            """() => {
              const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
              const nodes = Array.from(document.querySelectorAll(
                'button, a, [role="button"], [data-testid="continue-with-email"]'
              ));
              const prefer = document.querySelector('[data-testid="continue-with-email"]');
              if (prefer) { prefer.click(); return 'testid'; }
              for (const n of nodes) {
                const t = norm(n.innerText || n.textContent || n.getAttribute('aria-label'));
                if (!t) continue;
                if (
                  t.includes('login with email') ||
                  t.includes('log in with email') ||
                  t.includes('continue with email') ||
                  t.includes('sign in with email') ||
                  t.includes('使用邮箱') ||
                  t.includes('邮箱登录') ||
                  (t.includes('login with') && t.includes('email')) ||
                  (t.includes('log in with') && t.includes('email'))
                ) {
                  n.click();
                  return t.slice(0, 60);
                }
              }
              return '';
            }"""
        )
        if clicked:
            _log(log, f"[relogin] 已点邮箱入口(js): {clicked}")
            await page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return False


async def _fill_by_selectors(page, selectors: list[str], value: str) -> bool:
    """优先 Playwright fill；失败则用 JS 写 React 受控输入。

    屏外窗口下 is_visible 常 false，不依赖可见性。
    """
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            try:
                await loc.click(timeout=4000, force=True)
            except Exception:
                pass
            try:
                await loc.fill("", timeout=3000)
            except Exception:
                pass
            try:
                await loc.fill(value, timeout=5000)
            except Exception:
                # 屏外时 fill 可能因不可见失败，交给 JS 兜底
                continue
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


async def _page_cf_challenge(page) -> bool:
    """True = 仍卡在 Cloudflare 整页挑战（Just a moment 等）。"""
    try:
        info = await page.evaluate(
            """() => {
              const title = (document.title || '').toLowerCase();
              const body = ((document.body && document.body.innerText) || '').slice(0, 500).toLowerCase();
              const html = (document.documentElement && document.documentElement.innerHTML || '').slice(0, 2000).toLowerCase();
              const jammed =
                title.includes('just a moment') ||
                body.includes('just a moment') ||
                body.includes('checking your browser') ||
                body.includes('verify you are human') ||
                body.includes('needs to review the security') ||
                html.includes('cf-browser-verification') ||
                html.includes('challenge-platform');
              const hasApp =
                !!document.querySelector('[data-testid="continue-with-email"]') ||
                !!document.querySelector('input[type="email"], input[name="email"], input[type="password"]') ||
                !!document.querySelector('button');
              return { jammed, hasApp, title: document.title || '' };
            }"""
        )
        if not info:
            return False
        # 有登录 UI 则不算整页挑战
        if info.get("hasApp") and not info.get("jammed"):
            return False
        return bool(info.get("jammed"))
    except Exception:
        return False


async def _read_turnstile_token(page) -> str:
    try:
        tok = await page.evaluate(
            """() => {
              try {
                const byInput = String(
                  (document.querySelector('input[name="cf-turnstile-response"]') || {}).value || ''
                ).trim();
                if (byInput) return byInput;
                if (window.turnstile && typeof turnstile.getResponse === 'function') {
                  return String(turnstile.getResponse() || '').trim();
                }
              } catch (e) {}
              return '';
            }"""
        )
        return str(tok or "").strip()
    except Exception:
        return ""


async def _click_turnstile_widget(page, log: LogFn = None) -> None:
    """对齐 turnstile_mint / register_flow：点 Turnstile 中心 + 尝试 iframe 复选框。"""
    # 1) 点 .cf-turnstile / [data-sitekey] 中心
    try:
        box = await page.evaluate(
            """() => {
              const e = document.querySelector(
                '.cf-turnstile, [data-sitekey], iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"]'
              );
              if (!e) return null;
              const r = e.getBoundingClientRect();
              if (!r.width && !r.height) return null;
              return { x: r.left + r.width / 2, y: r.top + Math.min(r.height / 2, 30) };
            }"""
        )
        if box:
            x, y = float(box["x"]), float(box["y"])
            await page.mouse.move(max(0, x - 20), max(0, y - 6))
            await page.mouse.move(x, y, steps=6)
            await page.mouse.down()
            await asyncio.sleep(0.05)
            await page.mouse.up()
            _log(log, "[relogin] 已点击 Turnstile 区域")
    except Exception:
        pass

    # 2) frame 内 checkbox（managed / non-interactive 也可能有）
    try:
        for frame in page.frames:
            url = (frame.url or "").lower()
            if "turnstile" not in url and "challenges.cloudflare" not in url:
                continue
            for sel in (
                'input[type="checkbox"]',
                "label",
                ".ctp-checkbox-label",
                "#challenge-stage",
                "body",
            ):
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() == 0:
                        continue
                    await loc.click(timeout=1500, force=True)
                    _log(log, f"[relogin] 已点 Turnstile frame: {sel}")
                    return
                except Exception:
                    continue
    except Exception:
        pass

    # 3) 兜底：点任意含 turnstile 的节点
    try:
        await page.evaluate(
            """() => {
              const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
                const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
                return String(txt).toLowerCase().includes('turnstile')
                  || String(txt).toLowerCase().includes('challenges.cloudflare');
              });
              if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
            }"""
        )
    except Exception:
        pass


async def _wait_cf_and_turnstile(
    page,
    *,
    timeout: float = 90,
    log: LogFn = None,
    need_token: bool = False,
) -> str:
    """等整页 CF 过掉；可选等到 Turnstile token（长度>=80）。返回 token（可空）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_click = 0.0
    logged_cf = False
    while asyncio.get_event_loop().time() < deadline:
        if await _page_cf_challenge(page):
            if not logged_cf:
                _log(log, "[relogin] 检测到 Cloudflare 人机/挑战页，等待通过…")
                logged_cf = True
            now = asyncio.get_event_loop().time()
            if now - last_click >= 2.5:
                await _click_turnstile_widget(page, log=log)
                last_click = now
            await page.wait_for_timeout(800)
            continue

        tok = await _read_turnstile_token(page)
        if tok and len(tok) >= 80:
            _log(log, f"[relogin] Turnstile 已通过，token 长度={len(tok)}")
            return tok

        # 页面上有 widget 但还没 token
        try:
            present = await page.evaluate(
                """() => !!(
                  document.querySelector('input[name="cf-turnstile-response"]') ||
                  document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]')
                )"""
            )
        except Exception:
            present = False
        if present:
            now = asyncio.get_event_loop().time()
            if now - last_click >= 3.0:
                await _click_turnstile_widget(page, log=log)
                last_click = now
            if need_token:
                await page.wait_for_timeout(700)
                continue
            # 不强制 token 时：widget 存在但尚未出 token 也先返回，让后续再轮询
            await page.wait_for_timeout(500)
            if not need_token:
                return tok or ""
        else:
            # 无挑战无 widget
            if not need_token:
                return ""
            await page.wait_for_timeout(500)

    tok = await _read_turnstile_token(page)
    if need_token and (not tok or len(tok) < 80):
        raise RuntimeError("Cloudflare/Turnstile 超时未通过")
    return tok or ""


def _extension_path() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    for name in ("turnstilePatch", "turnstile_patch"):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            return p
    return ""


async def _login_async(
    email: str,
    password: str,
    proxy: str = "",
    timeout: float = 150,
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

    # 与注册一致：默认屏外+最小化（用户点不出来）；GROK_RELOGIN_VISIBLE=1 才弹窗
    visible = (os.environ.get("GROK_RELOGIN_VISIBLE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # 兼容旧 env：OFFSCREEN=0 也表示可见
    env_off = (os.environ.get("GROK_RELOGIN_OFFSCREEN") or "").strip().lower()
    if env_off in ("0", "false", "no", "off"):
        visible = True

    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--window-size=1000,800",
    ]
    if not headless and not visible:
        # 同 browser_session / turnstile_mint：有界面内核过 CF，但启动即屏外
        args.extend(
            [
                "--window-position=-32000,-32000",
                "--start-minimized",
            ]
        )
    launch: dict = {
        "executable_path": chrome,
        "headless": bool(headless),
        "args": args,
    }
    ext = _extension_path()
    if ext:
        launch["args"] = list(launch["args"]) + [
            f"--disable-extensions-except={ext}",
            f"--load-extension={ext}",
        ]
        _log(log, f"[relogin] 加载扩展: {ext}")
    if proxy:
        launch["proxy"] = {"server": proxy}

    _log(
        log,
        f"[relogin] 打开登录页 chrome={chrome} headless={headless} visible={visible}",
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch)
        try:
            context = await browser.new_context(
                viewport={"width": 1000, "height": 800},
                locale="en-US",
            )
            await context.add_init_script(
                """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                """
            )
            page = await context.new_page()
            if not headless and not visible:
                await _hide_browser_windows(browser, log=log)
            await page.goto(SIGNIN_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            if not headless and not visible:
                await _hide_browser_windows(browser, log=log)

            # 0) 整页 CF 挑战（Just a moment）— 不过就没有 Login with email
            try:
                await _wait_cf_and_turnstile(
                    page, timeout=min(60.0, timeout * 0.4), log=log, need_token=False
                )
            except RuntimeError as exc:
                _log(log, f"[relogin] 进站 CF 未完全通过: {exc}，继续尝试…")

            # 1) 首页只有 OAuth + Login with email
            email_ready = False
            for attempt in range(5):
                if await _page_cf_challenge(page):
                    await _click_turnstile_widget(page, log=log)
                    await page.wait_for_timeout(1500)
                if await _fill_by_selectors(page, _EMAIL_SELECTORS, email):
                    email_ready = True
                    break
                clicked = await _click_continue_with_email(page, log=log)
                if not clicked and attempt == 0:
                    _log(log, "[relogin] 未看到 continue-with-email，继续探测…")
                await page.wait_for_timeout(900)

            if not email_ready:
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
                          cf: ((document.body&&document.body.innerText)||'').slice(0,120),
                        })"""
                    )
                    _log(log, f"[relogin] 页面诊断: {snap}")
                except Exception:
                    pass
                raise RuntimeError(
                    "登录页未找到邮箱输入框（可能仍卡在 Cloudflare，或需先点 Login with email）"
                )

            _log(log, "[relogin] 邮箱已填")

            # 2) 若无密码框，点 Continue/下一步
            pass_ready = await _fill_by_selectors(page, _PASS_SELECTORS, password)
            if not pass_ready:
                for label in (
                    "Continue",
                    "继续",
                    "Next",
                    "下一步",
                    "Log in",
                    "Login",
                    "Sign in",
                    "登录",
                ):
                    try:
                        btn = page.get_by_role("button", name=re.compile(label, re.I))
                        if await btn.count():
                            await btn.first.click(timeout=3000)
                            await page.wait_for_timeout(1200)
                            break
                    except Exception:
                        pass
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
            _log(log, "[relogin] 密码已填，处理 Cloudflare / Turnstile…")

            # 3) 提交前必须等人机（与注册 fill_profile 一致）
            try:
                await _wait_cf_and_turnstile(
                    page, timeout=min(75.0, timeout * 0.5), log=log, need_token=True
                )
            except RuntimeError:
                # managed 模式有时 token 不在 input 里，仍尝试提交
                _log(log, "[relogin] Turnstile token 未读到，尝试点击后提交…")
                await _click_turnstile_widget(page, log=log)
                await page.wait_for_timeout(2000)

            deadline = asyncio.get_event_loop().time() + max(30.0, timeout * 0.5)
            submitted = False
            last_cf_try = 0.0
            while asyncio.get_event_loop().time() < deadline:
                # 若又弹出 CF，先过
                if await _page_cf_challenge(page):
                    await _click_turnstile_widget(page, log=log)
                    await page.wait_for_timeout(1000)

                tok = await _read_turnstile_token(page)
                if tok and len(tok) < 80:
                    now = asyncio.get_event_loop().time()
                    if now - last_cf_try >= 3.0:
                        await _click_turnstile_widget(page, log=log)
                        last_cf_try = now

                # 有 widget 且 token 太短时不要硬点登录
                try:
                    cf_present = await page.evaluate(
                        """() => !!(
                          document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey]')
                        )"""
                    )
                except Exception:
                    cf_present = False
                if cf_present and (not tok or len(tok) < 80):
                    await page.wait_for_timeout(700)
                    # 仍允许偶尔点一次登录（有的 managed 交互后才出 token）
                    if not submitted and asyncio.get_event_loop().time() + 40 < deadline:
                        pass
                    else:
                        if not submitted:
                            pass

                if not submitted or (cf_present and tok and len(tok) >= 80):
                    for label in (
                        "Log in",
                        "Login",
                        "Sign in",
                        "登录",
                        "Continue",
                        "继续",
                    ):
                        try:
                            btn = page.get_by_role(
                                "button", name=re.compile(label, re.I)
                            )
                            if await btn.count():
                                # token 未就绪时少点；已就绪或已等很久再点
                                if (not cf_present) or (tok and len(tok) >= 80) or submitted:
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
                                if (not cf_present) or (tok and len(tok) >= 80):
                                    await sub.click(timeout=2000)
                                    submitted = True
                                    _log(log, "[relogin] 已点击 submit")
                        except Exception:
                            pass
                    if not submitted and (not cf_present or (tok and len(tok) >= 80)):
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
                if any(
                    k in url
                    for k in ("cookie", "set-cookie", "exchange", "sso", "grok.com")
                ):
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
            # 最后诊断
            try:
                title = await page.title()
                _log(log, f"[relogin] 超时诊断 title={title!r} url={page.url}")
            except Exception:
                pass
            raise RuntimeError("登录超时：未拿到 SSO cookie（可能 Cloudflare 未通过）")
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
