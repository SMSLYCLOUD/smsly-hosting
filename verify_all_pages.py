# pylint: disable=broad-exception-caught
"""Module for verifying all pages."""
from playwright.sync_api import sync_playwright

def run():
    """Run verification."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Mobile Viewport
        context = browser.new_context(viewport={"width": 375, "height": 812})
        context.add_cookies([{
            "name": "auth_token",
            "value": "dummy",
            "domain": "localhost",
            "path": "/"
        }])
        page = context.new_page()

        pages_to_test = [
            {"path": "/", "name": "dashboard"},
            {"path": "/admin-dashboard", "name": "admin"},
            {"path": "/store", "name": "store"},
            {"path": "/topology", "name": "topology"}
        ]

        for item in pages_to_test:
            print(f"Testing {item['path']}...")
            try:
                page.goto(f"http://localhost:3000{item['path']}")
                page.wait_for_load_state("networkidle")
                page.screenshot(path=f"/tmp/verify_{item['name']}.png", full_page=True)
                print(f"Saved /tmp/verify_{item['name']}.png")
            except Exception as e:
                print(f"Error on {item['path']}: {e}")

        browser.close()

if __name__ == "__main__":
    run()
