# Grid Enterprise — White-Label & Enterprise Plan

## Phase 1: White-Label Core (Week 1–2)

### 1.1 Platform Branding Config Model

Add a `BrandingConfig` to the existing `PlatformConfig` singleton:

```python
# backend/apps/deployments/models_core.py — extend PlatformConfig with:

class BrandingConfig(models.Model):
    platform = models.OneToOneField(PlatformConfig, on_delete=models.CASCADE, related_name="branding")

    # Identity
    product_name = models.CharField(max_length=100, default="Grid")
    company_name = models.CharField(max_length=200, default="SMSLYCLOUD")
    tagline = models.CharField(max_length=300, blank=True)
    meta_description = models.TextField(blank=True)

    # Visual
    logo_url = models.URLField(blank=True, help_text="URL to logo (navbar/sidebar)")
    favicon_url = models.URLField(blank=True)
    login_background_url = models.URLField(blank=True)
    logo_square_url = models.URLField(blank=True, help_text="Square icon for sidebar collapsed state")

    # Theme
    primary_color = models.CharField(max_length=7, default="#10b981", help_text="Hex color")
    primary_color_dark = models.CharField(max_length=7, default="#34d399")
    secondary_color = models.CharField(max_length=7, default="#6366f1")
    accent_color = models.CharField(max_length=7, default="#f59e0b")
    border_radius = models.CharField(max_length=10, default="0.5rem")

    # Footer
    footer_text = models.TextField(blank=True)
    copyright_text = models.CharField(max_length=300, blank=True)
    hide_powered_by = models.BooleanField(default=False)

    # Links
    support_url = models.URLField(blank=True)
    docs_url = models.URLField(blank=True)
    terms_url = models.URLField(blank=True)
    privacy_url = models.URLField(blank=True)

    # Email branding
    email_from_name = models.CharField(max_length=100, blank=True)
    email_logo_url = models.URLField(blank=True)

    # Feature flags
    enable_custom_css = models.BooleanField(default=False)
    custom_css = models.TextField(blank=True)
    enable_custom_js = models.BooleanField(default=False)
    custom_js = models.TextField(blank=True)

    updated_at = models.DateTimeField(auto_now=True)
```

Migration + admin panel, REST endpoint at `GET/PATCH /api/v1/platform/branding/`.

### 1.2 CSS Custom Properties Injection

Instead of hardcoded `globals.css`, serve a dynamic CSS endpoint:

```
GET /api/v1/platform/branding.css  →  Generated CSS with custom properties
```

The CSS endpoint reads `BrandingConfig` from DB and returns:

```css
:root {
  --primary: ${primary_color};
  --primary-foreground: #ffffff;
  --secondary: ${secondary_color};
  --accent: ${accent_color};
  --radius: ${border_radius};
  /* ... 30+ derived color shades computed server-side ... */
}
.dark {
  --primary: ${primary_color_dark};
}
```

Frontend loads this via `<link rel="stylesheet" href="/api/v1/platform/branding.css">` in `layout.tsx`.

### 1.3 Branding Context Provider

```tsx
// frontend/src/context/BrandingContext.tsx

interface BrandingConfig {
  productName: string;
  companyName: string;
  logoUrl: string | null;
  faviconUrl: string | null;
  primaryColor: string;
  hidePoweredBy: boolean;
  footerText: string | null;
  // ... all fields
}

// Fetches GET /api/v1/platform/branding/ on mount
// Caches in localStorage with TTL
// Provides useBranding() hook
```

### 1.4 Replace All Hardcoded References

Use `useBranding()` hook everywhere. Priority order:

| Component | Current | Replace With |
|-----------|---------|--------------|
| `layout.tsx` metadata title | `"Grid — Free..."` | `branding.productName + " — " + branding.metaDescription` |
| `layout.tsx` favicon | `/images/mini_logo.png` | `branding.faviconUrl \|\| "/images/mini_logo.png"` |
| `Navbar.tsx` logo + name | `<Image src="/images/...">` + `Grid` | `branding.logoUrl` + `branding.productName` |
| `Sidebar.tsx` name + icon | `Grid` + emerald block | `branding.productName.charAt(0)` with theme color |
| `Footer.tsx` text + copyright | Hardcoded | `branding.footerText \|\| branding.copyrightText` |
| `PoweredByBadge.tsx` | Shows "Powered by SMSLY" | Checks `branding.hidePoweredBy` |
| `globals.css` | Hardcoded HSL values | Dynamic CSS endpoint above |
| `login/page.tsx` | Default title | `branding.productName` |
| `register/page.tsx` | Default title | `branding.productName` |
| Email templates | Hardcoded | `email_from_name`, `email_logo_url` |

### 1.5 Settings UI Page

Add a "Branding" tab to `frontend/src/app/settings/page.tsx`:

- Product name, company name, tagline inputs
- Logo/favicon upload → base64 or S3 URL
- Color pickers (6 hex inputs with live preview)
- Footer text editor
- Toggle: Hide "Powered by..." badge
- Custom CSS/JS textareas (gated to enterprise tier)
- Live preview panel showing navbar/sidebar/login changes in real-time
- Save button → PATCH `/api/v1/platform/branding/`
- Reset to defaults button

---

## Phase 2: Landing Page System (Week 2–3)

### 2.1 Configurable Landing Page

Create a `LandingPage` model or extend `BrandingConfig`:

```python
class LandingSection(models.Model):
    branding = models.ForeignKey(BrandingConfig, related_name="sections")
    order = models.IntegerField(default=0)
    section_type = models.CharField(choices=[
        ("hero", "Hero"),
        ("features", "Features Grid"),
        ("stats", "Stats Counter"),
        ("pricing", "Pricing Table"),
        ("testimonials", "Testimonials"),
        ("cta", "Call to Action"),
        ("logos", "Trusted By Logos"),
        ("custom_html", "Custom HTML"),
    ])
    title = models.CharField(max_length=300, blank=True)
    subtitle = models.TextField(blank=True)
    config = models.JSONField(default=dict)  # flexible per-section config
    enabled = models.BooleanField(default=True)

class LandingFeature(models.Model):
    section = models.ForeignKey(LandingSection, related_name="features")
    icon = models.CharField(max_length=50)  # lucide icon name
    title = models.CharField(max_length=200)
    description = models.TextField()
    order = models.IntegerField(default=0)
```

### 2.2 Landing Page Renderer

Instead of the hardcoded `frontend/src/app/page.tsx`, build a dynamic renderer:

```tsx
// frontend/src/app/page.tsx
// Fetches GET /api/v1/platform/landing/ → sections[]
// Renders each section with the appropriate component:
//   HeroSection, FeaturesGrid, StatsCounter, PricingTable,
//   TestimonialsCarousel, CTASection, TrustedLogos, CustomHTMLSection
```

Default content ships in a fixture that looks exactly like the current landing page. Users customize via settings.

### 2.3 Public vs Authenticated Routing

| Route | Behavior |
|-------|----------|
| `/` | Landing page (public) if configured; redirect to `/dashboard` if landing disabled |
| `/dashboard` | Main app (requires auth) |
| `/login` | Branded login page |
| `/register` | Branded register (disabled if SSO enforced) |

This separation means the landing page is the marketing site, dashboard is the app — all under one domain, same deployment.

---

## Phase 3: Enterprise Tier System (Week 3–4)

### 3.1 Tier Definitions

```python
TIERS = {
    "community": {
        "max_services": 3,
        "max_team_members": 1,
        "max_servers": 1,
        "max_deployments_per_day": 10,
        "features": {
            "git_deploy": True,
            "docker_deploy": True,
            "ssl": True,
            "custom_domain": True,
            "white_label": False,
            "sso": False,
            "audit_logs": False,
            "priority_support": False,
            "custom_smtp": False,
            "backup_retention_days": 7,
            "preview_environments": False,
            "k8s_support": False,
            "multi_server": False,
            "vpn_mesh": False,
        }
    },
    "pro": {
        "max_services": 50,
        "max_team_members": 10,
        "max_servers": 5,
        "max_deployments_per_day": -1,
        "features": {
            # Same as above +
            "white_label": True,
            "preview_environments": True,
            "backup_retention_days": 30,
            "multi_server": True,
            "custom_smtp": True,
        }
    },
    "team": {
        "max_services": 200,
        "max_team_members": 50,
        "max_servers": 20,
        "features": {
            # Same as pro +
            "audit_logs": True,
            "sso": True,
            "k8s_support": True,
            "vpn_mesh": True,
            "backup_retention_days": 90,
        }
    },
    "enterprise": {
        "max_services": -1,
        "max_team_members": -1,
        "max_servers": -1,
        "features": {
            # Everything + priority_support, dedicated slack, custom SLA
            # Custom contracts via sales
        }
    },
}
```

Already have the `PlatformLicense` model — extend it to validate tier features.

### 3.2 License Key Activation

```python
# backend/apps/licensing/license_manager.py

class LicenseManager:
    @staticmethod
    def activate(key: str) -> PlatformLicense:
        # Validate against SMSLY license server
        # POST https://license.smsly.cloud/api/v1/activate
        # Returns: tier, max_services, features, expiry
        # Stores in PlatformLicense singleton
        pass

    @staticmethod
    def check_feature(feature: str) -> bool:
        license = PlatformLicense.get_solo()
        return license.features.get(feature, False)

    @staticmethod
    def refresh():
        # Periodic refresh (every 24h) from license server
        pass
```

License is tied to domain. Offline grace period (72h) if license server unreachable.

### 3.3 Feature Gate Middleware

```python
# backend/apps/licensing/middleware.py

class TierGateMiddleware:
    # Checks request against feature flags
    # e.g., POST /api/v1/services/ → check max_services limit
    #       POST /api/v1/teams/invite/ → check max_team_members

# Decorator for views:
@require_feature("white_label")
def branding_view(request): ...
```

Frontend equivalent — `TierContext` already exists, extend it:

```tsx
const { canAccess } = useTier();
{canAccess("white_label") && <BrandingTab />}
```

---

## Phase 4: Enterprise Features (Week 4–6)

### 4.1 SSO / SAML / OIDC

Already have OAuth (GitHub, Google). Add:

```python
# backend/apps/auth/sso.py
# - SAML 2.0 Service Provider (python3-saml)
# - OpenID Connect Relying Party
# - Just-in-time user provisioning
# - IdP-initiated and SP-initiated flows
# - Configurable per team via settings UI
```

### 4.2 Audit Logs

Already have an audit pattern in the codebase. Formalize:

```python
class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True)
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=50)
    resource_id = models.CharField(max_length=100)
    details = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["resource_type", "resource_id"]),
            models.Index(fields=["created_at"]),
        ]
```

Searchable, exportable (CSV/JSON), retention policy configurable.

### 4.3 Custom SMTP

Already configurable via `.env`. Add UI in settings:

- SMTP host, port, TLS, username, password
- "Send test email" button
- Override per-team for multi-tenant deployments

### 4.4 Role-Based Access Control (RBAC)

```python
class Role(models.Model):
    name = models.CharField(max_length=50)
    permissions = models.JSONField(default=list)
    # ["service.create", "service.delete", "team.manage", "billing.view", ...]

class TeamMembership(models.Model):
    user = models.ForeignKey(User)
    team = models.ForeignKey(Team)
    role = models.ForeignKey(Role)
```

Pre-built roles: Owner, Admin, Developer, Viewer. Custom roles for enterprise.

### 4.5 Multi-Tenant Dashboard

For MSPs / platform teams managing multiple Grid instances:

```python
class Tenant(models.Model):
    name = models.CharField(max_length=100)
    domain = models.CharField(max_length=255)
    branding = models.OneToOneField(BrandingConfig)
    license = models.OneToOneField(PlatformLicense)
    isolated_db = models.BooleanField(default=False)  # separate DB for compliance
```

Multi-tenant admin dashboard to switch between tenants, manage each one's branding, limits, and users.

---

## Phase 5: Deployment & Distribution (Week 6–8)

### 5.1 Enterprise Installer

Extend `install.sh` with enterprise mode:

```bash
curl -fsSL https://cdn.smsly.cloud/grid/install.sh | bash -s -- \
  --enterprise \
  --license-key=GRD-XXXX-XXXX-XXXX \
  --product-name="Acme Cloud" \
  --primary-color="#7c3aed" \
  --logo-url="https://cdn.acme.com/logo.png"
```

Non-interactive mode reads from `grid-enterprise.env` or command-line flags.

### 5.2 Enterprise Docker Image

Pre-built Docker image with enterprise features enabled:

```
docker pull smslycloud/grid-enterprise:latest
docker run -e LICENSE_KEY=... -p 80:80 -p 443:443 smslycloud/grid-enterprise
```

### 5.3 Helm Chart — Enterprise Profile

```yaml
# values.enterprise.yaml
global:
  licenseKey: "GRD-XXXX-XXXX-XXXX"
branding:
  productName: "Acme Cloud"
  logoUrl: "https://cdn.acme.com/logo.png"
  primaryColor: "#7c3aed"
features:
  sso: true
  auditLogs: true
  whiteLabel: true
```

### 5.4 One-Click Deploy Buttons

```
- Deploy to AWS (CloudFormation)
- Deploy to GCP (Deployment Manager)
- Deploy to Azure (ARM template)
- Deploy to DigitalOcean (1-click marketplace)
- Deploy to Hetzner (cloud-init)
```

### 5.5 Managed Cloud — Enterprise Tier

```
app.grid.sh → Free community tier (branded "Powered by Grid")
                Pro tier (no badge, custom domain)
                Enterprise tier (full white-label, SSO, SLA)

Enterprise can also BYO license key to self-hosted instances.
```

---

## Phase 6: Monetization (Week 8+)

### 6.1 Pricing Tiers

| Tier | Price | Key Differentiator |
|------|-------|--------------------|
| Community | Free | Up to 3 services, 1 server, 1 user |
| Pro | $29/mo | 50 services, white-label, preview envs, multi-server |
| Team | $99/mo | 200 services, SSO, audit logs, K8s, VPN mesh |
| Enterprise | Custom | Unlimited everything, dedicated support, custom SLA, on-prem air-gapped |

### 6.2 License Server

```python
# Standalone service: license.smsly.cloud
# - Issues license keys
# - Validates activations
# - Tracks active instances (for analytics, not enforcement)
# - Webhook for license revocation
# - Stripe integration for auto-renewal
```

### 6.3 Stripe Billing Integration

Already have Stripe configured. Add:

- Subscription management UI
- Usage-based billing for service count / team members
- Invoice history
- Payment method management
- Upgrade/downgrade flows with proration
- Trial period (14 days enterprise features)

---

## Implementation Order (Priority)

1. **Week 1**: `BrandingConfig` model + migration + API endpoint
2. **Week 1**: Dynamic CSS endpoint (`GET /api/v1/platform/branding.css`)
3. **Week 2**: `BrandingContext` provider + replace hardcoded references (Navbar, Sidebar, Footer, layout.tsx)
4. **Week 2**: Settings → Branding tab UI
5. **Week 3**: Landing page dynamic renderer
6. **Week 3**: License key activation flow
7. **Week 4**: Feature gate middleware (backend + frontend)
8. **Week 5**: SSO/SAML
9. **Week 6**: Audit logs formalization
10. **Week 7**: Enterprise installer + Helm profile
11. **Week 8**: License server + Stripe integration

---

## Key Architecture Decisions

| Decision | Recommendation |
|----------|---------------|
| **DB vs File config** | DB (BrandingConfig model) — enables UI editing, no redeploy |
| **CSS approach** | Dynamic CSS endpoint, not Tailwind config rebuild — enables runtime color changes |
| **Logo storage** | S3/object store URL or base64 in DB — no filesystem dependency |
| **License validation** | Central license server with 72h offline grace period |
| **Multi-tenant isolation** | DB-level (PostgreSQL schemas or separate DBs per tenant) for enterprise |
| **Frontend build** | Keep single build, dynamic at runtime — avoids per-tenant rebuilds |
| **Open core** | MIT for community features; enterprise features in same repo, gated by license |
