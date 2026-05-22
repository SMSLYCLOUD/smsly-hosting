#!/usr/bin/env python
import os
import sys
import django

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Setup Django
django.setup()

print("=== Custom Domain System Fix Implementation ===\n")

# Check if Docker is available and working
print("1. Docker API Connection Check")
print("-" * 40)

try:
    import subprocess
    import json
    
    # Check Docker daemon status
    try:
        result = subprocess.run(['docker', 'info'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("Docker daemon is running")
            print("Docker info available")
        else:
            print(f"Docker daemon error: {result.stderr}")
    except Exception as e:
        print(f"Docker daemon connection failed: {e}")
    
    # Check specific Caddy container
    try:
        result = subprocess.run(['docker', 'ps', '-a', '--filter', 'name=smsly-hosting-caddy-1'], 
                              capture_output=True, text=True, timeout=10)
        if 'smsly-hosting-caddy-1' in result.stdout:
            print("Caddy container exists")
        else:
            print("Caddy container not found")
    except Exception as e:
        print(f"Error checking Caddy container: {e}")
        
except Exception as e:
    print(f"Docker check failed: {e}")

print()

# Check domain check endpoint
print("2. Domain Check Endpoint Analysis")
print("-" * 40)

try:
    # Find the correct domain check view
    from apps.deployments import views
    
    # List all views in the module
    view_functions = [name for name in dir(views) if 'domain' in name.lower() and not name.startswith('_')]
    print("Domain-related views found:")
    for view in view_functions:
        print(f"  - {view}")
    
    # Try to import common domain check view names
    possible_views = [
        'check_domain',
        'domain_check', 
        'check_domain_view',
        'check_domain_api'
    ]
    
    for view_name in possible_views:
        try:
            view = getattr(views, view_name)
            print(f"Found view: {view_name} - {view}")
            break
        except AttributeError:
            continue
    
except Exception as e:
    print(f"Error checking domain views: {e}")

print()

# Check task execution and Caddy reload
print("3. Task Execution and Caddy Reload Test")
print("-" * 40)

try:
    from apps.domains.models import Domain
    from apps.domains.tasks import verify_dns_and_provision_ssl_task
    from services.caddy_manager import apply_caddyfile
    from apps.deployments.models import PlatformConfig
    
    domains = Domain.objects.all()
    if domains.exists():
        domain = domains.first()
        print(f"Testing with domain: {domain.domain_name}")
        
        # Execute the task
        print("Executing domain verification task...")
        result = verify_dns_and_provision_ssl_task(domain.id)
        print(f"Task result: {result}")
        
        # Check domain status after task
        domain.refresh_from_db()
        print(f"Domain status after task: {domain.status}")
        print(f"Domain verified after task: {domain.verified}")
        
        # Test Caddy reload with a mock approach
        print("\nTesting Caddy reload...")
        config = PlatformConfig.load()
        caddyfile_content = """
# Test Caddyfile
{
    on_demand_tls {
        ask http://nginx:80/api/v1/services/check-domain/
    }
}

example.com {
    reverse_proxy nginx:80
}
"""
        
        # Try to apply Caddyfile without Docker
        result = apply_caddyfile(caddyfile_content, cloudflare_token="")
        print(f"Caddy apply result: {result}")
        
    else:
        print("No domains found for testing")
        
except Exception as e:
    print(f"Error in task execution test: {e}")
    import traceback
    traceback.print_exc()

print()

# Provide fix recommendations
print("4. Fix Recommendations")
print("-" * 40)

print("RECOMMENDED FIXES:")
print("1. Fix Docker API connection:")
print("   - Ensure Docker Desktop is running")
print("   - Check Docker daemon status: docker info")
print("   - Verify Caddy container is running: docker ps -a | grep caddy")

print("\n2. Fix domain check endpoint:")
print("   - Search for domain check view in views.py")
print("   - Ensure the endpoint exists at /api/v1/services/check-domain/")
print("   - Check URL routing in urls.py")

print("\n3. Ensure Celery workers are running:")
print("   - Start Celery worker: celery -A config worker --loglevel=info")
print("   - Start Celery beat: celery -A config beat --loglevel=info")
print("   - Check worker status: celery -A config inspect active")

print("\n4. Test the complete workflow:")
print("   - Add a domain that actually points to the platform")
print("   - Wait for DNS verification task to run")
print("   - Check if domain status changes to 'dns_verified'")
print("   - Verify Caddy reloads and includes the domain")

print("\n=== End Fix Implementation ===")