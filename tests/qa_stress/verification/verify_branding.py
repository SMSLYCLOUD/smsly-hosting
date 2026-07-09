"""
Verify branding updates.
"""

from playwright.sync_api import sync_playwright


def run():
    """Run verification."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Desktop
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        # Check Landing
        print("Checking Landing Page...")
        try:
            page.goto("http://localhost:3000/")
            page.wait_for_load_state("networkidle")
            page.screenshot(path="/home/jules/verification/landing_page_updated.png", full_page=True)
            print("Captured landing_page_updated.png")

            # Check for Grid text
            if page.get_by_text("Grid").count() > 0:
                print("✓ Found 'Grid' text.")
            else:
                print("❌ ERROR: 'Grid' text not found.")

            # Check for legacy CloudNeuron text
            if page.get_by_text("CloudNeuron").count() > 0:
                print("❌ ERROR: Found legacy 'CloudNeuron' text!")

        except Exception as e:
            print(f"Error on Landing: {e}")

        # Check Login
        print("Checking Login Page...")
        try:
            page.goto("http://localhost:3000/login")
            page.wait_for_load_state("networkidle")
            page.screenshot(path="/home/jules/verification/login_page_updated.png")
            print("Captured login_page_updated.png")
        except Exception as e:
            print(f"Error on Login: {e}")

        # Check Register
        print("Checking Register Page...")
        try:
            page.goto("http://localhost:3000/register")
            page.wait_for_load_state("networkidle")
            page.screenshot(path="/home/jules/verification/register_page_updated.png")
            print("Captured register_page_updated.png")
        except Exception as e:
            print(f"Error on Register: {e}")

        browser.close()

if __name__ == "__main__":
    run()
