"""
Verify specific fixes on the frontend.
"""

from playwright.sync_api import sync_playwright


def run():
    """Run verification."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Emulate a mobile device to test responsiveness
        context = browser.new_context(viewport={"width": 375, "height": 812})
        page = context.new_page()

        # Test 1: /new should be accessible
        print("Navigating to /new...")
        try:
            page.goto("http://localhost:3000/new")
            page.wait_for_load_state("networkidle")

            if page.get_by_text("Deploy New Service").is_visible():
                print("PASS: Heading found on /new.")
            else:
                print("FAIL: Heading NOT found on /new.")

            # Take screenshot of /new
            page.screenshot(
                path="/tmp/verification_new_ui_mobile_v2.png",
                full_page=True
            )
            print("Screenshot saved to /tmp/verification_new_ui_mobile_v2.png")

        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"Error on /new: {e}")

        # Test 2: / should redirect to /login (no cookie)
        print("\nNavigating to / (no cookie)...")
        try:
            # pylint: disable=unused-variable
            page.goto("http://localhost:3000/")
            page.wait_for_load_state("networkidle")

            if "/login" in page.url:
                print("PASS: Redirected to /login.")
            else:
                print(f"FAIL: Did not redirect to /login. URL: {page.url}")
        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"Error on /: {e}")

        # Test 3: / with cookie should stay on /
        print("\nNavigating to / (with cookie)...")
        try:
            context.add_cookies([{
                "name": "auth_token",
                "value": "dummy_token",
                "domain": "localhost",
                "path": "/"
            }])
            # pylint: disable=unused-variable
            page.goto("http://localhost:3000/")
            page.wait_for_load_state("networkidle")

            # It might still redirect if the page itself does auth check,
            # but middleware should pass. The page.tsx for / assumes dashboard content.
            if page.url.rstrip('/') == "http://localhost:3000":
                print("PASS: Stayed on /.")
            else:
                print(f"FAIL: Redirected from /. URL: {page.url}")
        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"Error on / with cookie: {e}")

        browser.close()

if __name__ == "__main__":
    run()
