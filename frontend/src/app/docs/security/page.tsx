import Link from 'next/link';
import { ArrowLeft, ArrowRight, Shield, Lock, Eye, Key, Server, Database, Globe, Check, Activity, FileWarning, Fingerprint, Scan, Container } from 'lucide-react';

const defenseLayers = [
  {
    icon: Container,
    title: 'Sandboxed Container Runtime',
    desc: 'Every user container runs in an isolated sandbox. Grid automatically selects the best available runtime: Kata Containers (VM-level isolation via KVM) when hardware virtualization is available, gVisor (user-space kernel) when KVM is not, or standard runc as a fallback. The runtime is detected at deploy time and injected into the container configuration.',
    details: [
      'Kata Containers: VM-level isolation with dedicated kernel per container',
      'gVisor (runsc): User-space kernel with syscall filtering, ~50MB overhead',
      'Auto-detection: priority kata > gVisor > runc',
      'Env override: SMSLY_CONTAINER_RUNTIME for manual selection',
    ],
    code: 'backend/apps/deployments/services/container_runtime.py',
  },
  {
    icon: Activity,
    title: 'Falco Runtime Threat Detection',
    desc: 'Falco monitors syscall activity across all containers using eBPF. It detects unauthorized process execution, shell spawning, sensitive file access, reverse shells, and privilege escalation attempts in real time.',
    details: [
      'Driver: modern_eBPF (no kernel headers needed, kernel >= 5.8)',
      'Image: falcosecurity/falco:0.39.2',
      'JSON output for structured alerting',
      'Capabilities: SYS_PTRACE, SYS_ADMIN, SYS_RESOURCE (minimum for eBPF)',
    ],
    code: 'infrastructure/docker/docker-compose.falco.yml',
  },
  {
    icon: Shield,
    title: 'Mandatory Access Control',
    desc: 'AppArmor enforces mandatory access control on every container with the docker-default profile. The Docker daemon has seccomp enabled by default, filtering dangerous syscalls. Combined with no-new-privileges and cap_drop ALL, containers operate in a severely restricted capability space.',
    details: [
      'AppArmor: docker-default profile on all containers',
      'seccomp: enabled in Docker daemon.json',
      'no-new-privileges: prevents privilege escalation via setuid',
      'cap_drop: ALL + selective cap_add (NET_BIND_SERVICE, CHOWN, SETUID, SETGID)',
      'pids-limit: 1024 (fork bomb protection)',
    ],
    code: 'docker-compose.prod.yml, lib/harden_apparmor.sh',
  },
  {
    icon: Lock,
    title: 'Zero Trust HMAC V2 Authentication',
    desc: 'All API requests are verified by a SecurityMiddleware that enforces HMAC V2 signatures. The signed payload includes method, path, timestamp, nonce, and body hash. Replay attacks are blocked via timestamp validation (5-minute window) and one-use nonce caching.',
    details: [
      'Signed payload: METHOD|PATH|TIMESTAMP|NONCE|BODY_HASH',
      'Timestamp validation: rejects requests older than 5 minutes',
      'Nonce replay protection: each nonce cached for 600 seconds',
      'Timing-safe comparison via hmac.compare_digest()',
      'Fail-closed: missing or invalid signatures return 403',
    ],
    code: 'backend/apps/core/middleware/security.py',
  },
  {
    icon: Key,
    title: 'Encryption at Rest',
    desc: 'All sensitive data is encrypted using Fernet symmetric encryption (AES-128-CBC). The encryption key is generated during installation and stored separately from encrypted data. Supports file-based key storage for enhanced security.',
    details: [
      'Fernet encryption (AES-128-CBC) for all sensitive fields',
      'EncryptedCharField for API keys, passwords, tokens',
      'File-based key storage: /opt/smsly-hosting/secrets/field-encryption-key',
      'Backup encryption with V2 header format (key_id + fingerprint)',
      'Auto-encryption enabled in production (DEBUG=False)',
    ],
    code: 'backend/config/settings.py, backend/apps/deployments/views/backup/',
  },
  {
    icon: Globe,
    title: 'TLS & Certificate Management',
    desc: 'Automatic Let\'s Encrypt certificates via Caddy with on-demand TLS. Domain validation prevents unauthorized certificate issuance. Wildcard subdomains supported via Cloudflare DNS-01 challenge. Inter-node communication uses TLS by default.',
    details: [
      'On-demand TLS: certificates provisioned automatically on first HTTPS visit',
      'Domain validation: /api/v1/services/check-domain/ endpoint gates issuance',
      'Wildcard support: Cloudflare DNS-01 challenge for *.your-domain.com',
      'HSTS headers: 31536000 seconds, includeSubdomains, preload',
      'Inter-node TLS: fail-closed by default (ALLOW_INSECURE_INTER_NODE_TLS=false)',
    ],
    code: 'infrastructure/docker/docker-compose.traefik.yml, backend/config/settings.py',
  },
  {
    icon: Eye,
    title: 'Immutable Audit Logging',
    desc: 'Every state change writes an immutable, hash-linked AuditLog entry. Each entry hashes the previous hash, timestamp, actor, action, target, and metadata. The chain is tamper-evident — modifying one entry breaks all subsequent hashes.',
    details: [
      'SHA-256 hash chain: previous_hash + timestamp + actor + action + target + metadata',
      'Immutability: save() raises ValidationError on existing PK',
      'Concurrency-safe: select_for_update() prevents race conditions',
      'Genesis block: first entry has previous_hash = "0" * 64',
    ],
    code: 'backend/apps/core/models/audit.py',
  },
  {
    icon: Scan,
    title: 'Vulnerability Scanning & Image Signing',
    desc: 'Trivy scans container images for CRITICAL and HIGH CVEs, including secret detection (AWS keys, private keys, JWTs, API keys). Cosign signs images after build and verifies before deployment, with fail-closed behavior when verification is required.',
    details: [
      'Trivy: scans CRITICAL+HIGH CVEs, detects leaked secrets',
      'Cosign: signs images with private key or keyless (Sigstore/Fulcio)',
      'CI pipeline: daily Trivy scans of backend and frontend images',
      'Fail-closed: deployment blocked if image verification fails',
    ],
    code: 'scripts/cosign-verify.sh, trivy.yaml, backend/apps/deployments/services/pipeline/signing.py',
  },
  {
    icon: FileWarning,
    title: 'Network Security & Firewall',
    desc: 'Multi-layer network defense: UFW firewall (deny all, allow SSH/HTTP/HTTPS/WireGuard), iptables DOCKER-USER chain restricting infrastructure ports to WireGuard mesh only, and CrowdSec behavioral threat detection as a WAF.',
    details: [
      'UFW: default deny incoming, allow outgoing',
      'Allowed: SSH (22), HTTP (80), HTTPS (443), WireGuard (51820/UDP)',
      'iptables: ports 5000, 5432, 6379, 5672 locked to localhost + Docker bridges + WireGuard mesh',
      'CrowdSec: behavioral WAF with 4-hour default ban, 24-hour for scanners',
      'WireGuard mesh: 10.100.0.0/24 subnet, all infrastructure traffic encrypted',
    ],
    code: 'lib/harden_ufw.sh, lib/fresh_hardening.sh, lib/harden_crowdsec.sh',
  },
  {
    icon: Fingerprint,
    title: 'Brute-Force Protection & Rate Limiting',
    desc: 'Three-layer rate limiting: application middleware (1000 req/min sliding window), DRF throttle classes (18 scopes), and throttled auth views. fail2ban adds IP banning for SSH, Caddy auth failures, and DDoS patterns.',
    details: [
      'Layer 1: sliding window middleware (1000 req/min anonymous)',
      'Layer 2: 18 DRF throttle classes (login 10/min, deployment 10000/min, AI 30/min)',
      'Layer 3: throttled auth views (login, logout, password reset, registration)',
      'fail2ban: sshd (3 retries/1h), caddy-auth (5 retries/1h), caddy-dos (300 req/5min)',
      'recidive jail: repeat offenders get 24-hour ban',
    ],
    code: 'backend/apps/core/middleware/ratelimit.py, lib/harden_fail2ban.sh',
  },
  {
    icon: Server,
    title: 'Kernel & System Hardening',
    desc: 'Host kernel is hardened via sysctl with ASLR, ptrace restrictions, BPF restrictions, and filesystem protections. auditd monitors critical files and syscalls. Docker daemon is configured with log rotation, live restore, and user namespace remapping.',
    details: [
      'sysctl: ASLR (randomize_va_space=2), ptrace_scope=1, kptr_restrict=2',
      'auditd: monitors /etc/shadow, /etc/passwd, /etc/sudoers, .env, secrets/, docker exec',
      'Docker daemon: json-file log driver, max-size 10m, live-restore, userns-remap',
      'Filesystem: protected_hardlinks=1, protected_symlinks=1, suid_dumpable=0',
    ],
    code: 'lib/harden_kernel.sh, lib/harden_auditd.sh, lib/harden_docker_daemon.sh',
  },
  {
    icon: Database,
    title: 'SSRF & DNS Rebinding Protection',
    desc: 'Outbound requests are validated against an SSRF guard that blocks loopback, metadata endpoints, private IPs, and reserved ranges. Serverless functions have additional DNS rebinding protection that resolves DNS and blocks responses pointing to internal IPs.',
    details: [
      'Blocks: localhost, 127.0.0.1, ::1, 169.254.169.254 (cloud metadata)',
      'Blocks: all RFC 1918 private IPs, link-local, reserved ranges',
      'DNS rebinding: resolves DNS and blocks private/reserved IP responses',
      'Function-level: safeFetch() overrides global fetch with validation',
    ],
    code: 'backend/apps/core/validators.py, backend/apps/cloud/services/function_provisioner.py',
  },
  {
    icon: Key,
    title: 'Secrets Management',
    desc: 'Infisical provides a self-hosted secrets management vault with versioning, RBAC, audit logs, and auto-reload. The secrets rotation script rotates all platform secrets with safety checks and upstream action checklists.',
    details: [
      'Infisical: self-hosted vault, binds to 127.0.0.1:8085 only',
      'Secret rotation: SECRET_KEY, FIELD_ENCRYPTION_KEY, DB/Redis passwords, API tokens',
      'Safety: writes to .env.rotated.<timestamp> (mode 0600), never overwrites original',
      'Validation: bash -n parse check before applying rotated secrets',
    ],
    code: 'infrastructure/docker/docker-compose.infisical.yml, scripts/rotate_secrets.sh',
  },
];

const authFeatures = [
  {
    icon: Key,
    title: 'Multi-Layer Authentication',
    items: [
      'API Tokens: SHA-256 hashed, smsly_ prefix, bearer auth',
      'API Keys: bcrypt hashed, sk_ prefix, expiry support',
      'Cookie-Aware Token: HttpOnly cookies with __Host- prefix in production',
      'HMAC: inter-node sync with timestamp + nonce + replay protection',
    ],
  },
  {
    icon: Shield,
    title: 'Two-Factor Authentication',
    items: [
      'TOTP-based 2FA with provisioning URI generation',
      'Backup codes: 10 single-use static codes',
      'Rate-limited 2FA login verification',
      'Password verification required to disable 2FA',
    ],
  },
  {
    icon: Fingerprint,
    title: 'Device Trust (Beta)',
    items: [
      'Browser fingerprint-based device identification',
      'SSH public key fingerprint matching',
      'Trust scoring: 0-100, incremented on success, decremented on suspicious activity',
      'Manual approval for unknown devices',
    ],
  },
];

export default function SecurityPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-emerald-100 dark:bg-emerald-900/50 rounded-xl">
              <Shield className="w-5 h-5 text-emerald-700 dark:text-emerald-300" />
            </div>
            <span className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Security</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
            Security
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
            Defense-in-depth security across 11 integrated layers. Your data never leaves your infrastructure.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">

        <p>
          Grid implements a defense-in-depth security architecture. No single layer is sufficient on its own — each layer provides independent protection, and together they create a comprehensive security posture. Since Grid is self-hosted, your data, source code, and deployments never leave your infrastructure.
        </p>

        <h2>Defense-in-Depth Layers</h2>

        <div className="not-prose space-y-6 my-8">
          {defenseLayers.map((layer, idx) => {
            const Icon = layer.icon;
            return (
              <div key={layer.title} className="rounded-xl border border-slate-200 dark:border-slate-700/50 bg-slate-50 dark:bg-slate-900/50 overflow-hidden">
                <div className="flex items-start gap-4 p-5">
                  <div className="p-2.5 rounded-lg bg-emerald-50 dark:bg-emerald-950/30 text-emerald-600 dark:text-emerald-400 flex-shrink-0">
                    <Icon size={20} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-mono text-slate-400 dark:text-slate-500">{idx + 1}</span>
                      <h3 className="text-sm font-bold text-slate-900 dark:text-white">{layer.title}</h3>
                    </div>
                    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-3">{layer.desc}</p>
                    <ul className="space-y-1">
                      {layer.details.map((d, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-slate-500 dark:text-slate-500">
                          <Check size={10} className="text-emerald-500 mt-0.5 flex-shrink-0" />
                          <span>{d}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <h2>Authentication & Access Control</h2>

        <div className="not-prose space-y-4 my-8">
          {authFeatures.map(feature => {
            const Icon = feature.icon;
            return (
              <div key={feature.title} className="p-5 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-slate-50 dark:bg-slate-900/50">
                <div className="flex items-center gap-3 mb-3">
                  <div className="p-2 rounded-lg bg-emerald-50 dark:bg-emerald-950/30 text-emerald-600 dark:text-emerald-400">
                    <Icon size={16} />
                  </div>
                  <h3 className="text-sm font-bold text-slate-900 dark:text-white">{feature.title}</h3>
                </div>
                <ul className="space-y-1.5">
                  {feature.items.map((item, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-400">
                      <Check size={12} className="text-emerald-500 mt-0.5 flex-shrink-0" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        <h2>Container Security Stack</h2>

        <p>
          Every container in Grid — infrastructure services, user deployments, and addons — runs with the same security hardening:
        </p>

        <div className="not-prose bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700/50 rounded-xl p-5 my-6">
          <pre className="text-xs text-slate-700 dark:text-slate-300 font-mono leading-relaxed">
{`# Applied to ALL containers:
security_opt:
  - no-new-privileges:true    # Block setuid privilege escalation
  - apparmor:docker-default   # Mandatory access control
cap_drop: ALL                  # Drop all Linux capabilities
cap_add:                       # Add only what's needed
  - NET_BIND_SERVICE
  - CHOWN
  - SETUID
  - SETGID
pids_limit: 1024               # Fork bomb protection

# User containers additionally get:
# - Sandboxed runtime (Kata/gVisor)
# - Resource limits (memory, CPU)
# - Read-only root filesystem where possible
# - tmpfs for /run and /tmp`}
          </pre>
        </div>

        <h2>Network Architecture</h2>

        <div className="not-prose bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700/50 rounded-xl p-5 my-6">
          <pre className="text-xs text-slate-700 dark:text-slate-300 font-mono leading-relaxed">
{`Internet → Caddy (80/443)
  ├─ TLS termination (Let's Encrypt)
  ├─ On-demand TLS domain validation
  ├─ HSTS + security headers
  ├─ CrowdSec WAF filtering
  ├─ /api/* → backend (8000)
  │    └─ Zero Trust HMAC V2 verification
  └─ /* → frontend (3000)

Internal network (Docker, encrypted WireGuard mesh):
  ├─ PostgreSQL (5432) — mesh only
  ├─ Redis (6379) — mesh only
  ├─ RabbitMQ (5672) — mesh only
  ├─ Falco — eBPF syscall monitoring
  ├─ Infisical — secrets vault (127.0.0.1:8085)
  └─ Socket Proxy — filtered Docker API access`}
          </pre>
        </div>

        <h2>Security Status API</h2>

        <p>
          Grid exposes a <code>GET /api/v1/system/security-status/</code> endpoint that reports the real-time status of every security layer. This includes container runtime detection, AppArmor status, seccomp, Falco health, CrowdSec bans, fail2ban jails, auditd status, and kernel hardening state.
        </p>

        <h2>Reporting Vulnerabilities</h2>

        <p>
          If you discover a security vulnerability in Grid, please report it responsibly. Do not open a public GitHub issue for security vulnerabilities. Instead, email <a href="mailto:security@Trulay.co">security@Trulay.co</a> with:
        </p>

        <ul>
          <li>A description of the vulnerability</li>
          <li>Steps to reproduce</li>
          <li>Potential impact assessment</li>
          <li>Any suggested fixes (optional)</li>
        </ul>

        <p>
          We aim to acknowledge reports within 48 hours and provide a resolution timeline within 5 business days.
        </p>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs/install" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> Installation Guide
          </Link>
          <Link href="/docs/changelog" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
            Changelog <ArrowRight size={14} />
          </Link>
        </div>

      </div>
    </main>
  );
}
