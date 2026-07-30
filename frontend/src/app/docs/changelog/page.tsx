import Link from 'next/link';
import { ArrowLeft, ArrowRight, History, Sparkles, Bug, Zap, Shield, Plus } from 'lucide-react';

const releases = [
  {
    version: '2.4.0',
    date: 'July 2026',
    tag: 'Latest',
    changes: [
      { type: 'feature', text: 'Multi-server remote deployment with WireGuard mesh networking' },
      { type: 'feature', text: 'Server transfer pipeline for moving workloads between nodes' },
      { type: 'feature', text: 'Autoscaling admin surface for manual replica control' },
      { type: 'feature', text: 'CrowdSec behavioral WAF with automatic IP banning' },
      { type: 'improvement', text: 'AI-enhanced autoscaling with Prometheus + Loki metrics' },
      { type: 'improvement', text: 'Deploy pipeline now supports BLUE_GREEN and CANARY strategies' },
      { type: 'improvement', text: 'Kubernetes NetworkPolicy with default-deny and intra-namespace allow' },
      { type: 'fix', text: 'Celery beat schedule now correctly registers all task modules' },
      { type: 'security', text: 'SPIFFE/SPIRE mTLS identity for inter-service authentication' },
    ],
  },
  {
    version: '2.3.0',
    date: 'June 2026',
    changes: [
      { type: 'feature', text: 'Serverless Functions runtime (Node 18, Python 3.9) with SSRF guard' },
      { type: 'feature', text: 'Runtime Intelligence — anomaly detection and self-healing' },
      { type: 'feature', text: 'DNS rebinding protection via safeFetch() in function provisioner' },
      { type: 'improvement', text: 'AI subsystem with 17 model providers and Senate Committee multi-agent' },
      { type: 'improvement', text: 'Jules auto-fix agent for failed deployments' },
      { type: 'fix', text: 'Domain .env sync now respects file permissions correctly' },
      { type: 'security', text: 'Trivy secret detection enabled (AWS keys, private keys, JWTs, API keys)' },
    ],
  },
  {
    version: '2.2.0',
    date: 'May 2026',
    changes: [
      { type: 'feature', text: 'GitHub App integration with automatic webhook management' },
      { type: 'feature', text: 'PR preview deployments for pull requests' },
      { type: 'feature', text: 'Cosign image signing with private key and keyless (Sigstore) modes' },
      { type: 'improvement', text: 'Deploy status badges on GitHub commits and PRs' },
      { type: 'improvement', text: 'Caddy on-demand TLS with domain validation endpoint' },
      { type: 'fix', text: 'CSRF_TRUSTED_ORIGINS now updates at runtime without restart' },
      { type: 'security', text: 'Zero Trust HMAC V2 middleware with timestamp + nonce replay protection' },
    ],
  },
  {
    version: '2.1.0',
    date: 'April 2026',
    changes: [
      { type: 'feature', text: 'Managed Addons — PostgreSQL, Redis, MongoDB, Qdrant' },
      { type: 'feature', text: 'One-click template deployments from the catalog' },
      { type: 'feature', text: 'Infisical secrets management vault with versioning and RBAC' },
      { type: 'improvement', text: 'Webhook idempotency via delivery_id deduplication' },
      { type: 'improvement', text: 'Backup encryption with V2 header format (key_id + fingerprint)' },
      { type: 'fix', text: 'Container restart policy now persists across updates' },
      { type: 'security', text: 'Upload security: zip bomb detection, path traversal protection, magic byte validation' },
    ],
  },
  {
    version: '2.0.0',
    date: 'March 2026',
    changes: [
      { type: 'feature', text: 'Complete UI rewrite with dark mode support' },
      { type: 'feature', text: 'Nixpacks buildpack with auto-detection for 10+ languages' },
      { type: 'feature', text: 'Dashboard-driven domain and SSL configuration' },
      { type: 'feature', text: 'Two-Factor Authentication (TOTP) with backup codes' },
      { type: 'improvement', text: 'Installation flow with IP mode and SSL mode options' },
      { type: 'improvement', text: 'Container health checks with auto-restart' },
      { type: 'security', text: 'Hash-linked immutable audit log for all state changes' },
      { type: 'security', text: 'gVisor (runsc) and Kata Containers automatic runtime selection' },
      { type: 'security', text: 'Falco eBPF runtime threat detection' },
      { type: 'security', text: '18 DRF throttle classes for API rate limiting' },
    ],
  },
  {
    version: '1.0.0',
    date: 'February 2026',
    changes: [
      { type: 'feature', text: 'Initial release of Grid hosting platform' },
      { type: 'feature', text: 'Docker-based deployment pipeline with 5 deployment types' },
      { type: 'feature', text: 'PostgreSQL, Redis, and Celery task queue' },
      { type: 'feature', text: 'Caddy reverse proxy with auto-SSL' },
      { type: 'feature', text: 'UFW firewall + fail2ban brute-force protection' },
      { type: 'security', text: 'Fernet encryption (AES-128-CBC) for secrets at rest' },
      { type: 'security', text: 'AppArmor + seccomp + no-new-privileges on all containers' },
      { type: 'security', text: 'Kernel hardening via sysctl (ASLR, ptrace, BPF restrictions)' },
    ],
  },
];

const changeIcons = {
  feature: { icon: Plus, color: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-100 dark:bg-emerald-900/40', label: 'New' },
  improvement: { icon: Sparkles, color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-100 dark:bg-blue-900/40', label: 'Improved' },
  fix: { icon: Bug, color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-100 dark:bg-amber-900/40', label: 'Fixed' },
  security: { icon: Shield, color: 'text-red-600 dark:text-red-400', bg: 'bg-red-100 dark:bg-red-900/40', label: 'Security' },
};

export default function ChangelogPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-emerald-100 dark:bg-emerald-900/50 rounded-xl">
              <History className="w-5 h-5 text-emerald-700 dark:text-emerald-300" />
            </div>
            <span className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Changelog</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
            Changelog
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
            Every release, every feature, every fix. Grid is open-source and transparent.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12">

        <div className="space-y-12">
          {releases.map((release, idx) => (
            <div key={release.version} className="relative">
              {idx < releases.length - 1 && (
                <div className="absolute left-5 top-10 bottom-0 w-px bg-slate-200 dark:bg-slate-800" />
              )}

              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-emerald-100 dark:bg-emerald-900/40 flex items-center justify-center flex-shrink-0 relative z-10">
                  <Zap size={16} className="text-emerald-600 dark:text-emerald-400" />
                </div>
                <div className="flex items-center gap-2">
                  <h2 className="text-xl font-bold text-slate-900 dark:text-white">v{release.version}</h2>
                  {release.tag && (
                    <span className="px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 text-xs font-semibold">
                      {release.tag}
                    </span>
                  )}
                  <span className="text-sm text-slate-400 dark:text-slate-500">{release.date}</span>
                </div>
              </div>

              <div className="ml-[2.75rem] space-y-2">
                {release.changes.map((change, i) => {
                  const meta = changeIcons[change.type as keyof typeof changeIcons];
                  const Icon = meta.icon;
                  return (
                    <div key={i} className="flex items-start gap-3 py-2">
                      <div className={`flex-shrink-0 w-5 h-5 rounded flex items-center justify-center mt-0.5 ${meta.bg}`}>
                        <Icon size={10} className={meta.color} />
                      </div>
                      <div className="flex-1">
                        <span className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">{change.text}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Navigation */}
        <div className="not-prose mt-16 pt-8 border-t border-slate-200 dark:border-slate-700">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> All Docs
          </Link>
        </div>

      </div>
    </main>
  );
}
