# Jules Task: SMSLY Hosting — Community/Pro/Enterprise Tier System

## Objective

Implement a feature-gating and license enforcement system for SMSLY Hosting with three tiers: **Community** (free), **Pro** ($29/mo), and **Enterprise** ($99/mo). The system must use server-validated + RSA-signed license keys to prevent unauthorized access to premium features.

This is a self-hosted platform (Docker Compose). Users install it on their own VPS. The license system controls which features are available based on their subscription tier.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  SMSLY Hosting Instance              │
│                                                     │
│  ┌─────────────┐    ┌──────────────────┐           │
│  │ License     │───▶│ license.smsly.cloud │ (daily)│
│  │ Validator   │    └──────────────────┘           │
│  │             │    ┌──────────────────┐           │
│  │             │───▶│ RSA Public Key   │ (offline) │
│  └──────┬──────┘    └──────────────────┘           │
│         │                                           │
│  ┌──────▼──────┐                                   │
│  │ Tier Gate   │  @require_tier('pro')              │
│  │ Middleware  │  <RequiresTier tier="pro">          │
│  └──────┬──────┘                                   │
│         │                                           │
│  ┌──────▼──────┐                                   │
│  │ Feature     │  AI, Autoscaler, Custom Domains... │
│  │ Modules     │                                   │
│  └─────────────┘                                   │
└─────────────────────────────────────────────────────┘
```

---

## Tier Definitions

### Community (Free — No License Required)

| Feature | Limit |
|---------|-------|
| Services | 3 max |
| Deployments | 5 per day |
| Health checks | Basic (liveness only) |
| Deploy mode | Manual only (no auto-deploy) |
| AI features | ❌ Disabled |
| Autoscaler | ❌ Disabled |
| Custom domains | ❌ Disabled (uses `*.smsly.localhost` subdomains) |
| SSL certificates | ❌ Disabled (self-signed only) |
| Marketplace/Addons | ❌ Read-only (cannot install) |
| Team members | 1 (owner only) |
| Backups | Manual only, 3-day retention |
| Branding | "Powered by SMSLY Hosting" badge required in footer |
| Support | Community forums only |
| Functions (serverless) | ❌ Disabled |
| Tunnels | ❌ Disabled |
| Topology view | ❌ Disabled |
| Transfers | ❌ Disabled |

### Pro ($29/month)

| Feature | Limit |
|---------|-------|
| Services | Unlimited |
| Deployments | Unlimited |
| Health checks | Full (liveness + readiness + custom endpoints) |
| Deploy mode | Auto-deploy from Git webhooks |
| AI features | ✅ Auto-fix, AI diagnosis, smart recommendations |
| Autoscaler | ✅ Enabled |
| Custom domains | ✅ Unlimited |
| SSL certificates | ✅ Auto Let's Encrypt |
| Marketplace/Addons | ✅ Install and use |
| Team members | 5 |
| Backups | Automated daily, 30-day retention |
| Branding | ✅ Remove "Powered by" badge |
| Support | Email support (48h SLA) |
| Functions | ✅ Enabled |
| Tunnels | ✅ Enabled (3 max) |
| Topology view | ✅ Enabled |
| Transfers | ✅ Enabled |

### Enterprise ($99/month)

| Feature | Limit |
|---------|-------|
| Everything in Pro | ✅ |
| Team members | Unlimited |
| SSO/SAML | ✅ |
| Audit logs | ✅ Full audit trail |
| White-label | ✅ Full rebranding |
| Priority support | ✅ 4h SLA |
| Tunnels | Unlimited |
| Custom SLA | ✅ |
| Role-based access control | ✅ |
| Multi-node clustering | ✅ |

---

## Implementation Plan

### Phase 1: Backend License & Tier Infrastructure

#### 1.1 License Model

Create `backend/apps/licensing/` Django app:

```python
# backend/apps/licensing/models.py

from django.db import models
import json

class PlatformTier(models.TextChoices):
    COMMUNITY = 'community', 'Community'
    PRO = 'pro', 'Pro'
    ENTERPRISE = 'enterprise', 'Enterprise'

class PlatformLicense(models.Model):
    """
    Singleton model storing the current license state.
    Only one record should exist (use .load() class method).
    """
    license_key = models.TextField(blank=True, default='')
    tier = models.CharField(
        max_length=20,
        choices=PlatformTier.choices,
        default=PlatformTier.COMMUNITY,
    )
    # RSA-signed license data (JSON string)
    license_data = models.TextField(blank=True, default='')
    
    # Cached validation state
    is_valid = models.BooleanField(default=False)
    last_validated = models.DateTimeField(null=True, blank=True)
    validation_error = models.TextField(blank=True, default='')
    
    # License metadata (extracted from signed payload)
    licensed_to = models.CharField(max_length=255, blank=True, default='')
    instance_id = models.CharField(max_length=64, blank=True, default='')
    expires_at = models.DateTimeField(null=True, blank=True)
    max_services = models.IntegerField(default=3)  # Community default
    max_team_members = models.IntegerField(default=1)
    
    # Payment info
    payment_provider = models.CharField(
        max_length=20,
        choices=[
            ('stripe', 'Stripe'),
            ('paystack', 'Paystack'),
            ('nowpayments', 'NowPayments'),
            ('paypal', 'PayPal'),
            ('manual', 'Manual'),
        ],
        blank=True, default='',
    )
    subscription_id = models.CharField(max_length=255, blank=True, default='')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Platform License'
    
    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
    
    @property
    def is_community(self):
        return self.tier == PlatformTier.COMMUNITY
    
    @property
    def is_pro(self):
        return self.tier in (PlatformTier.PRO, PlatformTier.ENTERPRISE)
    
    @property
    def is_enterprise(self):
        return self.tier == PlatformTier.ENTERPRISE
```

#### 1.2 License Validation Service

```python
# backend/apps/licensing/validator.py

"""
License validation with two verification paths:

1. ONLINE (primary): POST license_key to https://license.smsly.cloud/v1/validate
   - Returns signed JSON with tier, expiry, features
   - Runs on startup + every 24 hours via Celery beat
   
2. OFFLINE (fallback): RSA signature verification
   - license_data is a base64-encoded JSON payload signed with SMSLY's RSA private key
   - Public key is embedded in the codebase
   - Used when license server is unreachable
   - Grace period: 7 days offline before downgrading to Community
"""
```

**RSA Key Pair:**
- Generate a 4096-bit RSA key pair
- Private key: stored on SMSLY's license server (NEVER in this repo)
- Public key: embedded in `backend/apps/licensing/keys/public.pem`

**License Payload Format (signed JSON):**
```json
{
  "license_id": "lic_abc123",
  "tier": "pro",
  "licensed_to": "user@example.com",
  "instance_id": "sha256-of-machine-id",
  "max_services": -1,
  "max_team_members": 5,
  "features": ["ai", "autoscaler", "custom_domains", "ssl", "marketplace", "functions", "tunnels", "topology", "transfers"],
  "issued_at": "2025-01-01T00:00:00Z",
  "expires_at": "2026-01-01T00:00:00Z"
}
```

**Instance Fingerprint:**
Generate a unique instance ID from the machine's hardware/container ID. Store in `.instance_id` file in the install directory. This prevents a single license from being used on multiple servers.

```python
def get_instance_id():
    """Generate deterministic instance fingerprint."""
    import hashlib, uuid, os
    id_file = os.path.join(os.environ.get('INSTALL_DIR', '/opt/smsly-hosting'), '.instance_id')
    if os.path.exists(id_file):
        return open(id_file).read().strip()
    # Generate from machine-id + random salt
    machine_id = ''
    for path in ['/etc/machine-id', '/var/lib/dbus/machine-id']:
        if os.path.exists(path):
            machine_id = open(path).read().strip()
            break
    if not machine_id:
        machine_id = str(uuid.uuid4())
    instance_id = hashlib.sha256(f"{machine_id}-{uuid.uuid4()}".encode()).hexdigest()[:32]
    with open(id_file, 'w') as f:
        f.write(instance_id)
    return instance_id
```

#### 1.3 Tier Gate Decorator

```python
# backend/apps/licensing/decorators.py

from functools import wraps
from rest_framework.response import Response
from rest_framework import status

def require_tier(*allowed_tiers):
    """
    Decorator for DRF views that gates access by platform tier.
    
    Usage:
        @require_tier('pro', 'enterprise')
        def my_premium_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            from apps.licensing.models import PlatformLicense
            license = PlatformLicense.load()
            if license.tier not in allowed_tiers:
                return Response(
                    {
                        'error': 'upgrade_required',
                        'message': f'This feature requires {allowed_tiers[0].title()} tier or above.',
                        'current_tier': license.tier,
                        'required_tier': allowed_tiers[0],
                        'upgrade_url': '/billing',
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
```

#### 1.4 Tier Limits Middleware

```python
# backend/apps/licensing/middleware.py

class TierLimitsMiddleware:
    """
    Enforce tier-based limits on resource creation.
    
    Checks:
    - Service count limit (Community: 3, Pro: unlimited)
    - Daily deployment limit (Community: 5)
    - Team member limit
    """
```

#### 1.5 Apply Tier Gates to Existing Endpoints

Apply `@require_tier` to these existing views/tasks:

| Endpoint/Feature | Gate |
|-----------------|------|
| AI auto-fix (`tasks.py: _post_deploy_monitor` auto-fix branch) | `pro` |
| AI diagnosis (`tasks_ai.py`) | `pro` |
| Autoscaler (`autoscaler/`) | `pro` |
| Custom domains (in `local.py` domain handling) | `pro` |
| SSL provisioning | `pro` |
| Marketplace addon install | `pro` |
| Git webhook auto-deploy | `pro` |
| Functions deploy | `pro` |
| Tunnels | `pro` |
| Topology view API | `pro` |
| Transfers API | `pro` |
| Automated backups | `pro` |
| SSO/SAML | `enterprise` |
| Audit logs | `enterprise` |
| White-label config | `enterprise` |
| RBAC | `enterprise` |
| Service creation (enforce limit) | Check `max_services` |
| Team member invite | Check `max_team_members` |
| Daily deployments | Check daily limit for Community |

#### 1.6 License Validation Celery Task

```python
# backend/apps/licensing/tasks.py

@shared_task
def validate_license_task():
    """
    Runs every 24 hours via celery-beat.
    1. Try online validation against license.smsly.cloud
    2. If offline, verify RSA signature + check expiry
    3. If expired/invalid, downgrade to Community tier
    4. Update PlatformLicense model
    """
```

Add to celery-beat schedule in `config/celery.py`.

#### 1.7 License API Endpoints

```
POST /api/v1/licensing/activate/     — Submit license key, validate, activate tier
GET  /api/v1/licensing/status/       — Current tier, expiry, features, limits
POST /api/v1/licensing/deactivate/   — Remove license, downgrade to Community
GET  /api/v1/licensing/features/     — List all features with enabled/disabled status
```

---

### Phase 2: Payment Integration

#### 2.1 Payment Providers

Create `backend/apps/billing/` Django app with adapters for each provider:

```python
# backend/apps/billing/providers/base.py
class PaymentProvider(ABC):
    @abstractmethod
    def create_subscription(self, plan_id, customer_email, **kwargs): ...
    
    @abstractmethod
    def cancel_subscription(self, subscription_id): ...
    
    @abstractmethod
    def verify_webhook(self, payload, signature): ...

# backend/apps/billing/providers/stripe_provider.py
# backend/apps/billing/providers/paystack_provider.py
# backend/apps/billing/providers/nowpayments_provider.py
# backend/apps/billing/providers/paypal_provider.py
```

#### 2.2 Webhook Handlers

```
POST /api/v1/billing/webhook/stripe/
POST /api/v1/billing/webhook/paystack/
POST /api/v1/billing/webhook/nowpayments/
POST /api/v1/billing/webhook/paypal/
```

Each webhook handler:
1. Verify signature (provider-specific)
2. On successful payment → call license server to issue/renew license
3. On cancellation/failure → downgrade to Community after grace period
4. Log all events to `BillingEvent` model for audit trail

#### 2.3 Pricing Plans

```python
PLANS = {
    'pro_monthly': {
        'tier': 'pro',
        'price_usd': 29,
        'interval': 'month',
        'stripe_price_id': 'price_xxx',
        'paystack_plan_code': 'PLN_xxx',
        'nowpayments_plan_id': 'xxx',
    },
    'pro_yearly': {
        'tier': 'pro',
        'price_usd': 290,  # ~2 months free
        'interval': 'year',
    },
    'enterprise_monthly': {
        'tier': 'enterprise',
        'price_usd': 99,
        'interval': 'month',
    },
    'enterprise_yearly': {
        'tier': 'enterprise',
        'price_usd': 990,
        'interval': 'year',
    },
}
```

---

### Phase 3: Frontend Tier Gates

#### 3.1 Tier Context Provider

```tsx
// frontend/src/context/TierContext.tsx

interface TierState {
  tier: 'community' | 'pro' | 'enterprise';
  features: string[];
  limits: {
    maxServices: number;
    maxTeamMembers: number;
    dailyDeployments: number;
  };
  expiresAt: string | null;
  isLoading: boolean;
}

export function TierProvider({ children }) {
  // Fetch from /api/v1/licensing/status/ on mount
  // Cache in state, refresh every hour
}

export function useTier() {
  return useContext(TierContext);
}
```

#### 3.2 Feature Gate Component

```tsx
// frontend/src/components/licensing/RequiresTier.tsx

interface Props {
  tier: 'pro' | 'enterprise';
  children: React.ReactNode;
  fallback?: React.ReactNode;  // Shown when tier insufficient
}

export function RequiresTier({ tier, children, fallback }: Props) {
  const { tier: currentTier } = useTier();
  
  if (currentTier >= tier) return children;
  
  return fallback || <UpgradePrompt requiredTier={tier} />;
}
```

#### 3.3 Upgrade Prompt Component

```tsx
// frontend/src/components/licensing/UpgradePrompt.tsx

// Beautiful card that shows:
// - Current tier
// - Required tier for this feature
// - Price
// - CTA button → /billing
// 
// Design: glassmorphism card with gradient border,
// lock icon, feature comparison mini-table
```

#### 3.4 Apply Gates to Frontend Pages

Wrap these pages/components with `<RequiresTier>`:

```tsx
// In each gated page:
export default function AutoscalerPage() {
  return (
    <RequiresTier tier="pro">
      <AutoscalerContent />
    </RequiresTier>
  );
}
```

Pages to gate: `/autoscaler`, `/functions`, `/tunnels`, `/topology`, `/transfers`, `/intelligence`

#### 3.5 Update Billing Page

The existing `/billing` page should show:
1. Current tier badge
2. Plan comparison table (Community vs Pro vs Enterprise)
3. Payment method selector (Stripe / Paystack / NowPayments / PayPal)
4. License key input field (for manual activation)
5. Subscription management (cancel, change plan)
6. Invoice history

#### 3.6 Community Branding Badge

```tsx
// frontend/src/components/licensing/PoweredByBadge.tsx

// Renders "Powered by SMSLY Hosting" in the footer
// Only visible on Community tier
// Cannot be hidden without upgrading (check tier in component)
```

---

### Phase 4: License Server Stub

> **NOTE:** The actual license server (`license.smsly.cloud`) is a separate
> microservice. For now, create a STUB that the platform can call.
> The stub should be a simple Django endpoint within the platform itself
> that can be replaced with the real server later.

```python
# backend/apps/licensing/stub_server.py

# For development/testing:
# POST /api/v1/licensing/stub/validate/
#   Input: { "license_key": "...", "instance_id": "..." }
#   Output: { "valid": true, "tier": "pro", "expires_at": "...", "signature": "..." }
#
# This generates real RSA-signed payloads using a dev key pair.
# In production, this endpoint is DISABLED and replaced by license.smsly.cloud
```

Generate a development RSA key pair for testing:
```bash
openssl genrsa -out dev_private.pem 4096
openssl rsa -in dev_private.pem -pubout -out dev_public.pem
```

Store `dev_public.pem` at `backend/apps/licensing/keys/public.pem`
Store `dev_private.pem` at `backend/apps/licensing/keys/dev_private.pem` (gitignored!)

---

## File Structure

```
backend/apps/licensing/
├── __init__.py
├── admin.py              # Django admin for PlatformLicense
├── apps.py
├── decorators.py          # @require_tier decorator
├── keys/
│   ├── public.pem         # RSA public key (committed)
│   └── dev_private.pem    # Dev-only private key (GITIGNORED)
├── middleware.py           # TierLimitsMiddleware
├── migrations/
├── models.py              # PlatformLicense, PlatformTier
├── serializers.py
├── signals.py             # Post-save signal to broadcast tier changes
├── stub_server.py         # Dev license validation stub
├── tasks.py               # validate_license_task (celery-beat)
├── urls.py
├── validator.py           # RSA + online validation logic
└── views.py               # License API endpoints

backend/apps/billing/
├── __init__.py
├── admin.py
├── apps.py
├── migrations/
├── models.py              # BillingEvent, Subscription
├── providers/
│   ├── __init__.py
│   ├── base.py            # Abstract PaymentProvider
│   ├── stripe_provider.py
│   ├── paystack_provider.py
│   ├── nowpayments_provider.py
│   └── paypal_provider.py
├── serializers.py
├── urls.py
├── views.py               # Billing API + webhook handlers
└── webhooks.py            # Webhook verification + processing

frontend/src/
├── context/
│   └── TierContext.tsx
├── components/licensing/
│   ├── RequiresTier.tsx
│   ├── UpgradePrompt.tsx
│   ├── PoweredByBadge.tsx
│   └── TierBadge.tsx
└── (update existing pages with tier gates)
```

---

## Environment Variables

Add to `.env`:
```bash
# Licensing
SMSLY_LICENSE_KEY=              # User's license key (empty = Community)
SMSLY_LICENSE_SERVER=https://license.smsly.cloud
SMSLY_LICENSE_OFFLINE_GRACE_DAYS=7

# Stripe
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRO_PRICE_ID=
STRIPE_ENTERPRISE_PRICE_ID=

# Paystack
PAYSTACK_SECRET_KEY=
PAYSTACK_WEBHOOK_SECRET=
PAYSTACK_PRO_PLAN_CODE=
PAYSTACK_ENTERPRISE_PLAN_CODE=

# NowPayments
NOWPAYMENTS_API_KEY=
NOWPAYMENTS_IPN_SECRET=

# PayPal
PAYPAL_CLIENT_ID=
PAYPAL_CLIENT_SECRET=
PAYPAL_WEBHOOK_ID=
```

---

## Testing Checklist

- [ ] Community tier: can create max 3 services, 4th fails with 402
- [ ] Community tier: deploy limit enforced (5/day)
- [ ] Community tier: AI/autoscaler/custom-domain endpoints return 402
- [ ] Pro tier: all Pro features accessible after license activation
- [ ] Enterprise tier: SSO/audit/RBAC accessible
- [ ] License activation: key submitted → validated → tier upgraded
- [ ] License expiry: expired license → graceful downgrade to Community
- [ ] Offline mode: platform works for 7 days without license server
- [ ] Payment webhook: Stripe payment → license issued → tier upgraded
- [ ] Payment webhook: Paystack payment → license issued → tier upgraded
- [ ] Payment cancellation → grace period → downgrade to Community
- [ ] Frontend: gated pages show UpgradePrompt on Community tier
- [ ] Frontend: PoweredByBadge visible only on Community
- [ ] Instance fingerprint: same key rejected on different server
- [ ] RSA signature: tampered license data fails validation

---

## Security Notes

1. **NEVER commit the RSA private key** — only the public key goes in the repo
2. **Webhook signatures must be verified** — all providers support HMAC verification
3. **License key format**: use `smsly_pro_` and `smsly_ent_` prefixes for easy identification
4. **Grace periods**: 3 days after payment failure before downgrade, 7 days offline tolerance
5. **Rate limit** the license activation endpoint to prevent brute-force key guessing
6. **Log all license state transitions** to `BillingEvent` for audit trail
