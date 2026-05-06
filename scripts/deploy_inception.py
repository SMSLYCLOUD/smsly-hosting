#!/usr/bin/env python3
import requests
import time
import sys

# This script simulates the "Inception" deployment:
# Instructing the running Python Platform to deploy the Rust Twin application via its API.

PYTHON_API_URL = "http://localhost:8090/api/v1"
# We would normally authenticate via GitHub/Google OAuth, but we'll assume an API token is provided
API_TOKEN = "test_token_123"

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

def deploy_rust_twin():
    print("1. Creating Project on Python Platform...")
    try:
        res = requests.post(f"{PYTHON_API_URL}/projects/", json={
            "name": "Rust Twin Inception",
            "slug": "rust-twin-inception",
            "description": "Deploying the next-gen architecture inside the legacy platform."
        }, headers=HEADERS, timeout=5)
        res.raise_for_status()
        project_id = res.json()["id"]
        print(f"   => Project created: {project_id}")
    except requests.exceptions.RequestException as e:
        print(f"   [!] Failed to create project. Is the Python backend running? Error: {e}")
        return

    print("\n2. Creating Service (Rust Web API)...")
    try:
        res = requests.post(f"{PYTHON_API_URL}/services/", json={
            "project_id": project_id,
            "name": "Rust Axum API",
            "slug": "rust-axum",
            "service_type": "WEB",
            # Pointing to the GitHub repo where the Rust code lives
            "repo_url": "https://github.com/SMSLYCLOUD/smsly-hosting.git",
            "branch": "main",
            # We specifically want to deploy the `api` crate
            "root_directory": "rust_twin/crates/api"
        }, headers=HEADERS, timeout=5)
        res.raise_for_status()
        service_id = res.json()["id"]
        print(f"   => Service created: {service_id}")
    except Exception as e:
        print(f"   [!] Failed to create service: {e}")
        return

    print("\n3. Triggering Deployment (Python Celery -> Nixpacks -> Rust Compile)...")
    try:
        res = requests.post(f"{PYTHON_API_URL}/deployments/", json={
            "service_id": service_id,
            "commit_hash": "HEAD"
        }, headers=HEADERS, timeout=5)
        res.raise_for_status()
        deployment_id = res.json()["id"]
        print(f"   => Deployment queued: {deployment_id}")
    except Exception as e:
        print(f"   [!] Failed to trigger deployment: {e}")
        return

    print("\n4. Polling Deployment Status...")
    for _ in range(30): # Poll for up to 5 minutes
        time.sleep(10)
        res = requests.get(f"{PYTHON_API_URL}/deployments/{deployment_id}/", headers=HEADERS)
        if res.status_code == 200:
            status = res.json()["status"]
            print(f"   => Status: {status}")
            if status == "RUNNING":
                print("\n✅ SUCCESS: The Rust Twin was compiled and deployed by the Python Twin!")
                # The Python Traefik edge proxy will have automatically assigned it a domain
                domain = res.json().get("public_domain", "localhost")
                print(f"   => Rust App Live At: https://{domain}")

                # Final verification: Hit the deployed Rust app
                verify = requests.get(f"http://{domain}/health")
                if verify.status_code == 200:
                    print("✅ verified: Rust /health endpoint returned 200 OK.")
                return
            elif status in ["FAILED", "STOPPED", "CRASHED"]:
                print("❌ FAILED: Deployment did not succeed.")
                return
    print("⏳ TIMEOUT: Deployment took too long.")

if __name__ == "__main__":
    print("==================================================")
    print("  Grid Inception: Python deploys Rust")
    print("==================================================")
    deploy_rust_twin()
