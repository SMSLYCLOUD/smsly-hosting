"""
Verify all frontend pages are accessible.
"""

from playwright.sync_api import sync_playwright

def run():
    """Run verification."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Define pages to check
        pages = [
            "/login",
            "/services",
            "/topology",
            "/store",
            "/new",
            # Add more pages as needed
        ]

        base_url = "http://localhost:3000"

        print("Verifying pages...")
        for path in pages:
            url = f"{base_url}{path}"
            print(f"Checking {url}...", end=" ")
            try:
                response = page.goto(url)
                if response.status == 200:
                    print("OK")
                else:
                    print(f"FAIL ({response.status})")
            except Exception as e: # pylint: disable=broad-exception-caught
                print(f"ERROR: {e}")

        browser.close()

if __name__ == "__main__":
    run()
