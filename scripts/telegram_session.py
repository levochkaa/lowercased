#!/usr/bin/env python3
"""Sign in once for Telegram language-pack synchronization."""

import argparse
import json
from pathlib import Path
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SITE = "https://translations.telegram.org"
STATE_PATH = ROOT / ".telegram-sync" / "session.json"


def page_config(page_html):
    marker = "ajInit("
    start = page_html.find(marker)
    if start < 0:
        raise ValueError("Telegram page does not contain its expected configuration")
    config, _ = json.JSONDecoder().raw_decode(page_html[start + len(marker):])
    return config


def is_authenticated(page_html):
    # Authenticated pages omit `unauth`; they do not send the boolean false.
    return (not page_config(page_html).get("unauth")
            and 'href="/lang/edit/lowercased-en"' in page_html)


def save_session(state, path):
    # Only the translations site session is needed; exclude Telegram login-site cookies.
    state = {
        "cookies": [cookie for cookie in state["cookies"]
                    if cookie["domain"].lstrip(".") == "translations.telegram.org"],
        "origins": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(state, file)
            file.close()
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def login(path=STATE_PATH, channel="chrome", timeout=600):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        options = {"headless": False, "chromium_sandbox": True}
        if channel != "chromium":
            options["channel"] = channel
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        context = playwright.chromium.launch_persistent_context(
            str(path.parent / "browser"), **options
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"{SITE}/lowercased-en/", wait_until="domcontentloaded")
            config = page_config(page.content())
            if config.get("unauth"):
                page.get_by_role("link", name="Login", exact=True).click()
            print("Complete Telegram sign-in in the opened browser window. Waiting…", flush=True)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                response = context.request.get(f"{SITE}/lowercased-en/", timeout=30000)
                if response.ok and is_authenticated(response.text()):
                    save_session(context.storage_state(), path)
                    print("Signed in. Local session saved for future sync runs.", flush=True)
                    return
                time.sleep(3)
            raise TimeoutError("Sign-in timed out. Run the login command again.")
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--channel", default="chrome", help="Installed Chrome channel, or chromium")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    login(args.state, args.channel, args.timeout)


if __name__ == "__main__":
    main()
