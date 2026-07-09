"""
Verify all frontend pages are accessible.
"""

import os

from playwright.sync_api import sync_playwright


def run(base_url: str = "http://localhost:3000") -> int:
    """Run verification."""
    failures = []
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

        print("Verifying pages...")
        for path in pages:
            url = f"{base_url}{path}"
            print(f"Checking {url}...", end=" ")
            try:
                response = page.goto(url)
                status = response.status if response is not None else None
                if status == 200:
                    print("OK")
                else:
                    print(f"FAIL ({status})")
                    failures.append((url, f"unexpected status {status}"))
            except Exception as e: # pylint: disable=broad-exception-caught
                print(f"ERROR: {e}")
                failures.append((url, str(e)))

        browser.close()

    if failures:
        print("\nVerification failed for the following pages:")
        for url, reason in failures:
            print(f"- {url}: {reason}")
        return 1

    print("\nAll checked pages returned HTTP 200.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(os.environ.get("BASE_URL", "http://localhost:3000")))
