import os
import sys

# Add the backend directory to sys.path
sys.path.append(os.getcwd())

try:
    from services.ecosystem import (
        _apply_plan_repo_defaults,
        _apply_smsly_core_intelligence,
        _build_heuristic_plan,
    )
    print("Import successful!")
except Exception as e:
    print(f"Import failed: {e}")
    import traceback
    traceback.print_exc()
