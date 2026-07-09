"""
Verify new UI components.
"""

# pylint: disable=duplicate-code

from playwright.sync_api import sync_playwright


def run():
    """Run verification."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Emulate a mobile device to test responsiveness
        context = browser.new_context(viewport={"width": 375, "height": 812})
        page = context.new_page()

        # Navigate to /new
        print("Navigating to /new...")
        try:
            page.goto("http://localhost:3000/new")
            page.wait_for_load_state("networkidle")

            print("Page loaded.")

            # Verify "Deploy New Service" heading
            if page.get_by_text("Deploy New Service").is_visible():
                print("Heading found.")
            else:
                print("Heading NOT found.")

            # Verify Navbar "Dashboard" link (might be inside hamburger menu on mobile)
            # On mobile, the hamburger menu is visible.
            if page.get_by_role("button").nth(0).is_visible(): # Hamburger button
                print("Hamburger menu button visible.")

            # Take screenshot
            page.screenshot(path="/tmp/verification_new_ui_mobile.png", full_page=True)
            print("Screenshot saved to /tmp/verification_new_ui_mobile.png")

        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run()
