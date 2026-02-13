'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, BookOpen, Download, RefreshCw, Server, Database, Shield, Key, Terminal, Globe, Wrench, Copy, Check, ChevronRight, ExternalLink } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';

// ────────────────────────────────────────────────────────────────
// CodeBlock — inline copy button for quick terminal commands
// ────────────────────────────────────────────────────────────────
function CodeBlock({ children, lang = 'bash' }: { children: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(children.trim());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="relative group my-4">
      <pre className="bg-slate-900 dark:bg-slate-800 text-slate-100 p-4 rounded-xl overflow-x-auto text-sm leading-relaxed font-mono">
        <code>{children.trim()}</code>
      </pre>
      <button
        onClick={copy}
        className="absolute top-3 right-3 p-1.5 rounded-lg bg-slate-700/60 hover:bg-slate-600 text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity"
        aria-label="Copy to clipboard"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────
// Table of Contents navigation
// ────────────────────────────────────────────────────────────────
const tocItems = [
  { id: 'system-requirements', label: 'System Requirements', icon: Server },
  { id: 'fresh-installation', label: 'Fresh Installation', icon: Download },
  { id: 'deployment-modes', label: 'Deployment Modes', icon: Globe },
  { id: 'updating-cloudneuron', label: 'Updating CloudNeuron', icon: RefreshCw },
  { id: 'managing-services', label: 'Managing Services', icon: Terminal },
  { id: 'database-operations', label: 'Database Operations', icon: Database },
  { id: 'ssl--custom-domains', label: 'SSL & Custom Domains', icon: Key },
  { id: 'troubleshooting', label: 'Troubleshooting', icon: Wrench },
  { id: 'security-hardening', label: 'Security Hardening', icon: Shield },
];

export default function InstallGuidePage() {
  const [activeSection, setActiveSection] = useState('');

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.find(e => e.isIntersecting);
        if (visible) setActiveSection(visible.target.id);
      },
      { rootMargin: '-80px 0px -70% 0px' }
    );
    tocItems.forEach(item => {
      const el = document.getElementById(item.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <Navbar />

      {/* Hero */}
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-4xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-emerald-100 dark:bg-emerald-900/50 rounded-xl">
              <BookOpen className="w-6 h-6 text-emerald-700 dark:text-emerald-300" />
            </div>
            <span className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Installation Guide</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white mb-3">
            CloudNeuron — Installation & Operations Guide
          </h1>
          <p className="text-lg text-slate-600 dark:text-slate-400">
            Complete guide to install, update, manage, and troubleshoot your CloudNeuron instance.
          </p>
        </div>
      </section>

      <div className="max-w-7xl mx-auto flex gap-8 px-4 py-12">

        {/* ────────────── Sidebar TOC ────────────── */}
        <aside className="hidden lg:block w-56 flex-shrink-0">
          <nav className="sticky top-24 space-y-1">
            <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">On this page</p>
            {tocItems.map(item => {
              const Icon = item.icon;
              return (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all ${
                    activeSection === item.id
                      ? 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 font-semibold'
                      : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-slate-900'
                  }`}
                >
                  <Icon size={14} className="flex-shrink-0" />
                  <span className="truncate">{item.label}</span>
                </a>
              );
            })}

            <div className="pt-4 mt-4 border-t border-slate-200 dark:border-slate-800">
              <a
                href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/INSTALL_GUIDE.md"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-3 py-2 text-xs text-slate-500 hover:text-emerald-600 transition-colors"
              >
                <ExternalLink size={12} /> View on GitHub
              </a>
            </div>
          </nav>
        </aside>

        {/* ────────────── Main Content ────────────── */}
        <article className="flex-1 max-w-3xl prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">

          {/* ──── System Requirements ──── */}
          <h2 id="system-requirements" className="text-2xl font-bold flex items-center gap-2 mt-0">
            <Server className="w-5 h-5 text-emerald-600" /> System Requirements
          </h2>

          <div className="overflow-x-auto not-prose my-6">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Requirement</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Minimum</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Recommended</th>
                </tr>
              </thead>
              <tbody className="text-slate-700 dark:text-slate-300">
                {[
                  ['OS', 'Ubuntu 20.04 LTS', 'Ubuntu 22.04 / 24.04 LTS'],
                  ['CPU', '2 vCPUs', '4 vCPUs'],
                  ['RAM', '2 GB', '4 GB+'],
                  ['Disk', '20 GB', '40 GB+ SSD'],
                  ['Ports', '80, 443', '80, 443, 8090'],
                ].map(([req, min, rec]) => (
                  <tr key={req} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="py-3 px-4 font-medium">{req}</td>
                    <td className="py-3 px-4">{min}</td>
                    <td className="py-3 px-4 text-emerald-600 dark:text-emerald-400 font-medium">{rec}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p>Software dependencies (Docker, Python 3, Caddy, Git) are installed automatically.</p>


          {/* ──── Fresh Installation ──── */}
          <h2 id="fresh-installation" className="text-2xl font-bold flex items-center gap-2">
            <Download className="w-5 h-5 text-emerald-600" /> Fresh Installation
          </h2>

          <h3>One-Command Install</h3>
          <p>SSH into your server as root and run:</p>
          <CodeBlock>{`curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh`}</CodeBlock>

          <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-xl p-4 my-4 text-sm text-amber-800 dark:text-amber-200">
            <strong>Important:</strong> Do NOT pipe directly from curl. The installer requires interactive input unless you pre-seed SSL env vars.
          </div>

          <h3>What Happens During Installation</h3>
          <div className="not-prose my-6 space-y-2">
            {[
              { step: '1', title: 'Pre-flight', desc: 'Checks OS, root access, available resources' },
              { step: '2', title: 'Dependencies', desc: 'Installs Docker, Python, system packages. Stops conflicting services' },
              { step: '3', title: 'Configuration', desc: 'Generates secrets: Django SECRET_KEY, Fernet key, DB/Redis passwords, HMAC gateway secret' },
              { step: '4', title: 'Deployment', desc: 'Builds and starts all Docker containers' },
              { step: '5', title: 'Database', desc: 'Waits for PostgreSQL, syncs passwords, runs Django migrations' },
              { step: '6', title: 'Admin User', desc: 'Creates admin superuser (credentials saved to /opt/smsly-hosting/.credentials)' },
              { step: '7', title: 'Reverse Proxy', desc: 'Installs Caddy for HTTP or HTTPS with auto-SSL' },
              { step: '8', title: 'Verification', desc: 'Runs health checks, prints container status' },
            ].map(item => (
              <div key={item.step} className="flex items-start gap-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800">
                <span className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 text-xs font-bold">{item.step}</span>
                <div>
                  <p className="font-semibold text-slate-900 dark:text-white text-sm">{item.title}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>

          <h3>After Installation</h3>
          <ol>
            <li>Open the URL shown in the terminal</li>
            <li>Log in with <code>admin</code> and the password in <code>/opt/smsly-hosting/.credentials</code></li>
            <li>(Recommended) Change the admin password (Settings → Security)</li>
            <li>Configure your cloud providers (Settings → Cloud)</li>
          </ol>


          {/* ──── Deployment Modes ──── */}
          <h2 id="deployment-modes" className="text-2xl font-bold flex items-center gap-2">
            <Globe className="w-5 h-5 text-emerald-600" /> Deployment Modes
          </h2>

          <div className="not-prose grid md:grid-cols-2 gap-4 my-6">
            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">IP Mode (Quick Start)</h4>
              <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-1">
                <li>• Access: <code className="text-xs bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 rounded">http://YOUR_IP</code></li>
                <li>• No domain needed</li>
                <li>• Best for testing</li>
                <li>• Select option <strong>1</strong> during install</li>
              </ul>
            </div>
            <div className="p-5 rounded-xl border border-emerald-200 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-950/30">
              <h4 className="font-bold text-emerald-800 dark:text-emerald-300 mb-2 text-sm">SSL Mode (Production) ✦</h4>
              <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-1">
                <li>• Access: <code className="text-xs bg-emerald-100 dark:bg-emerald-900/50 px-1.5 py-0.5 rounded">https://your-domain.com</code></li>
                <li>• Auto Let&apos;s Encrypt SSL</li>
                <li>• Requires DNS A record</li>
                <li>• Select option <strong>2</strong> during install</li>
              </ul>
            </div>
          </div>


          {/* ──── Updating CloudNeuron ──── */}
          <h2 id="updating-cloudneuron" className="text-2xl font-bold flex items-center gap-2">
            <RefreshCw className="w-5 h-5 text-emerald-600" /> Updating CloudNeuron
          </h2>

          <h3>From the Terminal</h3>

          <div className="not-prose space-y-4 my-6">
            <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700">
              <p className="text-sm font-bold text-slate-900 dark:text-white mb-2">Full Update (Frontend + Backend)</p>
              <CodeBlock>{`cd /opt/smsly-hosting
sudo bash install.sh --update`}</CodeBlock>
            </div>
            <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700">
              <p className="text-sm font-bold text-slate-900 dark:text-white mb-2">Frontend Only (1-2 min, no backend downtime)</p>
              <CodeBlock>{`cd /opt/smsly-hosting
sudo bash install.sh --update-frontend`}</CodeBlock>
            </div>
            <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700">
              <p className="text-sm font-bold text-slate-900 dark:text-white mb-2">Backend Only (includes migrations)</p>
              <CodeBlock>{`cd /opt/smsly-hosting
sudo bash install.sh --update-backend`}</CodeBlock>
            </div>
          </div>

          <h3 id="rollback-on-failure">Rollback on Failure</h3>
          <p>If an update fails, the installer automatically:</p>
          <ol>
            <li>Stops new containers</li>
            <li>Restores the previous <code>.env</code> backup</li>
            <li>Pops the git stash (rolls back code)</li>
          </ol>

          <p>Manual rollback:</p>
          <CodeBlock>{`cd /opt/smsly-hosting
git log --oneline -n 5           # Find the previous commit
git checkout <commit-hash>       # Roll back
docker compose -f docker-compose.prod.yml up -d --build`}</CodeBlock>

          <h3>From the Dashboard</h3>
          <p>Admins can trigger updates from <strong>Settings → System → Update Software</strong>.</p>


          {/* ──── Managing Services ──── */}
          <h2 id="managing-services" className="text-2xl font-bold flex items-center gap-2">
            <Terminal className="w-5 h-5 text-emerald-600" /> Managing Services
          </h2>

          <h3>View Container Status</h3>
          <CodeBlock>{`cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml ps`}</CodeBlock>

          <h3>View Logs</h3>
          <CodeBlock>{`# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend`}</CodeBlock>

          <h3>Restart Services</h3>
          <CodeBlock>{`# Restart everything
docker compose -f docker-compose.prod.yml restart

# Restart specific service
docker compose -f docker-compose.prod.yml restart backend`}</CodeBlock>

          <h3 id="health-check">Health Check</h3>
          <CodeBlock>{`curl http://localhost:8090/health`}</CodeBlock>

          <h3 id="container-map">Container Map</h3>
          <div className="overflow-x-auto not-prose my-6">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Service</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Port</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Purpose</th>
                </tr>
              </thead>
              <tbody className="text-slate-700 dark:text-slate-300">
                {[
                  ['backend', '8000', 'Django API (Gunicorn)'],
                  ['frontend', '3000', 'Next.js SSR'],
                  ['nginx', '8090', 'Internal routing'],
                  ['db', '5432', 'PostgreSQL 16'],
                  ['redis', '6379', 'Cache + Celery broker'],
                  ['celery', '—', 'Background task worker'],
                  ['celery-beat', '—', 'Periodic task scheduler'],
                ].map(([svc, port, desc]) => (
                  <tr key={svc} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="py-2.5 px-4 font-mono text-emerald-600 dark:text-emerald-400 text-xs">{svc}</td>
                    <td className="py-2.5 px-4">{port}</td>
                    <td className="py-2.5 px-4">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>


          {/* ──── Database Operations ──── */}
          <h2 id="database-operations" className="text-2xl font-bold flex items-center gap-2">
            <Database className="w-5 h-5 text-emerald-600" /> Database Operations
          </h2>

          <h3>Backup</h3>
          <CodeBlock>{`cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml exec db \\
  pg_dump -U smsly_admin smsly_hosting | gzip > backup_$(date +%Y%m%d).sql.gz`}</CodeBlock>

          <h3>Automated Backups</h3>
          <p>Add to root crontab (<code>crontab -e</code>):</p>
          <CodeBlock>{`# Daily at 2 AM
0 2 * * * cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting | gzip > backups/daily_$(date +\\%Y\\%m\\%d).sql.gz`}</CodeBlock>

          <h3 id="restore-from-backup">Restore from Backup</h3>
          <CodeBlock>{`# Stop write-services
docker compose -f docker-compose.prod.yml stop backend celery celery-beat

# Restore
gunzip -c backup_20260211.sql.gz | \\
  docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting

# Restart
docker compose -f docker-compose.prod.yml start backend celery celery-beat`}</CodeBlock>

          <h3 id="reset-admin-password">Reset Admin Password</h3>
          <CodeBlock>{`docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c \\
  "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.get(username='admin'); u.set_password('your_new_password'); u.save(); print('Done.')"`}</CodeBlock>


          {/* ──── SSL & Custom Domains ──── */}
          <h2 id="ssl--custom-domains" className="text-2xl font-bold flex items-center gap-2">
            <Key className="w-5 h-5 text-emerald-600" /> SSL & Custom Domains
          </h2>

          <h3>Switch from IP Mode to SSL</h3>
          <ol>
            <li>Create a DNS A record pointing to your server IP</li>
            <li>Edit Caddyfile: <code>nano /etc/caddy/Caddyfile</code></li>
          </ol>
          <CodeBlock>{`your-domain.com {
    reverse_proxy localhost:8090
    encode gzip
}`}</CodeBlock>
          <ol start={3}>
            <li>Update <code>.env</code> — set <code>DOMAIN</code>, <code>USE_SSL=true</code>, <code>ALLOWED_HOSTS</code>, <code>CSRF_TRUSTED_ORIGINS</code></li>
            <li>Restart: <code>systemctl restart caddy && docker compose -f docker-compose.prod.yml restart backend</code></li>
          </ol>


          {/* ──── Troubleshooting ──── */}
          <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
            <Wrench className="w-5 h-5 text-emerald-600" /> Troubleshooting
          </h2>

          <div className="not-prose space-y-3 my-6">
            {[
              { q: 'Dashboard Not Loading', a: 'Check containers (docker compose ps), verify nginx on port 8090, check firewall (ufw status), check Caddy.' },
              { q: 'Database Connection Error', a: 'Check backend logs, verify .env POSTGRES_PASSWORD matches DATABASE_URL. Re-sync with --update.' },
              { q: 'Build Fails During Update', a: 'Check disk space (df -h), clean Docker cache (docker system prune -f), retry update.' },
              { q: 'Caddy SSL Error', a: 'Verify DNS resolves (host your-domain.com), check Caddy logs (journalctl -u caddy), ensure ports 80/443 are open.' },
              { q: 'Container Keeps Restarting', a: 'Check which container (docker compose ps), view its logs (docker compose logs --tail=100 <service>).' },
            ].map(item => (
              <details key={item.q} className="group rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 overflow-hidden">
                <summary className="flex items-center justify-between p-4 cursor-pointer text-sm font-semibold text-slate-900 dark:text-white">
                  {item.q}
                  <ChevronRight size={16} className="text-slate-400 group-open:rotate-90 transition-transform" />
                </summary>
                <div className="px-4 pb-4 text-sm text-slate-600 dark:text-slate-400">{item.a}</div>
              </details>
            ))}
          </div>


          {/* ──── Security Hardening ──── */}
          <h2 id="security-hardening" className="text-2xl font-bold flex items-center gap-2">
            <Shield className="w-5 h-5 text-emerald-600" /> Security Hardening
          </h2>

          <h3>Post-Install Checklist</h3>
          <div className="not-prose my-4 space-y-2">
            {[
              'Change the admin password (recommended)',
              'Verify DEBUG=False in .env',
              'Configure ALLOWED_HOSTS to only your domain',
              'Enable SSL mode for production',
              'Set up UFW firewall (ports 80, 443 only)',
            ].map((item, i) => (
              <div key={i} className="flex items-center gap-3 text-sm text-slate-700 dark:text-slate-300">
                <div className="w-5 h-5 rounded-md border-2 border-slate-300 dark:border-slate-600 flex-shrink-0" />
                {item}
              </div>
            ))}
          </div>

          <h3>Firewall Setup (UFW)</h3>
          <CodeBlock>{`ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable`}</CodeBlock>

          <h3 id="credential-locations">Credential Locations</h3>
          <div className="overflow-x-auto not-prose my-6">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">File</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Purpose</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Permissions</th>
                </tr>
              </thead>
              <tbody className="text-slate-700 dark:text-slate-300">
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">/opt/smsly-hosting/.env</td>
                  <td className="py-2.5 px-4">All secrets & config</td>
                  <td className="py-2.5 px-4 font-mono text-xs">chmod 600</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">/opt/smsly-hosting/.credentials</td>
                  <td className="py-2.5 px-4">Admin login info</td>
                  <td className="py-2.5 px-4 font-mono text-xs">chmod 600</td>
                </tr>
              </tbody>
            </table>
          </div>

        </article>
      </div>
    </main>
  );
}
