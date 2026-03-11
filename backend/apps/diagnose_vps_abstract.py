
import os
import sys
import inspect

# Add current dir to path
sys.path.append(os.getcwd())

def check_file(path):
    print(f"\n--- Checking {path} ---")
    if not os.path.exists(path):
        print("FILE NOT FOUND!")
        return
    with open(path, 'r') as f:
        content = f.read()
        # Print lines around pull_image
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "pull_image" in line:
                start = max(0, i - 2)
                end = min(len(lines), i + 5)
                for j in range(start, end):
                    print(f"{j+1}: {lines[j]}")

def test_instantiation():
    print("\n--- Testing Instantiation ---")
    try:
        from apps.cloud.adapters.local import LocalAdapter
        print("Successfully imported LocalAdapter")
        print(f"Abstract methods: {LocalAdapter.__abstractmethods__}")
        
        try:
            adapter = LocalAdapter()
            print("Successfully instantiated LocalAdapter")
        except TypeError as e:
            print(f"FAILED to instantiate: {e}")
            
    except Exception as e:
        print(f"Error during import/test: {e}")

if __name__ == "__main__":
    check_file("apps/cloud/adapters/base.py")
    check_file("apps/cloud/adapters/local.py")
    test_instantiation()
    
    # Force delete pycache
    print("\nCleaning pycache...")
    for root, dirs, files in os.walk("."):
        for d in dirs:
            if d == "__pycache__":
                import shutil
                shutil.rmtree(os.path.join(root, d))
    print("Pycache cleaned.")
