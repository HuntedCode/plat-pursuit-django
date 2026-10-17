import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

page_path = Path(sys.argv[1]).resolve()
out = sys.argv[2]
selector = sys.argv[3] if len(sys.argv) > 3 else None
width = int(sys.argv[4]) if len(sys.argv) > 4 else 1440

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
    page.on("console", lambda m: print("console:", m.type, m.text))
    page.on("pageerror", lambda e: print("pageerror:", e))
    page.goto(page_path.as_uri())
    page.wait_for_timeout(400)
    target = page.locator(selector) if selector else page
    if selector:
        target.screenshot(path=out)
    else:
        page.screenshot(path=out, full_page=True)
    browser.close()
print("saved", out)
