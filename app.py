"""Flask API that runs the RepeaterMock login script on demand."""
import asyncio
import json
import os
import sys
import threading
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

# In-memory state for tracking runs
runs = {}

OUTPUT_DIR = Path("/data/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})


@app.route("/")
def index():
    return jsonify({
        "service": "RepeaterMock Login API",
        "endpoints": {
            "GET /health": "Health check",
            "POST /login": "Trigger login (async, returns run_id)",
            "GET /status/<run_id>": "Check run status + logs",
            "GET /cookies": "Get latest cookies if login succeeded",
        },
    })


def run_login_async(run_id, email, password):
    """Run the login script in a background thread."""
    runs[run_id] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "logs": [],
        "result": None,
    }

    def log(msg, level="INFO"):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        runs[run_id]["logs"].append(line)
        print(line, flush=True)

    try:
        log("Starting login process…")

        # Import nodriver (Chrome must be installed in the Docker image)
        import nodriver as uc
        import httpx
        import random

        URL = "https://repeatermock.com/login"
        SITEKEY = "0x4AAAAAADixxaKQ-LspbGkf"
        CHROME_PATH = "/usr/bin/google-chrome-stable"
        PROFILE_DIR = "/tmp/ts_profile"

        async def solve_and_login():
            log("STEP 1: Launch Chrome via nodriver")
            browser = None
            for attempt in range(5):
                try:
                    if attempt > 0:
                        import shutil
                        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
                        await asyncio.sleep(2)
                    log(f"  Attempt {attempt+1}/5…")
                    browser = await uc.start(
                        browser_executable_path=CHROME_PATH,
                        headless=False,
                        user_data_dir=PROFILE_DIR,
                        sandbox=False,
                        browser_args=[
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                            "--disable-software-rasterizer",
                            "--no-first-run",
                            "--no-default-browser-check",
                            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        ],
                    )
                    log("  ✅ Browser launched!")
                    break
                except Exception as e:
                    log(f"  Attempt {attempt+1} failed: {e}", "WARN")
                    if browser:
                        try: browser.stop()
                        except: pass
                        browser = None

            if not browser:
                raise Exception("Failed to launch browser after 5 attempts")

            try:
                log("STEP 2: Navigate to login page")
                page = await browser.get(URL)
                await asyncio.sleep(8)

                # Check page state
                raw = await page.evaluate("""
                    (() => ({
                        url: window.location.href,
                        title: document.title,
                        hasEmailInput: !!document.querySelector('input[type=email]'),
                        hasForm: !!document.querySelector('form'),
                    }))()
                """)
                page_info = parse_eval(raw)
                log(f"  Page: {page_info}")

                if not page_info or not page_info.get("hasEmailInput"):
                    raise Exception("Login form not visible on page")

                log("STEP 3: Inject Turnstile widget")
                await page.evaluate(f"""
                    (() => {{
                        if (document.getElementById('_ts_box')) return;
                        window._tsToken = null;
                        const wrap = document.createElement('div');
                        wrap.id = '_ts_box';
                        wrap.style = 'position:fixed;top:20px;left:20px;z-index:2147483647;background:white;';
                        document.body.appendChild(wrap);
                        window._tsLoad = function () {{
                            turnstile.render('#_ts_box', {{
                                sitekey: '{SITEKEY}',
                                callback: function(token) {{ window._tsToken = token; }}
                            }});
                        }};
                        const s = document.createElement('script');
                        s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=_tsLoad&render=explicit';
                        s.async = true;
                        document.head.appendChild(s);
                    }})()
                """)
                await asyncio.sleep(5)

                log("STEP 4: Wait for Turnstile token")
                token = parse_eval(await page.evaluate("""
                    (() => {
                        if (window._tsToken) return window._tsToken;
                        const inp = document.querySelector('#_ts_box [name="cf-turnstile-response"]');
                        return (inp && inp.value) ? inp.value : null;
                    })()
                """))

                if not token:
                    log("  Not auto-solved, clicking widget…")
                    for attempt in range(8):
                        token = parse_eval(await page.evaluate("""
                            (() => {
                                if (window._tsToken) return window._tsToken;
                                const inp = document.querySelector('#_ts_box [name="cf-turnstile-response"]');
                                return (inp && inp.value) ? inp.value : null;
                            })()
                        """))
                        if token:
                            log(f"  ✅ Solved at attempt {attempt+1}!")
                            break

                        rect = parse_eval(await page.evaluate("""
                            (() => {
                                for (const f of document.querySelectorAll('iframe')) {
                                    const src = f.src || '';
                                    if (!src.includes('challenges.cloudflare.com')) continue;
                                    const r = f.getBoundingClientRect();
                                    if (r.width > 50 && r.height > 20)
                                        return {x: r.x, y: r.y, w: r.width, h: r.height};
                                }
                                return null;
                            })()
                        """))
                        if rect:
                            cx = rect["x"] + 28 + random.uniform(-3, 3)
                            cy = rect["y"] + rect["h"] / 2 + random.uniform(-3, 3)
                            log(f"  Attempt {attempt+1}: clicking iframe ({cx:.0f},{cy:.0f})")
                        else:
                            cx = 48 + random.uniform(-3, 3)
                            cy = 52 + random.uniform(-3, 3)
                            log(f"  Attempt {attempt+1}: clicking fixed ({cx:.0f},{cy:.0f})")

                        await page.mouse_move(cx - 80, cy - 20)
                        await asyncio.sleep(0.2)
                        await page.mouse_move(cx, cy)
                        await asyncio.sleep(0.1)
                        await page.mouse_click(cx, cy)
                        await asyncio.sleep(4)

                if not token:
                    raise Exception("Failed to solve Turnstile after 8 attempts")

                log(f"  ✅ Token obtained! Length: {len(token)}")

                log("STEP 5: Extract cookies + submit login")
                browser_cookies = await browser.cookies.get_all()
                cookie_str = "; ".join(f"{c.name}={c.value}" for c in browser_cookies)
                log(f"  Got {len(browser_cookies)} cookies")

                async with httpx.AsyncClient(timeout=30.0) as cli:
                    r = await cli.post(
                        "https://api.repeatermock.com/auth/login",
                        json={"email": email, "password": password, "turnstileToken": token},
                        headers={
                            "Content-Type": "application/json",
                            "Origin": "https://repeatermock.com",
                            "Referer": "https://repeatermock.com/login",
                            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                            "Cookie": cookie_str,
                        },
                    )
                    api_status = r.status_code
                    api_json = r.json() if "json" in r.headers.get("content-type", "") else {"raw": r.text[:500]}
                    set_cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else []

                log(f"  API status: {api_status}")
                log(f"  API response: {json.dumps(api_json, ensure_ascii=False)[:300]}")

                if api_status != 200 or not api_json.get("success"):
                    raise Exception(f"Login API failed: {api_json}")

                user = api_json.get("user", {})
                log(f"  ✅ LOGIN SUCCESSFUL!")
                log(f"  User: {user.get('name')} ({user.get('email')})")
                log(f"  Plan: {user.get('plan')}")

                # Build cookie list
                cookie_list = []
                for c in browser_cookies:
                    ss = c.same_site if hasattr(c, "same_site") else "Lax"
                    if hasattr(ss, "value"): ss = ss.value
                    elif not isinstance(ss, str): ss = str(ss)
                    cookie_list.append({
                        "name": c.name, "value": c.value, "domain": c.domain,
                        "path": c.path, "httpOnly": c.http_only if hasattr(c, "http_only") else False,
                        "secure": c.secure if hasattr(c, "secure") else False, "sameSite": ss,
                    })
                for sc in set_cookies:
                    parts = sc.split(";")[0].split("=", 1)
                    if len(parts) == 2 and not any(c["name"] == parts[0].strip() for c in cookie_list):
                        cookie_list.append({
                            "name": parts[0].strip(), "value": parts[1].strip(),
                            "domain": ".repeatermock.com", "path": "/",
                            "httpOnly": True, "secure": True, "sameSite": "Lax",
                        })

                # Save cookies
                with open(OUTPUT_DIR / "cookies.json", "w") as f:
                    json.dump({"cookies": cookie_list, "timestamp": datetime.now(timezone.utc).isoformat(),
                               "email": email, "user": user}, f, indent=2)
                with open(OUTPUT_DIR / "cookies.txt", "w") as f:
                    f.write("# Netscape HTTP Cookie File\n")
                    for c in cookie_list:
                        d = c["domain"]
                        f.write(f"{d}\t{'TRUE' if d.startswith('.') else 'FALSE'}\t/\t"
                                f"{'TRUE' if c.get('secure') else 'FALSE'}\t0\t{c['name']}\t{c['value']}\n")

                # Browse 5 pages
                log("STEP 6: Browse 5 pages with session cookies")
                auth_cookies = {c["name"]: c["value"] for c in cookie_list}
                cookie_header = "; ".join(f"{k}={v}" for k, v in auth_cookies.items())
                browse_results = []
                pages = [
                    ("Dashboard", "https://repeatermock.com/dashboard"),
                    ("Test Series", "https://repeatermock.com/test-series"),
                    ("Pricing", "https://repeatermock.com/pricing"),
                    ("About", "https://repeatermock.com/about"),
                    ("Blog", "https://repeatermock.com/blog"),
                ]
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
                    for name, url in pages:
                        try:
                            r = await cli.get(url, headers={
                                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                "Cookie": cookie_header, "Referer": "https://repeatermock.com/",
                            })
                            title = r.text.split("<title>")[1].split("</title>")[0].strip()[:60] if "<title>" in r.text else ""
                            me = await cli.get("https://api.repeatermock.com/auth/me", headers={"Cookie": cookie_header})
                            me_ok = me.json().get("success", False) if "json" in me.headers.get("content-type","") else False
                            log(f"  {name}: {r.status_code} len={len(r.text):,} title='{title}' auth={me_ok}")
                            browse_results.append({"name": name, "status": r.status_code, "len": len(r.text), "auth": me_ok})
                        except Exception as e:
                            log(f"  {name}: ERROR {e}", "ERROR")
                            browse_results.append({"name": name, "error": str(e)})

                return {
                    "success": True,
                    "user": user,
                    "cookies": cookie_list,
                    "browse_results": browse_results,
                }
            finally:
                browser.stop()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = asyncio.run(solve_and_login())

        runs[run_id]["status"] = "completed"
        runs[run_id]["result"] = result
        runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        log("🎉 ALL DONE!")

    except Exception as e:
        runs[run_id]["status"] = "failed"
        runs[run_id]["error"] = str(e)
        runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        log(f"❌ FAILED: {e}", "ERROR")
        import traceback
        runs[run_id]["logs"].extend(traceback.format_exc().split("\n"))


def parse_eval(result):
    """Parse nodriver's CDP evaluate return format."""
    if result is None:
        return None
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, list):
        if len(result) > 0 and isinstance(result[0], list) and len(result[0]) == 2:
            d = {}
            for item in result:
                if isinstance(item, list) and len(item) == 2:
                    key, val = item
                    if isinstance(val, dict) and 'value' in val:
                        d[key] = val['value']
                    else:
                        d[key] = parse_eval(val)
            return d
        return [parse_eval(item) if isinstance(item, dict) and 'value' in item else parse_eval(item) for item in result]
    if isinstance(result, dict):
        if 'value' in result and 'type' in result:
            return result['value']
        return {k: parse_eval(v) for k, v in result.items()}
    return result


@app.route("/login", methods=["POST"])
def login():
    email = request.json.get("email", os.environ.get("RM_EMAIL", "spellingbeeanswers@gmail.com"))
    password = request.json.get("password", os.environ.get("RM_PASSWORD", "BloggingJi@7"))
    run_id = f"run_{int(time.time())}"
    runs[run_id] = {"status": "queued", "logs": []}

    thread = threading.Thread(target=run_login_async, args=(run_id, email, password))
    thread.daemon = True
    thread.start()

    return jsonify({"run_id": run_id, "status": "started", "message": "Login triggered. Poll /status/<run_id>"})


@app.route("/status/<run_id>")
def status(run_id):
    if run_id not in runs:
        return jsonify({"error": "run not found"}), 404
    return jsonify(runs[run_id])


@app.route("/cookies")
def cookies():
    cookies_file = OUTPUT_DIR / "cookies.json"
    if not cookies_file.exists():
        return jsonify({"error": "no cookies yet — trigger /login first"}), 404
    with open(cookies_file) as f:
        return jsonify(json.load(f))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
