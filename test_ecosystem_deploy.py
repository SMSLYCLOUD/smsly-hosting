import os
import sys
import json
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def main():
    if not os.environ.get('OPENROUTER_API_KEY'):
        print("Please set your OPENROUTER_API_KEY environment variable.")
        print("Example: $env:OPENROUTER_API_KEY='sk-or-v1-...'")
        sys.exit(1)

    # Set up Django environment
    sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    
    try:
        import django
        django.setup()
    except Exception as e:
        print(f"Error setting up Django: {e}")
        sys.exit(1)
        
    from services.ecosystem import analyze_ecosystem_chunked
    
    # We create a mock repository data payload for testing
    print("Preparing test repository data (osaretin/test-repo)...")
    repos_data = [
        {
            "repo": "osaretin/test-repo",
            "description": "A test repository for AI parsing.",
            "default_branch": "main",
            "files": ["package.json", "index.js", ".env.example"],
            "heuristic": {
                "stack": "node",
                "port": 3000,
                "build": "nixpacks",
                "addons": ["POSTGRES", "REDIS"],
                "env_vars": [
                    {"key": "PORT", "required": False, "default": "3000"},
                    {"key": "DATABASE_URL", "required": True},
                    {"key": "REDIS_URL", "required": True}
                ]
            },
            "private": False
        },
        {
            "repo": "osaretin/test-frontend",
            "description": "A Next.js frontend test",
            "default_branch": "main",
            "files": ["package.json", "next.config.js"],
            "heuristic": {
                "stack": "nextjs",
                "port": 3000,
                "build": "nixpacks",
                "addons": [],
                "env_vars": [
                    {"key": "NEXT_PUBLIC_API_URL", "required": True}
                ]
            },
            "private": False
        }
    ]
    
    print("Running analyze_ecosystem_chunked with openrouter...")
    try:
        # provider_id="openrouter" maps to whatever openrouter logic is inside ask_with_fallback
        # In ask_with_fallback, it usually checks AI_PROVIDER env var if provider_id='auto'
        os.environ['AI_PROVIDER'] = 'openrouter' 
        plan = analyze_ecosystem_chunked(
            repos_data=repos_data, 
            github_token=None, 
            ai_provider="openrouter"
        )
        
        print("\n=== AI ECOSYSTEM PLAN ===")
        print(json.dumps(plan, indent=2))
        print("=== END PLAN ===")
        print("Success! AI revalidation process completed without crashing.")
    except Exception as e:
        print(f"\nFAILURE: {e}")

if __name__ == "__main__":
    main()
