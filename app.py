#!/usr/bin/env python3
"""
RepeaterMock Login via NoCaptchaAI API — 100% free, no browser, no local machine.

Uses NoCaptchaAI's free tier (6,000 solves, no card required) to solve the
Cloudflare Turnstile challenge via API. The token is then submitted to
RepeaterMock's login API along with email + password.

This approach works from ANY IP (datacenter, residential, etc.) because
NoCaptchaAI solves the Turnstile on THEIR servers with THEIR residential
IPs — we just get the token and submit it.

Flow:
1. POST /createTask to NoCaptchaAI with TurnstileTaskProxyLess
2. Poll /getTaskResult until the token is ready
3. POST the token to RepeaterMock's /auth/login API
4. Get cookies (accessToken, refreshToken)
5. Browse 5-6 pages to verify the session

Requirements:
    pip install httpx
    export NOCAPTCHA_API_KEY="nocap_..."

Usage:
    python repeatermock_login_nocaptcha.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Configuration
NOCAPTCHA_API_KEY = os.environ.get("NOCAPTCHA_API_KEY", "")
NOCAPTCHA_BASE = "https://api.nocaptchaai.com"

LOGIN_URL = "https://repeatermock.com/login"
LOGIN_API = "https://api.repeatermock.com/auth/login"
ME_API = "https://api.repeatermock.com/auth/me"
REFRESH_API = "https://api.repeatermock.com/auth/refresh"
EMAIL = os.environ.get("RM_EMAIL", "spellingbeeanswers@gmail.com")
PASSWORD = os.environ.get("RM_PASSWORD", "BloggingJi@7")
SITEKEY = "0x4AAAAAADixxaKQ-LspbGkf"

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/home/z/my-project/download"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_DIR / "nocaptcha_login.log"

BROWSE_PAGES = [
    ("Dashboard", "https://repeatermock.com/dashboard"),
    ("Test Series", "https://repeatermock.com/test-series"),
    ("Pricing", "https://repeatermock.com/pricing"),
    ("About", "https://repeatermock.com/about"),
    ("Blog", "https://repeatermock.com/blog"),
]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def section(title):
    log("")
    log("=" * 70)
    log(f"  {title}")
    log("=" * 70)


async def check_balance(cli):
    """Check NoCaptchaAI account balance."""
    log("Checking NoCaptchaAI balance…")
    r = await cli.post(f"{NOCAPTCHA_BASE}/getBalance",
                       json={"clientKey": NOCAPTCHA_API_KEY})
    data = r.json()
    log(f"  Balance response: {json.dumps(data, ensure_ascii=False)[:300]}")
    return data


async def solve_turnstile(cli):
    """Solve Cloudflare Turnstile via NoCaptchaAI API."""
    section("STEP 1: Create Turnstile task on NoCaptchaAI")
    log(f"  websiteURL: {LOGIN_URL}")
    log(f"  websiteKey: {SITEKEY}")

    r = await cli.post(f"{NOCAPTCHA_BASE}/createTask", json={
        "clientKey": NOCAPTCHA_API_KEY,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": LOGIN_URL,
            "websiteKey": SITEKEY,
        }
    })
    data = r.json()
    log(f"  createTask response: {json.dumps(data, ensure_ascii=False)[:300]}")

    if data.get("errorId") and data.get("errorId") != 0:
        log(f"  ❌ createTask failed: {data.get('errorDescription', data)}", "ERROR")
        return None

    task_id = data.get("taskId")
    if not task_id:
        log(f"  ❌ No taskId in response", "ERROR")
        return None

    log(f"  ✅ Task created: {task_id}")

    section("STEP 2: Poll for Turnstile token")
    start = time.time()
    for attempt in range(60):  # up to 120s
        await asyncio.sleep(2)
        r = await cli.post(f"{NOCAPTCHA_BASE}/getTaskResult", json={
            "clientKey": NOCAPTCHA_API_KEY,
            "taskId": task_id,
        })
        data = r.json()
        status = data.get("status", "?")
        elapsed = time.time() - start

        if status == "ready":
            token = data.get("solution", {}).get("token", "")
            log(f"  ✅ Token ready at {elapsed:.1f}s! Length: {len(token)}")
            log(f"  Token (first 30): {token[:30]}…")
            return token
        elif status == "failed" or data.get("errorId", 0) != 0:
            log(f"  ❌ Task failed at {elapsed:.1f}s: {data}", "ERROR")
            return None
        else:
            if attempt % 5 == 0:
                log(f"  [{elapsed:.0f}s] status={status}…")

    log("  ❌ Timed out waiting for token (120s)", "ERROR")
    return None


async def submit_login(cli, token):
    """Submit the login form with the solved Turnstile token."""
    section("STEP 3: Submit login to RepeaterMock API")
    log(f"  POST {LOGIN_API}")
    log(f"  email: {EMAIL}")
    log(f"  turnstileToken: {token[:30]}…")

    r = await cli.post(LOGIN_API, json={
        "email": EMAIL,
        "password": PASSWORD,
        "turnstileToken": token,
    }, headers={
        "Content-Type": "application/json",
        "Origin": "https://repeatermock.com",
        "Referer": "https://repeatermock.com/login",
        "User-Agent": UA,
    })

    log(f"  Status: {r.status_code}")
    try:
        data = r.json()
    except:
        data = {"raw": r.text[:500]}
    log(f"  Response: {json.dumps(data, ensure_ascii=False)[:400]}")

    # Capture Set-Cookie headers
    set_cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else []
    log(f"  Set-Cookie headers: {len(set_cookies)}")
    for sc in set_cookies:
        log(f"    {sc[:120]}…")

    if r.status_code != 200 or not data.get("success"):
        log(f"  ❌ Login failed", "ERROR")
        return None, None

    user = data.get("user", {})
    log(f"  ✅ LOGIN SUCCESSFUL!")
    log(f"  User: {user.get('name')}")
    log(f"  Email: {user.get('email')}")
    log(f"  ID: {user.get('id')}")
    log(f"  Plan: {user.get('plan')}")

    return user, set_cookies


async def browse_pages(cli, cookie_header):
    """Browse 5-6 pages using the session cookies."""
    section("STEP 5: Browse pages with session cookies")
    browse_results = []
    for name, url in BROWSE_PAGES:
        log(f"  → {name}: {url}")
        try:
            r = await cli.get(url, headers={
                "User-Agent": UA,
                "Cookie": cookie_header,
                "Referer": "https://repeatermock.com/",
            }, follow_redirects=True)
            title = ""
            if "<title>" in r.text:
                title = r.text.split("<title>")[1].split("</title>")[0].strip()[:60]
            log(f"    Status: {r.status_code} | Length: {len(r.text):,} | Title: '{title}'")

            # Verify auth with /auth/me
            me_r = await cli.get(ME_API, headers={"Cookie": cookie_header})
            try:
                me_ok = me_r.json().get("success", False)
            except:
                me_ok = False
            log(f"    /auth/me: {me_r.status_code} success={me_ok}")

            browse_results.append({
                "name": name, "url": url, "status": r.status_code,
                "length": len(r.text), "title": title, "auth_verified": me_ok,
            })
        except Exception as e:
            log(f"    ❌ Error: {e}", "ERROR")
            browse_results.append({"name": name, "url": url, "error": str(e)})
    return browse_results


async def main():
    section("RepeaterMock Login via NoCaptchaAI (free, no browser, no local)")
    log(f"Email: {EMAIL}")
    log(f"API Key: {NOCAPTCHA_API_KEY[:15]}…" if NOCAPTCHA_API_KEY else "API Key: NOT SET")
    log(f"Python: {sys.version.split()[0]}")

    if not NOCAPTCHA_API_KEY:
        log("❌ NOCAPTCHA_API_KEY environment variable not set!", "ERROR")
        log("   Get a free API key at https://nocaptchaai.com (6,000 free solves, no card)", "ERROR")
        return

    async with httpx.AsyncClient(timeout=60.0) as cli:
        # Check balance
        balance = await check_balance(cli)
        if balance.get("errorId", 0) != 0:
            log(f"❌ API key invalid: {balance}", "ERROR")
            return

        # Solve Turnstile
        token = await solve_turnstile(cli)
        if not token:
            log("❌ Failed to solve Turnstile", "ERROR")
            return

        # Submit login
        user, set_cookies = await submit_login(cli, token)
        if not user:
            return

        # Build cookie list from Set-Cookie headers
        section("STEP 4: Save cookies")
        cookie_list = []
        for sc in set_cookies:
            parts = sc.split(";")[0].split("=", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                value = parts[1].strip()
                # Parse attributes
                attrs = sc.split(";")[1:]
                http_only = any("httponly" in a.lower() for a in attrs)
                secure = any("secure" in a.lower() for a in attrs)
                domain = ".repeatermock.com"
                for a in attrs:
                    if a.strip().lower().startswith("domain="):
                        domain = a.split("=", 1)[1].strip()
                cookie_list.append({
                    "name": name, "value": value, "domain": domain,
                    "path": "/", "httpOnly": http_only, "secure": secure,
                    "sameSite": "Lax",
                })

        # Also get cookies from the response (client-side cookies)
        # httpx doesn't auto-store cookies from Set-Cookie unless we use a cookie jar
        # Let's also make a request to the login page to get cf_clearance etc.
        try:
            r = await cli.get(LOGIN_URL, headers={"User-Agent": UA})
            for c in r.cookies.jar:
                if not any(existing["name"] == c.name for existing in cookie_list):
                    cookie_list.append({
                        "name": c.name, "value": c.value, "domain": c.domain or ".repeatermock.com",
                        "path": c.path or "/", "httpOnly": False, "secure": c.secure,
                        "sameSite": "Lax",
                    })
        except:
            pass

        # Build cookie header for browsing
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookie_list)
        log(f"  Total cookies: {len(cookie_list)}")
        for c in cookie_list:
            log(f"    - {c['name']}={c['value'][:50]}… (domain={c['domain']})")

        # Save cookies
        cookies_data = {
            "cookies": cookie_list,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "email": EMAIL,
            "user": user,
            "method": "nocaptchaai_api",
        }
        with open(OUTPUT_DIR / "cookies.json", "w") as f:
            json.dump(cookies_data, f, indent=2, ensure_ascii=False)

        with open(OUTPUT_DIR / "cookies.txt", "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in cookie_list:
                d = c["domain"]
                f.write(f"{d}\t{'TRUE' if d.startswith('.') else 'FALSE'}\t{c['path']}\t"
                        f"{'TRUE' if c['secure'] else 'FALSE'}\t0\t{c['name']}\t{c['value']}\n")

        # Save auth tokens separately
        access_token = next((c["value"] for c in cookie_list if c["name"] == "accessToken"), "")
        refresh_token = next((c["value"] for c in cookie_list if c["name"] == "refreshToken"), "")
        with open(OUTPUT_DIR / "auth_tokens.json", "w") as f:
            json.dump({
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "email": EMAIL,
            }, f, indent=2)

        log(f"\n  Saved: cookies.json, cookies.txt, auth_tokens.json")
        log(f"  accessToken (first 50): {access_token[:50]}…")

        # Browse pages
        browse_results = await browse_pages(cli, cookie_header)

        # Summary
        section("FINAL SUMMARY")
        log(f"Login: ✅ SUCCESS (via NoCaptchaAI API — no browser needed!)")
        log(f"User:  {user.get('name')} ({user.get('email')})")
        log(f"Plan:  {user.get('plan')}")
        auth_ok = sum(1 for r in browse_results if r.get("auth_verified"))
        log(f"Pages browsed: {len(browse_results)}/{len(BROWSE_PAGES)}")
        log(f"Auth verified: {auth_ok}/{len(browse_results)}")
        for r in browse_results:
            icon = "✅" if r.get("status") == 200 else "❌"
            auth = "🔒" if r.get("auth_verified") else "🔓"
            log(f"  {icon} {auth} {r['name']:<15} status={r.get('status','ERR')} len={r.get('length',0):>7,}")
        log(f"\n🎉 ALL DONE — no browser, no local machine, no proxy needed!")



# ===== Flask API wrapper for HTTP access =====

app = Flask(__name__)
@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

@app.route("/")
def index():
    return jsonify({
        "service": "RepeaterMock Login API (NoCaptchaAI — no browser needed)",
        "endpoints": {
            "GET /health": "Health check",
            "POST /login": "Trigger login via NoCaptchaAI API",
            "GET /status/<run_id>": "Check run status + logs",
            "GET /cookies": "Get latest cookies",
        },
    })

@app.route("/login", methods=["POST"])
def login_endpoint():
    data = request.get_json(silent=True) or {}
    email = data.get("email", EMAIL)
    password = data.get("password", PASSWORD)
    api_key = data.get("apikey", NOCAPTCHA_API_KEY)
    run_id = f"run_{int(time.time())}"
    runs[run_id] = {"status": "queued", "logs": [], "result": None}
    
    def run_bg():
        global _current_run_id, NOCAPTCHA_API_KEY
        _current_run_id = run_id
        runs[run_id]["status"] = "running"
        old_key = NOCAPTCHA_API_KEY
        if api_key:
            NOCAPTCHA_API_KEY = api_key
        try:
            asyncio.run(main())
            runs[run_id]["status"] = "completed"
        except Exception as e:
            runs[run_id]["status"] = "failed"
            runs[run_id]["error"] = str(e)
            import traceback
            runs[run_id]["logs"].append(traceback.format_exc())
        finally:
            NOCAPTCHA_API_KEY = old_key
            _current_run_id = None
    
    import threading
    t = threading.Thread(target=run_bg)
    t.daemon = True
    t.start()
    return jsonify({"run_id": run_id, "status": "started", "message": "Poll /status/<run_id> for logs"})

@app.route("/status/<run_id>")
def status_endpoint(run_id):
    if run_id not in runs:
        return jsonify({"error": "run not found"}), 404
    return jsonify(runs[run_id])

@app.route("/cookies")
def cookies_endpoint():
    f = OUTPUT_DIR / "cookies.json"
    if not f.exists():
        return jsonify({"error": "no cookies yet"}), 404
    with open(f) as fh:
        return jsonify(json.load(fh))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
