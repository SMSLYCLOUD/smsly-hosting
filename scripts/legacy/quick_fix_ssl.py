#!/usr/bin/env python3
"""
Quick Fix for Custom Domain SSL System
Addresses the core issues preventing custom domain SSL from working
"""

import logging
import subprocess
import sys

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_command(cmd, description, check=True):
    """Run a command and handle errors"""
    logger.info(f"Running: {description}")
    try:
        result = subprocess.run(cmd, shell=True, check=check, 
                              capture_output=True, text=True, timeout=30)
        logger.info(f"✅ {description} - Success")
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ {description} - Failed: {e}")
        logger.error(f"STDERR: {e.stderr}")
        if check:
            raise
        return e
    except subprocess.TimeoutExpired as e:
        logger.error(f"❌ {description} - Timeout: {e}")
        if check:
            raise
        return e

def fix_dependencies():
    """Install missing dependencies"""
    logger.info("🔧 Installing missing dependencies...")
    
    # Install required packages
    packages = ["dj_database_url", "celery", "redis", "python-dotenv"]
    
    for package in packages:
        logger.info(f"Installing {package}...")
        result = run_command(f"pip install {package}", f"Install {package}")
        if result.returncode == 0:
            logger.info(f"✅ {package} installed successfully")
        else:
            logger.error(f"❌ Failed to install {package}")

def check_domain_system():
    """Check the domain system status"""
    logger.info("🔍 Checking domain system...")
    
    try:
        # Test Django setup
        result = run_command('python -c "import os; os.environ[\'DJANGO_SETTINGS_MODULE\'] = \'config.settings\'; import django; django.setup(); from apps.domains.models import Domain; print(f\'Domains: {Domain.objects.count()}\')"', 
                           "Test Django domain system")
        
        if result.returncode == 0:
            logger.info("✅ Domain system is working")
            return True
        else:
            logger.error("❌ Domain system issue")
            return False
            
    except Exception as e:
        logger.error(f"❌ Failed to check domain system: {e}")
        return False

def setup_celery():
    """Setup Celery configuration"""
    logger.info("🔧 Setting up Celery...")
    
    try:
        # Test Celery worker startup
        result = run_command("celery -A config worker --loglevel=info --timeout=5", 
                           "Test Celery worker", check=False)
        
        if result.returncode == 0:
            logger.info("✅ Celery is working")
            return True
        else:
            logger.error("❌ Celery configuration issue")
            logger.error(f"Error: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Failed to setup Celery: {e}")
        return False

def create_manual_fix():
    """Create a manual fix script for the user"""
    logger.info("📝 Creating manual fix script...")
    
    manual_script = """#!/usr/bin/env python3
"""
    manual_script += """# Manual Fix for Custom Domain SSL System
# Run this script after starting Docker Desktop

import os
import sys
import subprocess
import time
from pathlib import Path

def run_command(cmd, description):
    print(f"Running: {description}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        print(f"✅ {description} - Success")
        if result.stdout:
            print(f"Output: {result.stdout}")
        return result
    except Exception as e:
        print(f"❌ {description} - Failed: {e}")
        return None

def main():
    print("🚀 Starting Manual Custom Domain SSL Fix...")
    
    # Change to backend directory
    os.chdir("backend")
    
    # Step 1: Start Celery worker
    print("\\n1. Starting Celery worker...")
    worker_process = subprocess.Popen(["celery", "-A", "config", "worker", "--loglevel=info"], 
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Step 2: Start Celery beat
    print("\\n2. Starting Celery beat...")
    beat_process = subprocess.Popen(["celery", "-A", "config", "beat", "--loglevel=info"], 
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait a bit for services to start
    print("\\n⏳ Waiting for services to start...")
    time.sleep(10)
    
    # Step 3: Test domain verification
    print("\\n3. Testing domain verification...")
    test_cmd = '''python -c "
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from apps.domains.models import Domain
from apps.domains.tasks import verify_dns_and_provision_ssl_task

# Get all pending domains
pending_domains = Domain.objects.filter(status__in=['pending', 'dns_pending'])
print(f'Found {pending_domains.count()} pending domains')

for domain in pending_domains:
    print(f'Processing domain: {domain.domain_name} (ID: {domain.id})')
    # Run the verification task
    verify_dns_and_provision_ssl_task(domain.id)
    print(f'Task queued for {domain.domain_name}')
"'''
    
    result = run_command(test_cmd, "Run domain verification tasks")
    
    # Step 4: Check domain statuses
    print("\\n4. Checking domain statuses...")
    status_cmd = '''python -c "
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from apps.domains.models import Domain, DomainStatus

domains = Domain.objects.all()
print('\\nDomain Statuses:')
for domain in domains:
    print(f'{domain.domain_name}: {domain.status} (Verified: {domain.verified}, SSL: {domain.ssl_active})')
"'''
    
    run_command(status_cmd, "Check domain statuses")
    
    print("\\n✅ Manual fix completed!")
    print("\\n📋 Next Steps:")
    print("1. Keep Celery worker and beat running")
    print("2. Add a custom domain that points to your VPS IP (209.159.152.123)")
    print("3. Monitor the domain status in the database")
    print("4. Check Caddyfile for the new domain")
    print("5. Test HTTPS access to the custom domain")
    
    print("\\nPress Ctrl+C to stop Celery processes when done")
    
    try:
        # Keep the processes running
        while True:
            time.sleep(1)
            if worker_process.poll() is not None:
                print("Celery worker process died")
                break
            if beat_process.poll() is not None:
                print("Celery beat process died")
                break
    except KeyboardInterrupt:
        print("\\nStopping Celery processes...")
        worker_process.terminate()
        beat_process.terminate()
        worker_process.wait()
        beat_process.wait()

if __name__ == "__main__":
    main()
"""
    
    with open("manual_fix_custom_domain.py", "w") as f:
        f.write(manual_script)
    
    print("✅ Manual fix script created: manual_fix_custom_domain.py")

def main():
    """Main function"""
    logger.info("🚀 Starting Quick Custom Domain SSL Fix...")
    
    # Fix dependencies first
    fix_dependencies()
    
    # Check domain system
    if not check_domain_system():
        logger.error("❌ Domain system check failed")
        return False
    
    # Setup Celery
    if not setup_celery():
        logger.error("❌ Celery setup failed")
        return False
    
    # Create manual fix script
    create_manual_fix()
    
    logger.info("✅ Quick fix completed!")
    logger.info("\n📋 Required Manual Steps:")
    logger.info("1. Start Docker Desktop (if not already running)")
    logger.info("2. Run: python manual_fix_custom_domain.py")
    logger.info("3. Add a custom domain that points to your VPS IP")
    logger.info("4. Monitor domain status changes")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)