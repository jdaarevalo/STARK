"""
One-time Playwright-based auth script for Garmin Connect.

How it works:
  1. Opens a real Chromium browser (no 429 — full browser TLS fingerprint)
  2. Intercepts the login API response to capture the service ticket
  3. Exchanges the ticket for DI tokens via diauth.garmin.com
  4. Saves tokens to ~/.garminconnect/garmin_tokens.json

Run once: uv run python scripts/garmin_auth.py
Tokens are reused automatically by the extractor afterwards.
"""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from garminconnect.client import (
    Client,
    PORTAL_SSO_SERVICE_URL,
    PORTAL_SSO_CLIENT_ID,
)

TOKENSTORE = Path.home() / ".garminconnect"
LOGIN_API_PATH = "/portal/api/login"
SSO_BASE = "https://sso.garmin.com"


async def main():
    load_dotenv()
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        print("ERROR: GARMIN_EMAIL and GARMIN_PASSWORD must be set in .env")
        sys.exit(1)

    ticket: str | None = None
    di_token_data: dict | None = None
    mobile_ticket: str | None = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # Hide webdriver flag so Garmin doesn't detect automation
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        async def on_response(response):
            nonlocal ticket, di_token_data
            url = response.url
            if response.status != 200:
                return
            # Strategy A: ticket in /portal/api/login JSON response
            if not ticket and LOGIN_API_PATH in url:
                try:
                    data = await response.json()
                    t = data.get("serviceTicketId")
                    if t:
                        ticket = t
                        print(f"[✓] Service ticket captured (portal API)")
                except Exception:
                    pass
            # Strategy D: DI token issued directly to the web app
            if not di_token_data and "diauth.garmin.com" in url:
                try:
                    data = await response.json()
                    if data.get("access_token") and data.get("refresh_token"):
                        di_token_data = data
                        print(f"[✓] DI token captured directly")
                except Exception:
                    pass

        async def on_request(request):
            nonlocal ticket
            if ticket:
                return
            url = request.url
            if "ticket=ST-" in url or "ticket=TGT-" in url:
                try:
                    qs = parse_qs(urlparse(url).query)
                    t = qs.get("ticket", [None])[0]
                    if t:
                        ticket = t
                        print(f"[✓] Service ticket captured (SSO redirect)")
                except Exception:
                    pass

        def on_navigate(frame):
            nonlocal ticket
            if ticket or frame != page.main_frame:
                return
            url = frame.url
            if "ticket=ST-" in url or "ticket=TGT-" in url:
                try:
                    qs = parse_qs(urlparse(url).query)
                    t = qs.get("ticket", [None])[0]
                    if t:
                        ticket = t
                        print(f"[✓] Service ticket captured (page URL)")
                except Exception:
                    pass

        page.on("response", on_response)
        page.on("request", on_request)
        page.on("framenavigated", on_navigate)

        print("Opening Garmin Connect...")
        print(">>> Log in manually in the browser window. The script will continue automatically.")
        await page.goto("https://connect.garmin.com")

        # Wait up to 120s for manual login — matches /app/home or /modern/
        await page.wait_for_url(
            lambda url: "/app/home" in url or "/modern/" in url,
            timeout=120000,
        )
        print("[✓] Login successful — fetching mobile service ticket...")
        await asyncio.sleep(2)

        # Use SSO session cookies to get a ticket for the MOBILE service URL.
        # diauth.garmin.com only accepts tickets issued for mobile service URLs.
        from garminconnect.client import MOBILE_SSO_SERVICE_URL, MOBILE_SSO_CLIENT_ID
        mobile_ticket: str | None = None
        sso_cookies = await context.cookies(SSO_BASE)
        if sso_cookies:
            cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in sso_cookies)
            try:
                r = requests.get(
                    f"{SSO_BASE}/sso/login",
                    params={
                        "service": MOBILE_SSO_SERVICE_URL,
                        "clientId": MOBILE_SSO_CLIENT_ID,
                        "locale": "en-US",
                    },
                    headers={
                        "Cookie": cookie_header,
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                    },
                    allow_redirects=False,
                    timeout=15,
                )
                location = r.headers.get("Location", "")
                qs = parse_qs(urlparse(location).query)
                mobile_ticket = qs.get("ticket", [None])[0]
                if mobile_ticket:
                    print(f"[✓] Mobile service ticket: {mobile_ticket[:30]}...")
                else:
                    print(f"  No ticket in redirect. Status={r.status_code} Location={location[:150]}")
            except Exception as e:
                print(f"  Mobile ticket request failed: {e}")
        else:
            print("  No SSO cookies found in browser context.")

        await browser.close()

    client = Client()

    if di_token_data:
        print("Using directly captured DI token...")
        client.di_token = di_token_data["access_token"]
        client.di_refresh_token = di_token_data.get("refresh_token")
        client.di_client_id = client._extract_client_id_from_jwt(client.di_token)
    elif mobile_ticket:
        print("Exchanging mobile service ticket for DI tokens...")
        client._exchange_service_ticket(mobile_ticket, service_url=MOBILE_SSO_SERVICE_URL)
    else:
        print("ERROR: Could not obtain a mobile service ticket.")
        sys.exit(1)

    TOKENSTORE.mkdir(parents=True, exist_ok=True)
    client.dump(str(TOKENSTORE))
    print(f"[✓] Tokens saved to {TOKENSTORE}/garmin_tokens.json")
    print("Done! Run the extractor normally: uv run python src/extractors/garmin.py")


if __name__ == "__main__":
    from src.config.logging_config import setup_logging
    setup_logging()
    asyncio.run(main())
