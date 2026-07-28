'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, BookOpen, Download, RefreshCw, Server, Database, Shield, Key, Terminal, Globe, Wrench, Copy, Check, ChevronRight, ExternalLink } from 'lucide-react';


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
      <pre className="bg-slate-900 dark:bg-slate-900/80 text-slate-100 p-4 pr-12 rounded-xl overflow-x-auto text-sm leading-relaxed font-mono border border-slate-800 dark:border-slate-700/50">
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
// TOC navigation
// ────────────────────────────────────────────────────────────────
const tocItems = [
  { id: 'system-requirements', label: 'System Requirements', icon: Server },
  { id: 'fresh-installation', label: 'Fresh Installation', icon: Download },
  { id: 'deployment-modes', label: 'Deployment Modes', icon: Globe },
  { id: 'accessing-the-dashboard', label: 'Accessing the Dashboard', icon: Terminal },
  { id: 'domain--ssl-setup', label: 'Domain & SSL Setup', icon: Key },
  { id: 'common-edge-cases', label: 'Common Edge Cases', icon: Wrench },
  { id: 'updating-grid', label: 'Updating Grid', icon: RefreshCw },
  { id: 'managing-services', label: 'Managing Services', icon: Terminal },
  { id: 'database-operations', label: 'Database Operations', icon: Database },
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


      {/* Hero */}
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-emerald-100 dark:bg-emerald-900/50 rounded-xl">
              <BookOpen className="w-5 h-5 text-emerald-700 dark:text-emerald-300" />
            </div>
            <span className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Installation Guide</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
            Installation & Operations Guide
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
            Complete guide to install, update, manage, and troubleshoot your Grid instance.
          </p>
        </div>
      </section>

      <div className="max-w-7xl mx-auto flex gap-8 px-4 py-12">

        {/* ────────────── Sidebar TOC ────────────── */}
        <aside className="hidden lg:block w-56 flex-shrink-0">
          <nav className="sticky top-24 space-y-0.5">
            <p className="text-xs font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-3 px-3">On this page</p>
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
                  ['Ports', '80, 443', '80, 443'],
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

          <p>Software dependencies (Docker, Python 3, Caddy, Git) are installed automatically by the installer.</p>


          {/* ──── Fresh Installation ──── */}
          <h2 id="fresh-installation" className="text-2xl font-bold flex items-center gap-2">
            <Download className="w-5 h-5 text-emerald-600" /> Fresh Installation
          </h2>

          <h3>One-Command Install</h3>
          <p>SSH into your server as root and run:</p>
          <CodeBlock>{`curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh`}</CodeBlock>

          <div className="docs-callout docs-callout-warning not-prose">
            <p className="!mt-0">
              <strong>Important:</strong> Do NOT pipe directly from curl. The installer requires interactive input unless you pre-seed SSL env vars.
            </p>
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
              { step: '8', title: 'Verification', desc: 'Runs health checks, prints container status, shows access URL' },
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

          <h3>Installation Output</h3>
          <p>When the installer finishes, it prints a summary like this:</p>
          <CodeBlock>{`═══════════════════════════════════════════
  Grid Installation Complete!
═══════════════════════════════════════════
  Access URL: http://203.0.113.42
  Admin: admin
  Password: /opt/smsly-hosting/.credentials
═══════════════════════════════════════════`}</CodeBlock>

          <p>The <strong>Access URL</strong> is always <code>http://YOUR_SERVER_IP</code> for a fresh install — never HTTPS. See &quot;Accessing the Dashboard&quot; below for why.</p>


          {/* ──── Deployment Modes ──── */}
          <h2 id="deployment-modes" className="text-2xl font-bold flex items-center gap-2">
            <Globe className="w-5 h-5 text-emerald-600" /> Deployment Modes
          </h2>

          <p>
            The installer offers two modes. Choose during the interactive prompts. You can switch from IP mode to SSL mode at any time through the Settings UI.
          </p>

          <div className="not-prose grid md:grid-cols-2 gap-4 my-6">
            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">IP Mode (Quick Start)</h4>
              <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-1">
                <li>• Access via <code className="text-xs bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 rounded">http://&lt;IP&gt;</code></li>
                <li>• No domain required</li>
                <li>• No SSL certificate</li>
                <li>• Best for testing / evaluation</li>
                <li>• Select option <strong>1</strong> during install</li>
              </ul>
            </div>
            <div className="p-5 rounded-xl border border-emerald-200 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-950/30">
              <h4 className="font-bold text-emerald-800 dark:text-emerald-300 mb-2 text-sm">SSL Mode (Production) ✦</h4>
              <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-1">
                <li>• Access via <code className="text-xs bg-emerald-100 dark:bg-emerald-900/50 px-1.5 py-0.5 rounded">https://your-domain.com</code></li>
                <li>• Auto Let&apos;s Encrypt SSL via Caddy</li>
                <li>• Requires DNS A record pointing to your server</li>
                <li>• Ports 80 + 443 must be publicly reachable</li>
                <li>• Select option <strong>2</strong> during install</li>
              </ul>
            </div>
          </div>

          <div className="docs-callout docs-callout-info not-prose">
            <p className="!mt-0">
              <strong>Note about HTTPS in IP mode:</strong> When no domain is configured, the server only binds port 80 (HTTP). Port 443 has a self-signed certificate that redirects to HTTP. Browsers will show a &quot;Your connection is not private&quot; warning if you manually type <code>https://&lt;IP&gt;</code>. Click &quot;Advanced&quot; &rarr; &quot;Proceed&quot; to be redirected to HTTP. <strong>Always use plain HTTP in IP mode</strong> to avoid this warning entirely.
            </p>
          </div>


          {/* ──── Accessing the Dashboard ──── */}
          <h2 id="accessing-the-dashboard" className="text-2xl font-bold flex items-center gap-2">
            <Terminal className="w-5 h-5 text-emerald-600" /> Accessing the Dashboard
          </h2>

          <h3>IP Mode (No Domain)</h3>
          <ol>
            <li>Open your browser and go to <code>http://YOUR_SERVER_IP</code> (e.g. <code>http://203.0.113.42</code>)</li>
            <li>Do <strong>not</strong> use <code>https://</code> — there is no valid certificate in IP mode</li>
            <li>If you accidentally visit <code>https://</code>, you will see a &quot;Your connection is not private&quot; warning. Click <strong>Advanced</strong> → <strong>Proceed to site</strong>. This redirects you to HTTP automatically</li>
            <li>Log in with username <code>admin</code> and the password stored in <code>/opt/smsly-hosting/.credentials</code></li>
          </ol>

          <h3>SSL Mode (With Domain)</h3>
          <ol>
            <li>Ensure your domain has an A record pointing to your server IP</li>
            <li>Open your browser and go to <code>https://your-domain.com</code></li>
            <li>Caddy automatically provisions a Let&apos;s Encrypt certificate on the first visit (may take 5-10 seconds the first time)</li>
            <li>Subsequent visits are instant with a valid, browser-trusted certificate</li>
          </ol>

          <h3>Why HTTP in IP Mode?</h3>
          <p>
            Let&apos;s Encrypt cannot issue certificates for raw IP addresses. The self-signed fallback certificate exists on port 443 only to redirect HTTPS traffic back to HTTP. Browsers warn on self-signed certificates before following the redirect. For a zero-warning experience, always access via <strong>HTTP</strong> when no domain is configured.
          </p>

          <div className="docs-callout docs-callout-success not-prose">
            <p className="!mt-0">
              <strong>Already set up a domain?</strong> Go to <strong>Settings &rarr; Domain &amp; SSL</strong> in the dashboard, enter your domain, toggle SSL on, and save. Caddy will automatically provision a Let&apos;s Encrypt certificate. No SSH required.
            </p>
          </div>


          {/* ──── Domain & SSL Setup ──── */}
          <h2 id="domain--ssl-setup" className="text-2xl font-bold flex items-center gap-2">
            <Key className="w-5 h-5 text-emerald-600" /> Domain & SSL Setup
          </h2>

          <div className="docs-callout docs-callout-warning not-prose">
            <p className="!mt-0">
              <strong>Important:</strong> You do <em>not</em> need to SSH into the server to set up a domain. Everything is configurable from the dashboard under <strong>Settings &rarr; Domain &amp; SSL</strong>.
            </p>
          </div>

          <h3>How It Works</h3>
          <p>
            The system uses <strong>Caddy</strong> as its reverse proxy and TLS terminator. Caddy automatically provisions and renews Let&apos;s Encrypt certificates. The backend generates the Caddyfile dynamically based on your configuration in the database (<code>PlatformConfig</code> model) and applies it without downtime.
          </p>

          <h3>Step 1: DNS Setup</h3>
          <p>Before configuring SSL, your domain must resolve to your server:</p>
          <div className="overflow-x-auto not-prose my-6">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Record Type</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Name</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Value</th>
                </tr>
              </thead>
              <tbody className="text-slate-700 dark:text-slate-300">
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">A</td>
                  <td className="py-2.5 px-4 font-mono text-xs">@</td>
                  <td className="py-2.5 px-4 font-mono text-xs">YOUR_SERVER_IP</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>Verify propagation:</p>
          <CodeBlock>{`dig +short your-domain.com
# Should return your server IP`}</CodeBlock>

          <div className="docs-callout docs-callout-danger not-prose">
            <p className="!mt-0">
              <strong>SSL will fail if DNS is not propagated.</strong> Caddy&apos;s ACME challenge (Let&apos;s Encrypt) must be able to reach your server on port 80. If DNS is wrong, the certificate cannot be issued.
            </p>
          </div>

          <h3>Step 2: Configure via Dashboard</h3>
          <ol>
            <li>
              <strong>Navigate to Settings → Domain &amp; SSL</strong>
            </li>
            <li>Enter your domain (e.g. <code>grid.your-domain.com</code>)</li>
            <li>Toggle <strong>SSL Enabled</strong> ON</li>
            <li>Enter the server public IP (usually pre-filled)</li>
            <li>Click <strong>Save &amp; Apply</strong></li>
          </ol>

          <p>After saving, the backend:</p>
          <ol>
            <li>Updates the <code>PlatformConfig</code> model in the database</li>
            <li>Regenerates the Caddyfile with your domain</li>
            <li>Reloads Caddy with zero downtime</li>
            <li>Syncs the domain back to the <code>.env</code> file (for future updates)</li>
            <li>Updates <code>ALLOWED_HOSTS</code>, <code>CSRF_TRUSTED_ORIGINS</code>, <code>CORS_ALLOWED_ORIGINS</code>, and <code>SITE_URL</code> at runtime</li>
          </ol>

          <h3>Step 3: Access via HTTPS</h3>
          <p>
            Visit <code>https://your-domain.com</code>. The first visit may take 5-10 seconds as Caddy obtains the Let&apos;s Encrypt certificate on-demand. Subsequent visits are instant.
          </p>

          <div className="docs-callout docs-callout-success not-prose">
            <p className="!mt-0">
              <strong>Tip:</strong> The first visitor triggers certificate issuance. If the certificate doesn&apos;t appear after 30 seconds, check Caddy logs via <code>docker logs smsly-hosting-caddy-1</code> on the server.
            </p>
          </div>

          <h3>What the Backend Updates Automatically</h3>
          <p>
            When you save domain config via the dashboard, the backend runtime patches all the following Django settings (no restart needed):
          </p>
          <div className="overflow-x-auto not-prose my-6">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Setting</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Source</th>
                  <th className="text-left py-3 px-4 text-slate-500 font-semibold">Updated</th>
                </tr>
              </thead>
              <tbody className="text-slate-700 dark:text-slate-300">
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">ALLOWED_HOSTS</td>
                  <td className="py-2.5 px-4"><code>patching.py</code></td>
                  <td className="py-2.5 px-4 text-emerald-600">Runtime + DB</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">CSRF_TRUSTED_ORIGINS</td>
                  <td className="py-2.5 px-4"><code>patching.py</code></td>
                  <td className="py-2.5 px-4 text-emerald-600">Runtime</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">CORS_ALLOWED_ORIGINS</td>
                  <td className="py-2.5 px-4"><code>patching.py</code></td>
                  <td className="py-2.5 px-4 text-emerald-600">Runtime</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">SITE_URL</td>
                  <td className="py-2.5 px-4"><code>patching.py</code></td>
                  <td className="py-2.5 px-4 text-emerald-600">Runtime</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">DOMAIN= (in .env)</td>
                  <td className="py-2.5 px-4"><code>signals.py</code></td>
                  <td className="py-2.5 px-4 text-emerald-600">File (if writable)</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">Caddyfile</td>
                  <td className="py-2.5 px-4"><code>caddy_manager.py</code></td>
                  <td className="py-2.5 px-4 text-emerald-600">File + Reload</td>
                </tr>
              </tbody>
            </table>
          </div>

          <h3>How .env Gets Updated</h3>
          <p>
            When the <code>PlatformConfig</code> model is saved (via the UI or API), a Django <code>post_save</code> signal fires (<code>signals.py</code>). This signal attempts to write the new <code>DOMAIN</code>, <code>USE_SSL</code>, <code>SITE_URL</code>, <code>CSRF_TRUSTED_ORIGINS</code>, and <code>CORS_ALLOWED_ORIGINS</code> values back to the <code>.env</code> file on the host.
          </p>
          <p>
            The backend container runs as user <code>smsly</code> (UID 1000). The installer sets <code>.env</code> permissions to <code>664</code> with group ownership by GID 1000, so the container can write to it. If the permissions are wrong (e.g. <code>600</code> or <code>644</code> owned by root), the signal logs a warning and skips the file update — the domain still works because the database is the source of truth, but a future <code>--update</code> run would not pick up the new domain.
          </p>
          <p>To fix this manually if it occurs:</p>
          <CodeBlock>{`sudo chown root:1000 /opt/smsly-hosting/.env
sudo chmod 664 /opt/smsly-hosting/.env`}</CodeBlock>

          <h3>Switching from IP Mode to SSL Mode</h3>
          <ol>
            <li>Create an A record for your domain → your server IP</li>
            <li>Wait for DNS propagation (verify with <code>dig +short your-domain.com</code>)</li>
            <li>Go to <strong>Settings → Domain &amp; SSL</strong></li>
            <li>Enter your domain, toggle SSL ON, click <strong>Save &amp; Apply</strong></li>
            <li>Access via <code>https://your-domain.com</code></li>
          </ol>

          <h3>Removing a Domain (Revert to IP Mode)</h3>
          <ol>
            <li>Go to <strong>Settings → Domain &amp; SSL</strong></li>
            <li>Clear the domain field, toggle SSL OFF</li>
            <li>Click <strong>Save &amp; Apply</strong></li>
            <li>Access via <code>http://&lt;IP&gt;</code></li>
          </ol>

          <h3>Wildcard Subdomains</h3>
          <p>
            If you enable <strong>Wildcard Subdomains</strong>, Caddy provisions a <code>*.your-domain.com</code> certificate via Cloudflare&apos;s DNS-01 challenge. This requires:
          </p>
          <ol>
            <li>Setting <strong>Cloudflare API Token</strong> (DNS: Edit zone DNS permission) in Settings → Domain &amp; SSL</li>
            <li>Your domain must be on Cloudflare DNS</li>
            <li>The wildcard cert covers all <code>*.your-domain.com</code> subdomains automatically</li>
          </ol>


          {/* ──── Common Edge Cases ──── */}
          <h2 id="common-edge-cases" className="text-2xl font-bold flex items-center gap-2">
            <Wrench className="w-5 h-5 text-emerald-600" /> Common Edge Cases
          </h2>

          <div className="not-prose space-y-4 my-6">

            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">&quot;Your connection is not private&quot; / <code>ERR_CERT_AUTHORITY_INVALID</code></h4>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                You accessed <code>https://&lt;IP&gt;</code> in IP mode (no domain). The server has a self-signed certificate on port 443 that redirects to HTTP, but browsers warn before following the redirect.
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                <strong>Fix:</strong> Use <code>http://&lt;IP&gt;</code> instead. If you already set up a domain, use <code>https://your-domain.com</code>. If you clicked through the warning, you are automatically redirected to HTTP.
              </p>
            </div>

            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">SSL Certificate Not Issued (HTTPS shows self-signed after domain config)</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                This usually means Caddy&apos;s on-demand TLS &quot;ask&quot; endpoint rejected the domain, or DNS hasn&apos;t propagated.
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">Diagnose from the server:</p>
              <CodeBlock>{`# 1. Check the ask endpoint (must return 200)
curl -s -w "\nHTTP %{http_code}\n" \\
  "http://backend:8000/api/v1/services/check-domain/?domain=your-domain.com"

# 2. Check Caddy logs for ACME errors
docker logs smsly-hosting-caddy-1 --tail 50

# 3. Force a Caddy reload to retry cert issuance
docker exec smsly-hosting-caddy-1 caddy reload --config /etc/caddy/Caddyfile`}</CodeBlock>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                If the ask endpoint returns <strong>200</strong>, Caddy should get the cert on the next HTTPS visit. If <strong>404</strong>, the domain is not in the <code>PlatformConfig</code> database — re-save it via Settings → Domain &amp; SSL.
              </p>
            </div>

            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">Gateway Timeout After Reboot</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                On first boot after a restart, the backend runs database migrations and waits for PostgreSQL to be healthy. This can take 1-3 minutes.
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">Check progress:</p>
              <CodeBlock>{`docker compose -f docker-compose.prod.yml logs backend --tail 30`}</CodeBlock>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                You will see messages like <code>Waiting for database...</code> followed by <code>Starting gunicorn</code>. The dashboard becomes available once the backend is healthy.
              </p>
            </div>

            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">Frontend API Calls to HTTP Instead of HTTPS</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                The frontend uses relative API paths (<code>/api/v1/...</code>), so the scheme (HTTP vs HTTPS) matches whatever the browser is using. If you access via <code>http://&lt;IP&gt;</code>, API calls use HTTP. If via <code>https://domain</code>, they use HTTPS.
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                If you see mixed-content errors or API calls going to the wrong scheme, ensure you&apos;re accessing via the correct URL for your mode.
              </p>
            </div>

            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">Domain Saved via UI but .env Not Updated</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                The backend container (user <code>smsly</code>, UID 1000) needs write permission on the host <code>.env</code> file. If the file is owned by root with <code>644</code>, the write fails with <code>PermissionError</code>.
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">Check and fix:</p>
              <CodeBlock>{`ls -la /opt/smsly-hosting/.env
# If owned by root:root, fix with:
sudo chown root:1000 /opt/smsly-hosting/.env
sudo chmod 664 /opt/smsly-hosting/.env`}</CodeBlock>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                The domain still works because it&apos;s stored in the database. The <code>.env</code> is only needed for future <code>--update</code> runs and container restarts.
              </p>
            </div>

            <div className="p-5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
              <h4 className="font-bold text-slate-900 dark:text-white mb-2 text-sm">&quot;403 Forbidden&quot; or &quot;CSRF token missing&quot; After Domain Change</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
                The <code>patch_runtime_settings()</code> function should update <code>CSRF_TRUSTED_ORIGINS</code> and <code>CORS_ALLOWED_ORIGINS</code> automatically when you save domain config. If these didn&apos;t update, re-save the domain config via Settings → Domain &amp; SSL.
              </p>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                If the issue persists, the backend may need a restart: <code>docker compose -f docker-compose.prod.yml restart backend</code>
              </p>
            </div>

          </div>


          {/* ──── Updating Grid ──── */}
          <h2 id="updating-grid" className="text-2xl font-bold flex items-center gap-2">
            <RefreshCw className="w-5 h-5 text-emerald-600" /> Updating Grid
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
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f caddy`}</CodeBlock>

          <h3>Restart Services</h3>
          <CodeBlock>{`# Restart everything
docker compose -f docker-compose.prod.yml restart

# Restart specific service
docker compose -f docker-compose.prod.yml restart backend
docker compose -f docker-compose.prod.yml restart caddy`}</CodeBlock>

          <h3 id="health-check">Health Check</h3>
          <CodeBlock>{`curl http://localhost/health`}</CodeBlock>

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
                  ['caddy', '80 / 443', 'Reverse proxy, TLS termination, Let\'s Encrypt'],
                  ['backend', '8000', 'Django API (Gunicorn + Uvicorn)'],
                  ['frontend', '3000', 'Next.js SSR'],
                  ['db', '5432', 'PostgreSQL 16'],
                  ['redis', '6379', 'Cache + Celery broker'],
                  ['celery', '—', 'Background task worker'],
                  ['celery-beat', '—', 'Periodic task scheduler'],
                  ['rabbitmq', '5672', 'Message broker for Celery'],
                  ['socket-proxy', '2375', 'Secured Docker API proxy'],
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
          <p>
            <strong>Architecture note:</strong> All external traffic enters through <strong>Caddy</strong> (ports 80/443). Caddy terminates TLS and proxies directly to the <strong>backend</strong> (port 8000) or <strong>frontend</strong> (port 3000) based on the request path. The stack does <em>not</em> expose backend/frontend ports directly to the internet.
          </p>


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


          {/* ──── Troubleshooting ──── */}
          <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
            <Wrench className="w-5 h-5 text-emerald-600" /> Troubleshooting
          </h2>

          <div className="not-prose space-y-3 my-6">
            {[
              {
                q: 'Dashboard Not Loading (blank page or 502)',
                a: 'Check that all containers are running: docker compose -f docker-compose.prod.yml ps. Wait for backend migrations to finish (may take 1-3 min after reboot). Check caddy health: curl http://localhost/health. Verify firewall allows ports 80/443: ufw status.'
              },
              {
                q: 'ERR_CERT_AUTHORITY_INVALID',
                a: 'You are likely accessing https://SERVER_IP in IP mode. Use http://SERVER_IP instead. If you have configured a domain, ensure DNS resolves correctly (dig +short your-domain.com) and the domain is saved in Settings → Domain & SSL.'
              },
              {
                q: 'Gateway Timeout (504)',
                a: 'The backend is still starting up. Run docker compose -f docker-compose.prod.yml logs backend --tail 30 to check progress. Common causes: database migrations, waiting for PostgreSQL health check, or slow build on first boot. Wait 2-3 minutes.'
              },
              {
                q: 'Caddy SSL Error — Certificate Not Issued',
                a: 'Verify DNS resolves (host your-domain.com), check Caddy logs (docker logs smsly-hosting-caddy-1), ensure ports 80/443 are open from outside (curl -v http://your-domain.com/.well-known/acme-challenge/check should not timeout). The ask endpoint must return 200 for your domain: curl -s "http://backend:8000/api/v1/services/check-domain/?domain=your-domain.com".'
              },
              {
                q: 'Database Connection Error',
                a: 'Check backend logs (docker compose -f docker-compose.prod.yml logs backend --tail 20). Verify POSTGRES_PASSWORD in .env matches what was set during install. Re-sync with: sudo bash install.sh --update.'
              },
              {
                q: 'Build Fails During Update',
                a: 'Check disk space (df -h), clean Docker cache (docker system prune -f), re-run the update. If the issue persists, the error may be in the build logs at /var/log/smsly-install.log.'
              },
              {
                q: 'Container Keeps Restarting (CrashLoop)',
                a: 'Run docker compose -f docker-compose.prod.yml ps to identify the unhealthy container, then check its logs: docker compose -f docker-compose.prod.yml logs --tail=50 <service>. Common causes: database not reachable, port conflicts, or missing environment variables.'
              },
              {
                q: '403 Forbidden or CSRF Validation Failed',
                a: 'The backend\'s CSRF_TRUSTED_ORIGINS may not include your current origin. Re-save the domain config via Settings → Domain & SSL to trigger the runtime patch. If that fails, restart the backend: docker compose -f docker-compose.prod.yml restart backend.'
              },
              {
                q: 'After Reboot, Everything Is Down',
                a: 'Containers with restart: unless-stopped will auto-start after a Docker daemon restart. Give it 2-3 minutes for the stack to fully initialize. Run docker compose -f docker-compose.prod.yml ps to check status. If containers are not running, start manually: docker compose -f docker-compose.prod.yml up -d.'
              },
              {
                q: 'How Do I Force Caddy to Renew / Retry a Certificate?',
                a: 'Run: docker exec smsly-hosting-caddy-1 caddy reload --config /etc/caddy/Caddyfile. This reloads the config and triggers ACME retries for any domains without valid certificates.'
              },
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
              'Set up UFW firewall (ports 80, 443, SSH only)',
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

          <p>
            Port <strong>5432</strong> (PostgreSQL), <strong>6379</strong> (Redis), and other internal ports are only accessible within the Docker network and should not be exposed to the internet.
          </p>

          <h3>.env File Permissions</h3>
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
                  <td className="py-2.5 px-4">All secrets &amp; config (database passwords, API keys)</td>
                  <td className="py-2.5 px-4 font-mono text-xs">664 (root:1000)</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">/opt/smsly-hosting/.credentials</td>
                  <td className="py-2.5 px-4">Admin login info</td>
                  <td className="py-2.5 px-4 font-mono text-xs">600</td>
                </tr>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5 px-4 font-mono text-xs">/opt/smsly-hosting/caddy-config/</td>
                  <td className="py-2.5 px-4">Caddy configuration &amp; certificates</td>
                  <td className="py-2.5 px-4 font-mono text-xs">775 (1000:1000)</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>
            The <code>.env</code> file needs <code>664</code> permissions with group ownership by GID 1000 because the backend Docker container runs as user <code>smsly</code> (UID 1000). This allows the domain-config signal to persist domain changes from the UI back to <code>.env</code> without requiring SSH access.
          </p>


          {/* ──── Architecture Reference (Quick Summary) ──── */}
          <h2 id="architecture-summary" className="text-2xl font-bold flex items-center gap-2 mt-12">
            <Server className="w-5 h-5 text-emerald-600" /> Architecture Summary
          </h2>

          <p>Understanding the request flow helps diagnose issues:</p>

          <div className="bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl p-5 my-4 not-prose">
            <pre className="text-xs text-slate-700 dark:text-slate-300 font-mono leading-relaxed">
{`Browser → https://your-domain.com
  │
  ▼
Caddy (port 443)
  ├─ Terminates TLS (Let's Encrypt cert)
  ├─ On-demand TLS: asks backend "is this domain allowed?"
  │    → GET /api/v1/services/check-domain/?domain=your-domain.com
  │    → 200 OK = proceed, 404 = reject
  ├─ /api/*, /ws/*, /admin/*, /health → backend (port 8000)
  ├─ /static/* → served directly from volume
  └─ /* → frontend (port 3000)`}
            </pre>
          </div>

          <p>
            <strong>Key insight:</strong> Caddy&apos;s on-demand TLS &quot;ask&quot; endpoint at <code>/api/v1/services/check-domain/</code> is the gatekeeper for certificate issuance. If it returns 404, Caddy will not obtain a Let&apos;s Encrypt certificate for that domain. The endpoint checks (in order): PlatformConfig primary domain, managed servers, service public domains, verified custom domains, and addon domains.
          </p>

          {/* Navigation */}
          <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700">
            <Link href="/docs/getting-started" className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
              Next: Getting Started <ArrowRight size={14} />
            </Link>
          </div>

        </article>
      </div>
    </main>
  );
}
