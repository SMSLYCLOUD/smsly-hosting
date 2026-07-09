import json
import time

from playwright.sync_api import sync_playwright

MOCK_TOPOLOGY = {
    "nodes": [
        {
            "id": "svc-1",
            "type": "SERVICE",
            "data": {
                "name": "api-gateway",
                "kind": "COMPUTE",
                "subtype": "GIT",
                "status": "ACTIVE",
                "region": "us-east",
                "url": "api.example.com",
                "metadata": {"replicas": 2}
            }
        },
        {
            "id": "addon-1",
            "type": "ADDON",
            "data": {
                "name": "main-db",
                "kind": "DATABASE",
                "subtype": "postgres",
                "status": "ACTIVE",
                "region": "us-east"
            }
        },
        {
            "id": "addon-2",
            "type": "ADDON",
            "data": {
                "name": "cache",
                "kind": "CACHE",
                "subtype": "redis",
                "status": "ACTIVE",
                "region": "us-east"
            }
        }
    ],
    "edges": [
        {"id": "e1", "source": "svc-1", "target": "addon-1", "type": "OWNS"},
        {"id": "e2", "source": "svc-1", "target": "addon-2", "type": "OWNS"},
        {"id": "e3", "source": "svc-1", "target": "addon-1", "type": "CONNECTS_TO", "data": {"protocol": "postgres"}}
    ]
}

def verify_topology(page):
    # Mock API
    page.route("**/api/v1/topology/", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(MOCK_TOPOLOGY)
    ))

    # Mock User (CRITICAL for AuthProvider)
    page.route("**/api/v1/auth/user/", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({
            "pk": 1,
            "username": "mockuser",
            "email": "mock@example.com",
            "first_name": "Mock",
            "last_name": "User"
        })
    ))

    # Mock Auth cookies
    page.context.add_cookies([
        {"name": "auth_token", "value": "mock-token", "url": "http://localhost:3000/"},
        {"name": "sessionid", "value": "mock-session", "url": "http://localhost:3000/"}
    ])

    # Set localStorage
    page.add_init_script("localStorage.setItem('auth_token', 'mock-token');")

    # Capture console logs and errors
    page.on("console", lambda msg: print(f"CONSOLE: {msg.text}"))
    page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))

    print("Navigating to Topology page...")
    page.goto("http://localhost:3000/topology")

    # Verify we are not redirected to login
    # We can check for "Sign in" or "Login" text which usually appears on login page
    # The Topology page should have "Topology" or tabs "3D View", "Schematic"

    try:
        # Wait for the tabs to appear, indicating we are on the topology page
        # The button text is "3D Graph"
        page.wait_for_selector("text=3D Graph", timeout=10000)
        print("Topology page loaded.")
    except Exception as e:
        print("Failed to load Topology page (possibly redirected to login).")
        page.screenshot(path="verification/login_redirect.png")
        raise e

    print("Waiting for 3D view canvas...")
    # ForceGraph3D renders canvas.
    try:
        page.wait_for_selector("canvas", timeout=30000)
        time.sleep(3) # Wait for animation/settle
        page.screenshot(path="verification/topology_3d.png")
        print("3D view captured.")
    except Exception as e:
        print(f"3D view failed: {e}")

    print("Switching to Schematic view...")
    try:
        page.get_by_text("Schematic").click()
        # Schematic uses ReactFlow, look for .react-flow class
        page.wait_for_selector(".react-flow", timeout=10000)
        time.sleep(2)
        page.screenshot(path="verification/topology_2d.png")
        print("Schematic view captured.")
    except Exception as e:
        print(f"Schematic view failed: {e}")

    print("Switching to Solar System view...")
    try:
        page.get_by_text("Solar System").click()
        time.sleep(3) # Wait for Three.js
        page.screenshot(path="verification/topology_solar.png")
        print("Solar System view captured.")
    except Exception as e:
        print(f"Solar view failed: {e}")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=egl", "--enable-unsafe-swiftshader", "--ignore-gpu-blacklist"])
        page = browser.new_page()
        try:
            verify_topology(page)
        except Exception as e:
            print(f"Global Error: {e}")
            page.screenshot(path="verification/error.png")
        finally:
            browser.close()
