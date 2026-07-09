import os
import sys

import pytest
import requests

ROUTER_URL = os.environ.get("AI_ROUTER_URL", "https://ai-router-b7bd2fad-eb7003.pcloud.linadeluxe.com")
API_KEY = os.environ.get("AI_ROUTER_API_KEY")
pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="AI_ROUTER_API_KEY is required for live AI router smoke tests.",
)

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def check_endpoint():
    print("\n--- Testing AI Router Integration ---")
    print(f"URL: {ROUTER_URL}")
    print(f"Key: {API_KEY[:4]}...{API_KEY[-4:]}")

    print(f"\n1. Testing Health Endpoint: {ROUTER_URL}/health")
    try:
        response = requests.get(f"{ROUTER_URL}/health", headers=headers, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Body: {response.text[:200]}")
        if response.status_code != 200:
            print("Warning: Health check did not return 200 OK. Router may not be ready.")
    except Exception as e:
        print(f"Error fetching health: {e}")

    print(f"\n2. Testing Models Endpoint: {ROUTER_URL}/v1/models")
    try:
        response = requests.get(f"{ROUTER_URL}/v1/models", headers=headers, timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            models = [m.get("id") for m in data.get("data", [])]
            print(f"Available models: {models}")
            if not models:
                print("Warning: The router is reachable, but has NO models configured.")
                return False
        else:
            print(f"Error Body: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"Error fetching models: {e}")
        return False

    print(f"\n3. Testing Chat Completions: {ROUTER_URL}/v1/chat/completions")
    try:
        # Use braid-llm if available, or first available model
        model_to_use = "braid-llm" if "braid-llm" in models else models[0]
        print(f"Using model: {model_to_use}")
        payload = {
            "model": model_to_use,
            "messages": [{"role": "user", "content": "Reply with 'pong'."}],
            "max_tokens": 10
        }
        response = requests.post(f"{ROUTER_URL}/v1/chat/completions", headers=headers, json=payload, timeout=30)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {response.json().get('choices', [{}])[0].get('message', {}).get('content')}")
            print("\n✅ All AI Router functionality is working perfectly!")
            return True
        else:
            print(f"Error Body: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"Error testing completion: {e}")
        return False

def test_ai_router_endpoint():
    assert check_endpoint()

if __name__ == "__main__":
    if not API_KEY:
        print("AI_ROUTER_API_KEY is required for the live AI router smoke test.")
        sys.exit(1)
    success = check_endpoint()
    if not success:
        print("\n❌ Tests failed or returned warnings. This is expected if the router hasn't been re-deployed yet!")
        sys.exit(1)
