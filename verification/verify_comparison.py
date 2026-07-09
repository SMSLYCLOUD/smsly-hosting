
import os
import time

from playwright.sync_api import sync_playwright


def verify_comparison_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        # 1. Visit Landing Page
        print("Visiting Landing Page...")
        page.goto("http://localhost:3000")
        time.sleep(2) # Wait for hydration

        # Screenshot landing page footer/link
        os.makedirs("verification", exist_ok=True)
        page.screenshot(path="verification/landing_page.png")
        print("Captured landing_page.png")

        # 2. Click Compare link
        # It might be in the footer
        try:
            link = page.get_by_role("link", name="Compare vs Railway/Vercel")
            if link.count() > 0:
                print("Found Compare link via role")
                link.first.click()
            else:
                print("Trying to find link by text...")
                page.get_by_text("Compare vs Railway/Vercel").click()
        except Exception as e:
            print(f"Could not click link: {e}")
            print("Navigating directly to /compare")
            page.goto("http://localhost:3000/compare")

        # 3. Verify Comparison Page
        print("Waiting for comparison page...")
        page.wait_for_url("**/compare")
        time.sleep(2)

        # Screenshot comparison page
        page.screenshot(path="verification/comparison_page.png", full_page=True)
        print("Captured comparison_page.png")

        browser.close()

if __name__ == "__main__":
    verify_comparison_page()
