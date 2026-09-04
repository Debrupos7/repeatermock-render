"""Flask API that runs RepeaterMock login using Playwright (not nodriver)."""
import asyncio
import json
import os
import sys
import threading
import time
import warnings
import random
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)
runs = {}
OUTPUT_DIR = Path("/data/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})


@app.route("/")
def index():
    return jsonify({
        "service": "RepeaterMock Login API (Playwright)",
        "endpoints": {
            "GET /health": "Health check",
            "POST /login": "Trigger login",
            "GET /status/<run_id>": "Check status + logs",
            "GET /cookies": "Get latest cookies",
            "GET /debug": "Debug Chrome",
        },
    })


@app.route("/debug")
def debug():
    """Test Playwright Chrome."""
    import subprocess
    results = {}
    results["display"] = os.environ.get("DISPLAY", "not set")
    try:
        r = subprocess.run(["whoami"], capture_output=True, text=True, timeout=5)
        results["user"] = r.stdout.strip()
    except: pass
    
    # Find Chrome binary
    import glob
    chrome_paths = glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")
    results["chrome_paths"] = chrome_paths
    
    if chrome_paths:
        chrome = chrome_paths[0]
        try:
            r = subprocess.run([chrome, "--version"], capture_output=True, text=True, timeout=5)
            results["chrome_version"] = r.stdout.strip()
        except Exception as e:
            results["chrome_version"] = f"ERROR: {e}"
        
        # Test headless
        try:
            r = subprocess.run(
                [chrome, "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                 "--headless=new", "--dump-dom", "about:blank"],
                capture_output=True, text=True, timeout=15
            )
            results["chrome_headless"] = {
                "returncode": r.returncode,
                "stdout_len": len(r.stdout),
                "stdout_preview": r.stdout[:200],
                "stderr_preview": r.stderr[:300],
            }
        except Exception as e:
            results["chrome_headless"] = f"ERROR: {e}"
        
        # Test Playwright
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    channel="chromium",
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page()
                page.goto("about:blank")
                title = page.title()
                browser.close()
                results["playwright_test"] = f"OK, title='{title}'"
        except Exception as e:
            results["playwright_test"] = f"ERROR: {e}"
    
    return jsonify(results)


def run_login_async(run_id, email, password):
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
        log("Starting login (Playwright approach)…")
        from playwright.async_api import async_playwright
        import httpx

        URL = "https://repeatermock.com/login"
        SITEKEY = "0x4AAAAAADixxaKQ-LspbGkf"

        async def solve_and_login():
            log("STEP 1: Launch Chromium via Playwright")
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    channel="chromium",
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                log("✅ Browser launched")

                ctx = await browser.new_context(
                    viewport={"width": 1366, "height": 900},
                    locale="en-US",
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                )
                # Block swiper.js (DisableDevtool)
                await ctx.route("**/swiper.js", lambda route: route.abort())
                page = await ctx.new_page()
                log("✅ Context created, swiper.js blocked")

                log("STEP 2: Navigate to login page")
                await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_selector("input[type=email]", timeout=15000)
                    log("✅ Login form visible!")
                except:
                    log("❌ Form not visible", "ERROR")
                    await browser.close()
                    return {"success": False, "error": "form not visible"}

                await page.wait_for_timeout(3000)
                ts_type = await page.evaluate("() => typeof window.turnstile")
                log(f"window.turnstile type: {ts_type}")

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
                await page.wait_for_timeout(5000)
                log("Widget injected, waiting for token…")

                log("STEP 4: Wait for Turnstile token")
                token = await page.evaluate("""
                    () => {
                        if (window._tsToken) return window._tsToken;
                        const inp = document.querySelector('#_ts_box [name="cf-turnstile-response"]');
                        return (inp && inp.value) ? inp.value : null;
                    }
                """)

                if not token:
                    log("Not auto-solved, clicking widget…")
                    for attempt in range(10):
                        token = await page.evaluate("""
                            () => {
                                if (window._tsToken) return window._tsToken;
                                const inp = document.querySelector('#_ts_box [name="cf-turnstile-response"]');
                                return (inp && inp.value) ? inp.value : null;
                            }
                        """)
                        if token:
                            log(f"✅ Solved at attempt {attempt+1}!")
                            break

                        rect = await page.evaluate("""
                            () => {
                                for (const f of document.querySelectorAll('iframe')) {
                                    const src = f.src || '';
                                    if (!src.includes('challenges.cloudflare.com')) continue;
                                    const r = f.getBoundingClientRect();
                                    if (r.width > 50 && r.height > 20)
                                        return {x: r.x, y: r.y, w: r.width, h: r.height};
                                }
                                return null;
                            }
                        """)
                        if rect:
                            cx = rect["x"] + 28 + random.uniform(-3, 3)
                            cy = rect["y"] + rect["h"] / 2 + random.uniform(-3, 3)
                            log(f"  Attempt {attempt+1}: iframe ({cx:.0f},{cy:.0f})")
                        else:
                            cx = 48 + random.uniform(-3, 3)
                            cy = 52 + random.uniform(-3, 3)
                            log(f"  Attempt {attempt+1}: fixed ({cx:.0f},{cy:.0f})")

                        await page.mouse.move(cx - 80, cy - 20)
                        await page.wait_for_timeout(200)
                        await page.mouse.move(cx, cy)
                        await page.wait_for_timeout(100)
                        await page.mouse.click(cx, cy)
                        await page.wait_for_timeout(4000)

                if not token:
                    log("❌ Failed to solve Turnstile", "ERROR")
                    await browser.close()
                    return {"success": False, "error": "turnstile not solved"}

                log(f"✅ Token obtained! Length: {len(token)}")

                log("STEP 5: Get cookies + submit login")
                cookies = await ctx.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                log(f"Got {len(cookies)} cookies")

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

                log(f"API status: {api_status}")
                log(f"API response: {json.dumps(api_json, ensure_ascii=False)[:300]}")

                if api_status != 200 or not api_json.get("success"):
                    await browser.close()
                    return {"success": False, "error": f"API failed: {api_json}"}

                user = api_json.get("user", {})
                log(f"✅ LOGIN SUCCESSFUL!")
                log(f"User: {user.get('name')} ({user.get('email')})")
                log(f"Plan: {user.get('plan')}")

                # Build cookie list
                cookie_list = []
                for c in cookies:
                    cookie_list.append({
                        "name": c["name"], "value": c["value"], "domain": c["domain"],
                        "path": c.get("path", "/"), "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", False), "sameSite": c.get("sameSite", "Lax"),
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
                log("STEP 6: Browse 5 pages")
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
                            log(f"  {name}: {r.status_code} len={len(r.text):,} auth={me_ok}")
                            browse_results.append({"name": name, "status": r.status_code, "auth": me_ok})
                        except Exception as e:
                            log(f"  {name}: ERROR {e}", "ERROR")

                await browser.close()
                return {"success": True, "user": user, "browse_results": browse_results, "cookies": cookie_list}

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = asyncio.run(solve_and_login())

        runs[run_id]["status"] = "completed"
        runs[run_id]["result"] = result
        if result.get("success"):
            log("🎉 ALL DONE!")
        else:
            log(f"❌ FAILED: {result.get('error')}", "ERROR")

    except Exception as e:
        runs[run_id]["status"] = "failed"
        runs[run_id]["error"] = str(e)
        log(f"❌ EXCEPTION: {e}", "ERROR")
        import traceback
        runs[run_id]["logs"].extend(traceback.format_exc().split("\n"))


@app.route("/login", methods=["POST"])
def login():
    email = request.json.get("email", os.environ.get("RM_EMAIL", "spellingbeeanswers@gmail.com"))
    password = request.json.get("password", os.environ.get("RM_PASSWORD", "BloggingJi@7"))
    run_id = f"run_{int(time.time())}"
    runs[run_id] = {"status": "queued", "logs": []}
    thread = threading.Thread(target=run_login_async, args=(run_id, email, password))
    thread.daemon = True
    thread.start()
    return jsonify({"run_id": run_id, "status": "started"})


@app.route("/status/<run_id>")
def status(run_id):
    if run_id not in runs:
        return jsonify({"error": "run not found"}), 404
    return jsonify(runs[run_id])


@app.route("/cookies")
def cookies():
    f = OUTPUT_DIR / "cookies.json"
    if not f.exists():
        return jsonify({"error": "no cookies yet"}), 404
    with open(f) as fh:
        return jsonify(json.load(fh))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
