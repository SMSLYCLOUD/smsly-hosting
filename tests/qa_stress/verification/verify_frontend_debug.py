import time

from playwright.sync_api import sync_playwright


def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()

    context.add_init_script("""
        localStorage.setItem('auth_token', 'mock-token');
        localStorage.setItem('smsly_active_team', 'team-1');
    """)

    page = context.new_page()

    # Mock API responses
    def handle_route(route):
        url = route.request.url
        # print(f"Request: {url}")
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
        time.sleep(3)
        page.screenshot(path="tests/qa_stress/verification/team_page_debug.png")
        print("Screenshot saved: team_page_debug.png")

        # 2. Verify Functions Page
        print("Navigating to Functions Page...")
        page.goto("http://localhost:3000/functions")
        time.sleep(3)
        page.screenshot(path="tests/qa_stress/verification/functions_page_debug.png")
        print("Screenshot saved: functions_page_debug.png")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        browser.close()

with sync_playwright() as playwright:
    run(playwright)
