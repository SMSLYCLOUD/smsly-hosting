"""
Visualize Hosting Topology using Playwright.

Generates screenshots of key pages in the hosting platform with mock data.
"""

# pylint: disable=unused-import
from playwright.sync_api import sync_playwright


def run(playwright_instance):
    """Run the visualization script."""
    # pylint: disable=too-many-statements
    browser = playwright_instance.chromium.launch()
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()

    # --- MOCK DATA ---
    services = []
    for i in range(1, 13):
        status = "ACTIVE"
        if i % 4 == 0:
            status = "DEPLOYING"
        if i == 11:
            status = "FAILED"

        services.append({
            "id": f"svc-{i}",
            "name": f"microservice-{i}",
            "repository_url": "https://github.com/smsly/example",
            "branch": "main",
            "internal_port": 8000,
            "cpu_cores": 0.5,
            "memory_mb": 512,
            "latest_deployment": {
                "id": f"dep-{i}",
                "status": status,
                "commit_hash": "a1b2c3d",
                "build_logs": "Build started...\nCompiling...\nSuccess!",
                "created_at": "2024-03-12T10:00:00Z"
            }
        })

    topology = {
        "nodes": [
            {"id": "svc-1", "name": "api-gateway", "type": "SERVICE"},
            {"id": "svc-2", "name": "auth-service", "type": "SERVICE"},
            {"id": "svc-3", "name": "payment-worker", "type": "SERVICE"},
            {"id": "db-1", "name": "users-db", "type": "POSTGRES"},
            {"id": "redis-1", "name": "cache", "type": "REDIS"},
        ] + [{"id": f"svc-{i}", "name": f"worker-{i}", "type": "SERVICE"} for i in range(4, 13)],
        "links": [
            {"source": "svc-1", "target": "svc-2"},
            {"source": "svc-2", "target": "db-1"},
            {"source": "svc-1", "target": "redis-1"},
            {"source": "svc-3", "target": "db-1"},
        ]
    }

    # --- ROUTES ---
    page.route("**/api/v1/services/", lambda route: route.fulfill(json=services))
    page.route("**/api/v1/services/svc-1/", lambda route: route.fulfill(json=services[0]))
    page.route("**/api/v1/deployments/dep-1/", lambda route: route.fulfill(json={
        "id": "dep-1",
        "status": "FAILED",
        "commit_hash": "a1b2c3d",
        "build_logs": (
            "[INFO] Cloning repo...\n"
            "[INFO] Building Dockerfile...\n"
            "[ERROR] npm install failed.\n"
            "package.json not found."
        ),
        "ai_diagnosis": (
            "It seems you are missing a `package.json` file. "
            "Ensure you are in the root directory."
        ),
        "created_at": "2024-03-12T10:00:00Z"
    }))
    page.route("**/api/v1/services/svc-1/env-vars/", lambda route: route.fulfill(json=[
        {"id": "1", "key": "DATABASE_URL", "is_secret": True},
        {"id": "2", "key": "API_KEY", "is_secret": True},
    ]))
    page.route("**/api/v1/metrics/?service_id=svc-1", lambda route: route.fulfill(json=[
        {
            "cpu_usage": 0.2 + (i/100),
            "memory_usage": 200 + (i*5),
            "timestamp": f"2024-03-12T10:{i}:00Z"
        } for i in range(10, 60)
    ]))
    page.route("**/api/v1/topology/", lambda route: route.fulfill(json=topology))
    page.route("**/api/v1/templates/", lambda route: route.fulfill(json=[
        {"id": "1", "name": "PostgreSQL", "description": "Relational Database",
         "icon_url": "", "repository_url": "", "default_port": 5432},
        {"id": "2", "name": "Redis", "description": "In-memory cache",
         "icon_url": "", "repository_url": "", "default_port": 6379},
    ]))

    # --- NAVIGATION ---
    base_url = "http://localhost:3000"

    print("Snapping Login...")
    page.goto(f"{base_url}/login")
    page.wait_for_timeout(1000)
    page.screenshot(path="screenshots/hosting/00_login.png", full_page=True)

    print("Snapping Dashboard (Canvas)...")
    page.goto(f"{base_url}/services")
    page.wait_for_timeout(2000)
    page.screenshot(path="screenshots/hosting/01_dashboard.png", full_page=True)

    print("Snapping Service Detail (Overview)...")
    page.goto(f"{base_url}/services/svc-1")
    page.wait_for_timeout(2000)
    page.screenshot(path="screenshots/hosting/02_service_overview.png", full_page=True)

    # Tabs mapping (id -> Label text)
    tabs = [
        ("logs", "Logs", "03_logs"),
        ("metrics", "Metrics", "04_metrics"),
        ("env", "Variables", "05_env"),
        ("addons", "Database", "06_addons"),
        ("deployments", "Deployments", "07_deployments"),
        ("settings", "Settings", "08_settings"),
        ("security", "Security", "10_security"),
    ]

    for tab_id, label, filename in tabs: # pylint: disable=unused-variable
        print(f"Snapping Service Detail ({label})...")
        page.click(f"button >> text={label}")
        page.wait_for_timeout(500)
        page.screenshot(path=f"screenshots/hosting/{filename}.png", full_page=True)

    print("Snapping Topology...")
    page.goto(f"{base_url}/topology")
    page.wait_for_timeout(3000)
    page.screenshot(path="screenshots/hosting/12_topology.png", full_page=True)

    print("Snapping Admin Dashboard...")
    page.goto(f"{base_url}/admin-dashboard")
    page.wait_for_timeout(2000)
    page.screenshot(path="screenshots/hosting/13_admin_dashboard.png", full_page=True)

    print("Snapping App Store...")
    page.goto(f"{base_url}/store")
    page.wait_for_timeout(2000)
    page.screenshot(path="screenshots/hosting/14_app_store.png", full_page=True)

    print("Snapping Smart Onboarding...")
    page.goto(f"{base_url}/get-started")
    page.wait_for_timeout(3000) # Wait for animation
    page.screenshot(path="screenshots/hosting/15_onboarding.png", full_page=True)

    browser.close()

with sync_playwright() as playwright:
    run(playwright)
