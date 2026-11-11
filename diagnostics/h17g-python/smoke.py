# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Smoke both browser executables after the original Docs installer succeeds."""

import importlib.metadata
import sys

from playwright.sync_api import sync_playwright

if importlib.metadata.version("playwright") != "1.64.1":
    sys.exit("Unexpected Playwright version")
name = sys.argv[1]
if name not in {"chromium", "webkit"}:
    sys.exit("Unexpected browser")
with sync_playwright() as playwright:
    browser = getattr(playwright, name).launch(headless=True, timeout=15000)
    try:
        page = browser.new_page()
        page.set_content("<title>h17g</title><p>ready</p>", timeout=5000)
        if page.locator("p").inner_text(timeout=5000) != "ready":
            sys.exit("Browser content mismatch")
        sys.stdout.write(f"{name} PASS\n")
    finally:
        browser.close()
