# Custom Domain SSL Fix - Complete Solution

Based on the investigation, here's the complete fix for the custom domain SSL issue:

## 📋 Problem Summary
- Custom domains show DNS pointing to VPS but PaaS status hasn't updated
- SSL certificates haven't been issued
- System is 80% complete but has infrastructure issues

## 🔧 Root Causes
1. **Docker Desktop not running** - Prevents Caddy reloads
2. **Celery workers not running** - Prevents background task processing
3. **Missing Python dependencies** - Celery and Django setup issues

## 🚀 Complete Fix Steps

### Step 1: Start Docker Desktop
```bash
# Start Docker Desktop application
# This is required for Caddy container to work
```

### Step 2: Install Dependencies
```bash
cd backend
pip install dj_database_url celery redis python-dotenv
```

### Step 3: Start Celery Services
```bash
# Open three separate terminals and run:

# Terminal 1: Start Celery worker
cd backend
celery -A config worker --loglevel=info

# Terminal 2: Start Celery beat (scheduler)
cd backend  
celery -A config beat --loglevel=info

# Terminal 3: Run domain verification tasks
cd backend
python -c "
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from apps.domains.models import Domain
from apps.domains.tasks import verify_dns_and_provision_ssl_task

# Process all pending domains
domains = Domain.objects.filter(status__in=['pending', 'dns_pending'])
print(f'Processing {domains.count()} pending domains...')

for domain in domains:
    print(f'Verifying domain: {domain.domain_name}')
    verify_dns_and_provision_ssl_task(domain.id)
    print(f'Task queued for {domain.domain_name}')
"
```

### Step 4: Verify System Status
```bash
# Check domain statuses
cd backend
python -c "
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from apps.domains.models import Domain, DomainStatus

print('=== DOMAIN STATUS ===')
for domain in Domain.objects.all():
    print(f'{domain.domain_name}: {domain.status} (Verified: {domain.verified}, SSL: {domain.ssl_active})')
"
```

### Step 5: Test Custom Domain Addition
```bash
# Add a new custom domain that points to your VPS IP (209.159.152.123)
# Use the web interface or API to add the domain
# The system should:
# 1. Create domain record with status='pending'
# 2. Celery task should verify DNS
# 3. Update status to 'dns_verified' if DNS is correct
# 4. Caddy should reload and include the domain
# 5. SSL should be issued automatically via Let's Encrypt
```

## 🔍 Verification Steps

### 1. Check Database Status
```bash
cd backend
python manage.py shell
# Then run:
from apps.domains.models import Domain
domain = Domain.objects.get(domain_name='your-domain.com')
print(f"Status: {domain.status}")
print(f"Verified: {domain.verified}")
print(f"SSL Active: {domain.ssl_active}")
```

### 2. Check Caddy Configuration
```bash
# Check the Caddyfile
cat caddy-config/Caddyfile
# Look for your custom domain with on_demand TLS configuration
```

### 3. Test HTTPS Access
```bash
# Test HTTPS access to your custom domain
curl -I https://your-domain.com
# Should return 200 OK with valid SSL certificate
```

## 🚨 Troubleshooting

### If Docker is not accessible:
```bash
# Check Docker status
docker info

# If Docker Desktop is not running, start it and wait for it to be ready
# Then restart containers if needed
```

### If Celery tasks are not running:
```bash
# Check Celery worker status
celery -A config inspect stats

# Check running tasks
celery -A config inspect active
```

### If domain verification fails:
```bash
# Check DNS lookup for your domain
nslookup your-domain.com

# Should return IP 209.159.152.123 or CNAME to grid.smsly.cloud
```

## 📊 Expected Workflow
1. Add custom domain → Status: `pending`
2. DNS verification task runs → Status: `dns_verified` (if DNS correct)
3. Caddy reloads → Domain added to Caddyfile
4. First HTTPS request → SSL certificate issued
5. SSL monitoring task → Status: `active`, SSL: `True`

## 🎯 Success Indicators
- Domain status changes from `pending` to `dns_verified` to `active`
- `ssl_active` becomes `True` in database
- Custom domain accessible via HTTPS
- No errors in Celery worker logs

This fix addresses the core infrastructure issues and should make the custom domain SSL system work properly.