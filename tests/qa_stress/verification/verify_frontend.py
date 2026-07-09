import time

from playwright.sync_api import sync_playwright


def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()

    # Pre-set auth token
    context.add_init_script("""
        localStorage.setItem('auth_token', 'mock-token');
        localStorage.setItem('smsly_active_team', 'team-1');
    """)

    page = context.new_page()

    # Mock API responses
    def handle_route(route):
        url = route.request.url
        if "/api/v1/auth/user/" in url:
            route.fulfill(json={"pk": 1, "username": "jules", "email": "jules@smsly.cloud"})
        elif "/api/v1/teams/" in url:
            if "members" in url:
                 route.fulfill(json=[
                    {"id": 1, "user": 1, "username": "jules", "email": "jules@smsly.cloud", "role": "ADMIN"},
                    {"id": 2, "user": 2, "username": "alice", "email": "alice@example.com", "role": "MEMBER"}
                ])
            else:
                route.fulfill(json=[{"id": "team-1", "name": "Engineering", "members_count": 2, "owner": "jules"}])
        elif "/api/v1/services/" in url:
            route.fulfill(json=[
                {"id": "func-1", "name": "my-function", "deploy_type": "FUNCTION", "latest_deployment": {"status": "ACTIVE"}}
            ])
        elif "/api/v1/servers/" in url:
            route.fulfill(json=[])
        elif "/api/v1/templates/" in url:
            route.fulfill(json=[])
        elif "/api/v1/integrations/github/repos/" in url:
            route.fulfill(json={"repos": []})
        else:
            route.continue_()

    page.route("**/*", handle_route)

    try:
        # 1. verify Team Page
        print("Navigating to Team Page...")
        page.goto("http://localhost:3000/settings/team")
        page.wait_for_selector("text=Engineering", timeout=10000)
        page.screenshot(path="tests/qa_stress/verification/team_page.png")
        print("Screenshot saved: team_page.png")

        # 2. Verify Functions Page
        print("Navigating to Functions Page...")
        page.goto("http://localhost:3000/functions")
        page.wait_for_selector("text=Functions", timeout=10000)
        # Wait for editor to load (might take a sec)
        time.sleep(2)
        page.screenshot(path="tests/qa_stress/verification/functions_page.png")
        print("Screenshot saved: functions_page.png")

        # 3. Verify New Service Page (Buildpack Selector)
        print("Navigating to New Service Page...")
        page.goto("http://localhost:3000/new")
        # Click "Git Repository" (step 1)
        # We need to bypass step 1 logic or mock state.
        # Actually, let's just check the BuildpackSelector component is present if we select "Template" -> skip analysis
        # Or easier: Go to a service settings page that has the Build tab?
        # But we need a service id. Let's rely on /new/page.tsx behavior.

        # Select "Template" to go to step 3 directly? No, step 1 requires selection.
        # Select Template card
        page.click("text=Template")
        # Click "Next"
        page.click("text=Next")

        # Now on Step 3 (Configure). Should see Buildpack selector?
        # Wait, Buildpack selector is hidden if sourceType is docker. But for Template/Git it should be visible?
        # Actually, in the code: {sourceType !== "docker" && (<BuildpackSelector ... />)}
        # So it should be visible.

        time.sleep(1)
        page.screenshot(path="tests/qa_stress/verification/new_service_page.png")
        print("Screenshot saved: new_service_page.png")

    except Exception as e:
        print(f"Error: {e}")
        page.screenshot(path="tests/qa_stress/verification/error.png")
    finally:
        browser.close()

with sync_playwright() as playwright:
    run(playwright)
