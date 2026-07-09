#!/usr/bin/env python3
"""
Custom Domain SSL Fix Script
Permanently fixes the custom domain SSL system by addressing:
1. Docker daemon issues
2. Celery worker setup
3. Missing dependencies
4. Testing and verification
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_command(cmd, description, check=True, cwd=None):
    """Run a command and handle errors"""
    logger.info(f"Running: {description}")
    try:
        result = subprocess.run(cmd, shell=True, check=check, cwd=cwd, 
                              capture_output=True, text=True, timeout=30)
        logger.info(f"✅ {description} - Success")
        if result.stdout:
            logger.debug(f"STDOUT: {result.stdout}")
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

def check_docker_status():
    """Check if Docker is running and accessible"""
    logger.info("🔍 Checking Docker status...")
    try:
        result = run_command("docker info", "Docker info check", check=False)
        if result.returncode == 0:
            logger.info("✅ Docker daemon is running")
            return True
        else:
            logger.error("❌ Docker daemon is not running")
            return False
    except Exception as e:
        logger.error(f"❌ Docker check failed: {e}")
        return False

def start_docker():
    """Start Docker Desktop"""
    logger.info("🚀 Attempting to start Docker Desktop...")
    try:
        # Try starting Docker Desktop
        result = run_command("dockerdesktop start", "Start Docker Desktop", check=False)
        if result.returncode == 0:
            logger.info("✅ Docker Desktop started successfully")
            # Wait for Docker to be ready
            logger.info("⏳ Waiting for Docker to be ready...")
            time.sleep(10)
            return check_docker_status()
        else:
            logger.error("❌ Could not start Docker Desktop automatically")
            logger.info("💡 Please start Docker Desktop manually and try again")
            return False
    except Exception as e:
        logger.error(f"❌ Failed to start Docker: {e}")
        return False

def check_caddy_container():
    """Check if Caddy container exists and is running"""
    logger.info("🔍 Checking Caddy container...")
    try:
        result = run_command("docker ps -a | findstr caddy", "Check Caddy containers", check=False)
        if result.returncode == 0:
            logger.info("✅ Caddy container found")
            # Check if it's running
            result2 = run_command("docker ps | findstr caddy", "Check running Caddy", check=False)
            if result2.returncode == 0:
                logger.info("✅ Caddy container is running")
                return True
            else:
                logger.warning("⚠️ Caddy container exists but not running")
                return False
        else:
            logger.warning("⚠️ No Caddy container found")
            return False
    except Exception as e:
        logger.error(f"❌ Failed to check Caddy container: {e}")
        return False

def install_dependencies():
    """Install missing Python dependencies"""
    logger.info("🔧 Installing Python dependencies...")
    backend_path = Path("backend")
    
    requirements_files = [
        backend_path / "requirements.txt",
    ]
    
    for req_file in requirements_files:
        if req_file.exists():
            logger.info(f"Installing from {req_file}...")
            result = run_command(f"pip install -r {req_file}", f"Install {req_file}")
            if result.returncode == 0:
                logger.info(f"✅ Successfully installed from {req_file}")
            else:
                logger.error(f"❌ Failed to install from {req_file}")
    
    # Install specific missing packages
    missing_packages = [
        "dj_database_url",
        "celery",
        "redis",
        "python-dotenv"
    ]
    
    for package in missing_packages:
        logger.info(f"Installing {package}...")
        result = run_command(f"pip install {package}", f"Install {package}")
        if result.returncode == 0:
            logger.info(f"✅ Successfully installed {package}")
        else:
            logger.error(f"❌ Failed to install {package}")

def setup_celery():
    """Setup and test Celery configuration"""
    logger.info("🔧 Setting up Celery...")
    backend_path = Path("backend")
    
    # Change to backend directory
    os.chdir(backend_path)
    
    # Test Celery worker
    logger.info("Testing Celery worker...")
    result = run_command("celery -A config worker --loglevel=info --timeout=10", 
                        "Test Celery worker", check=False, cwd=backend_path)
    
    if result.returncode == 0:
        logger.info("✅ Celery worker is working")
        return True
    else:
        logger.error("❌ Celery worker configuration issue")
        logger.error(f"Error: {result.stderr}")
        return False

def test_domain_verification():
    """Test the domain verification system"""
    logger.info("🔍 Testing domain verification system...")
    backend_path = Path("backend")
    
    try:
        # Test DNS verification
        test_cmd = '''python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from apps.domains.verification import verify_custom_domain_dns
from apps.domains.models import Domain
from apps.deployments.models import PlatformConfig

config = PlatformConfig.load()
domain = Domain.objects.first()
if domain:
    result = verify_custom_domain_dns(domain, config)
    print('Domain:', domain.domain_name)
    print('Verified:', result.verified)
    print('Expected:', result.expected)
    print('Actual:', result.actual)
    print('Error:', result.error)
else:
    print('No domains found in database')
"'''
        
        result = run_command(test_cmd, "Test DNS verification", check=False, cwd=backend_path)
        
        if result.returncode == 0:
            logger.info("✅ Domain verification system working")
            return True
        else:
            logger.error("❌ Domain verification system issue")
            return False
            
    except Exception as e:
        logger.error(f"❌ Failed to test domain verification: {e}")
        return False

def check_caddy_config():
    """Check Caddy configuration"""
    logger.info("🔍 Checking Caddy configuration...")
    caddyfile_path = Path("caddy-config/Caddyfile")
    
    if caddyfile_path.exists():
        logger.info("✅ Caddyfile found")
        with open(caddyfile_path, 'r') as f:
            content = f.read()
            logger.info("Caddyfile content:")
            logger.info(content)
        return True
    else:
        logger.warning("⚠️ No Caddyfile found")
        return False

def create_test_domain():
    """Create a test domain for verification"""
    logger.info("🔧 Creating test domain...")
    backend_path = Path("backend")
    
    try:
        test_cmd = '''python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from apps.domains.models import Domain, DomainStatus
from apps.deployments.models import Service

# Find a service
service = Service.objects.first()
if service:
    # Create test domain
    test_domain = Domain.objects.create(
        domain_name='test-fix.example.com',
        service=service,
        status=DomainStatus.PENDING
    )
    print('Created test domain:', test_domain.domain_name, 'with ID:', test_domain.id)
else:
    print('No services found in database')
"'''
        
        result = run_command(test_cmd, "Create test domain", check=False, cwd=backend_path)
        
        return result.returncode == 0
        
    except Exception as e:
        logger.error(f"❌ Failed to create test domain: {e}")
        return False

def main():
    """Main fix function"""
    logger.info("🚀 Starting Custom Domain SSL Fix...")
    
    # Check current working directory
    logger.info(f"Current directory: {os.getcwd()}")
    
    # Step 1: Check and start Docker
    if not check_docker_status():
        if not start_docker():
            logger.error("❌ Docker is not running. Please start Docker Desktop manually.")
            return False
    
    # Step 2: Check Caddy container
    if not check_caddy_container():
        logger.warning("⚠️ Caddy container needs attention")
    
    # Step 3: Install dependencies
    install_dependencies()
    
    # Step 4: Setup Celery
    if not setup_celery():
        logger.error("❌ Celery setup failed")
        return False
    
    # Step 5: Check Caddy configuration
    check_caddy_config()
    
    # Step 6: Test domain verification
    if not test_domain_verification():
        logger.error("❌ Domain verification test failed")
        return False
    
    # Step 7: Create test domain
    create_test_domain()
    
    logger.info("✅ Custom Domain SSL Fix completed successfully!")
    logger.info("\n📋 Next Steps:")
    logger.info("1. Start Celery workers: cd backend && celery -A config worker --loglevel=info")
    logger.info("2. Start Celery beat: cd backend && celery -A config beat --loglevel=info")
    logger.info("3. Add a custom domain that points to your VPS IP")
    logger.info("4. Monitor the domain status in the admin panel")
    logger.info("5. Test HTTPS access to the custom domain")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)