import os
import sys

# Get absolute path to the backend directory
# Assuming the script is in backend/apps/intelligence/
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))

if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Backend dir: {backend_dir}")

try:
    from apps.intelligence.scanner import RepoScanner
    print("Successfully imported RepoScanner")
except ImportError as e:
    print(f"Import failed: {e}")
    sys.exit(1)

def test_scanner():
    # Use the root directory as a test case (where .env and code resides)
    root_dir = os.path.abspath(os.path.join(backend_dir, '..'))
    print(f"Scanning root: {root_dir}")

    scanner = RepoScanner(root_dir)
    results = scanner.scan()

    print(f"Stack: {results.get('stack')}")
    print(f"Detected Variables: {len(results.get('env_vars_context', {}))} found")

    # Check if context was captured
    context = results.get('env_vars_context', {})
    for var, snippets in list(context.items())[:5]:
        print(f"\nVariable: {var}")
        for snippet in snippets:
            # Clean up snippet for printing
            clean_snippet = snippet.strip().split('\n')[0]
            print(f"  Snippet (first line): {clean_snippet}")

if __name__ == "__main__":
    test_scanner()
