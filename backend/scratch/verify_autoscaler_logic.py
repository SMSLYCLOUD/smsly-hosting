import os
import sys
import django
import time
from unittest.mock import MagicMock, patch

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.cache import cache
from django.utils import timezone

# Mock the cache to avoid Redis connection issues
mock_cache_store = {}
def mock_get(key, default=None): return mock_cache_store.get(key, default)
def mock_set(key, val, timeout=None): mock_cache_store[key] = val
def mock_delete(key): mock_cache_store.pop(key, None)

cache.get = mock_get
cache.set = mock_set
cache.delete = mock_delete
from apps.autoscaler.views import _decide_scaling, CACHE_KEY_LAST_SCALE, CACHE_KEY_DECISIONS, COOLDOWN_UP, COOLDOWN_DOWN

def verify_autoscaler_scenarios():
    print("--- Starting Deep Verification of Autoscaler Logic ---")
    
    # Reset Cache for clean test
    cache.delete(CACHE_KEY_LAST_SCALE)
    cache.delete(CACHE_KEY_DECISIONS)
    
    config = {
        "services": {
            "test-app": {"min_workers": 1, "max_workers": 5, "priority": 5}
        }
    }
    
    def make_svc(cur_workers, demand):
        return {
            "test-app": {
                "current_workers": cur_workers,
                "demand_score": demand,
                "min_workers": config["services"]["test-app"]["min_workers"],
                "max_workers": config["services"]["test-app"]["max_workers"]
            }
        }

    # Test 1: Scale Up Decision
    print("\nTest 1: Scale Up Decision (Demand 85%)")
    services = make_svc(1, 85.0)
    actions = _decide_scaling(services)
    assert len(actions) == 1
    assert actions[0]['action'] == 'scale_up'
    assert actions[0]['target_workers'] == 2
    print("[OK] Scale Up Decision Passed")

    # Test 2: Cooldown Up (Scale Up within 60s)
    print("\nTest 2: Cooldown Up (Immediate follow-up scale up)")
    # We just scaled, so another check now should NOT produce actions
    services = make_svc(2, 90.0) 
    actions = _decide_scaling(services)
    assert len(actions) == 0
    print("[OK] Cooldown Up Enforcement Passed")

    # Test 3: Scale Down Decision
    print("\nTest 3: Scale Down Decision (Demand 20%)")
    # Manually reset cooldown to allow scale down
    cache.delete(CACHE_KEY_LAST_SCALE)
    services = make_svc(3, 20.0)
    actions = _decide_scaling(services)
    assert len(actions) == 1
    assert actions[0]['action'] == 'scale_down'
    assert actions[0]['target_workers'] == 2
    print("[OK] Scale Down Decision Passed")

    # Test 4: Max Limits
    print("\nTest 4: Max Limits Enforcement")
    cache.delete(CACHE_KEY_LAST_SCALE)
    services = make_svc(5, 95.0) # Already at max
    actions = _decide_scaling(services)
    assert len(actions) == 0
    print("[OK] Max Limits Enforcement Passed")

    # Test 5: Min Limits
    print("\nTest 5: Min Limits Enforcement")
    cache.delete(CACHE_KEY_LAST_SCALE)
    services = make_svc(1, 10.0) # Already at min
    actions = _decide_scaling(services)
    assert len(actions) == 0
    print("[OK] Min Limits Enforcement Passed")

    print("\n--- All Decision Scenarios Verified Successfully ---")

if __name__ == "__main__":
    verify_autoscaler_scenarios()
