#!/usr/bin/env python3
import requests
import time
import json
import concurrent.futures
from dataclasses import dataclass

PYTHON_HOST = "http://localhost:8001"
RUST_HOST = "http://localhost:8002"

@dataclass
class TestResult:
    endpoint: str
    method: str
    python_status: int
    rust_status: int
    python_time_ms: float
    rust_time_ms: float
    parity_passed: bool

def hit_endpoint(host, method, endpoint, payload=None, token=None):
    url = f"{host}{endpoint}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    start_time = time.time()
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=5)
        elif method == "POST":
            resp = requests.post(url, json=payload, headers=headers, timeout=5)

        elapsed = (time.time() - start_time) * 1000
        return resp.status_code, elapsed
    except Exception as e:
        return 0, 0.0

def run_parity_test(endpoint, method="GET", payload=None, token=None):
    print(f"Testing {method} {endpoint} simultaneously...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_py = executor.submit(hit_endpoint, PYTHON_HOST, method, endpoint, payload, token)
        future_rs = executor.submit(hit_endpoint, RUST_HOST, method, endpoint, payload, token)

        py_status, py_time = future_py.result()
        rs_status, rs_time = future_rs.result()

    # We consider parity "passed" if both systems return the same HTTP Status classification (e.g. 20x, 40x)
    # The new Rust system might return 201 Created while old Python returned 200 OK depending on DRF specifics.
    parity_passed = str(py_status)[0] == str(rs_status)[0] and py_status != 0

    return TestResult(endpoint, method, py_status, rs_status, py_time, rs_time, parity_passed)

def generate_markdown_report(results):
    with open("PARITY_REPORT.md", "w") as f:
        f.write("# Grid Python vs. Rust Parity Report\n\n")
        f.write("This report details the simultaneous endpoint testing against both the legacy Python/Django application and the new Rust/Axum twin.\n\n")

        f.write("| Method | Endpoint | Python Status | Rust Status | Python Latency (ms) | Rust Latency (ms) | Parity Status |\n")
        f.write("|---|---|---|---|---|---|---|\n")

        total_py_time = 0
        total_rs_time = 0

        for res in results:
            status_emoji = "✅ PASS" if res.parity_passed else "❌ FAIL"
            f.write(f"| {res.method} | {res.endpoint} | {res.python_status} | {res.rust_status} | {res.python_time_ms:.2f} | {res.rust_time_ms:.2f} | {status_emoji} |\n")

            total_py_time += res.python_time_ms
            total_rs_time += res.rust_time_ms

        f.write("\n## Performance Summary\n")
        if len(results) > 0:
            f.write(f"- **Average Python Latency:** {total_py_time / len(results):.2f} ms\n")
            f.write(f"- **Average Rust Latency:** {total_rs_time / len(results):.2f} ms\n")

        print("\n=> Generated PARITY_REPORT.md successfully.")

if __name__ == "__main__":
    print("Waiting for containers to fully boot...")
    time.sleep(3) # Give databases a moment

    results = []

    # 1. Health Check
    results.append(run_parity_test("/health", "GET"))

    # 2. Authentication Rejection (Missing Token)
    results.append(run_parity_test("/api/v1/projects", "GET"))

    # 3. Billing Singleton Test (Unauthorized)
    results.append(run_parity_test("/api/v1/billing/license", "GET"))

    # 4. Teams Listing (Unauthorized)
    results.append(run_parity_test("/api/v1/teams", "GET"))

    # 5. Invalid Login Credentials
    login_payload = {"username": "non_existent_user", "password": "wrong_password"}
    results.append(run_parity_test("/api/v1/auth/login", "POST", payload=login_payload))

    generate_markdown_report(results)
