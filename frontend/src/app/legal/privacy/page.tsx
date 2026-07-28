import Link from 'next/link';
import { ArrowLeft, Shield } from 'lucide-react';

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-slate-50/60 to-white dark:from-slate-900/40 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-slate-500 dark:text-slate-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Home
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-slate-100 dark:bg-slate-800 rounded-xl">
              <Shield className="w-5 h-5 text-slate-700 dark:text-slate-300" />
            </div>
            <span className="text-sm font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider">Legal</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
            Privacy Policy
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
            How we collect, use, and protect your information.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <p className="text-sm text-slate-400 dark:text-slate-500">Last updated: July 27, 2026</p>

        <h2>1. Introduction</h2>
        <p>
          Grid (&quot;we,&quot; &quot;our,&quot; or &quot;us&quot;) is a free, open-source Platform-as-a-Service operated by SMSLYCLOUD. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you use the Grid platform, website, and related services (collectively, the &quot;Service&quot;).
        </p>
        <p>
          Since Grid is self-hosted, your application data, environment variables, and deployments remain on your own infrastructure. We do not have access to your data unless you explicitly grant it.
        </p>

        <h2>2. Information We Collect</h2>

        <h3>2.1 Information You Provide</h3>
        <ul>
          <li><strong>Account information</strong> — When you create an admin account, we store your username, email address, and an encrypted password hash.</li>
          <li><strong>OAuth credentials</strong> — When you connect a GitHub account, we store the access token (encrypted) and basic profile information needed for repository access and webhook management.</li>
          <li><strong>Configuration data</strong> — Domain names, SSL settings, environment variables, and deployment configurations you enter through the dashboard or API.</li>
          <li><strong>Payment information</strong> — If you purchase a paid plan, payment processing is handled by our payment provider. We do not store credit card numbers on our servers.</li>
        </ul>

        <h3>2.2 Information Collected Automatically</h3>
        <ul>
          <li><strong>Usage logs</strong> — API request logs, deployment history, and audit trails are stored locally on your Grid instance.</li>
          <li><strong>Container metrics</strong> — CPU, memory, and network metrics collected by the autoscaler and intelligence subsystem for scaling decisions.</li>
          <li><strong>Error reports</strong> — Build logs and deployment error reports are stored on your instance for debugging.</li>
        </ul>

        <h3>2.3 Information We Do NOT Collect</h3>
        <ul>
          <li>Your source code (it stays on your server or Git provider)</li>
          <li>Your database contents</li>
          <li>Your users&apos; personal data</li>
          <li>Environmental metrics from third-party services unless you configure them</li>
        </ul>

        <h2>3. How We Use Information</h2>
        <p>We use the information we collect to:</p>
        <ul>
          <li>Provide, maintain, and improve the Service</li>
          <li>Process deployments and manage your infrastructure</li>
          <li>Send important service notifications (security alerts, updates)</li>
          <li>Respond to support requests</li>
          <li>Detect and prevent fraud, abuse, and security incidents</li>
          <li>Comply with legal obligations</li>
        </ul>

        <h2>4. Data Storage and Security</h2>
        <p>
          Your data is stored on your own infrastructure. Grid uses industry-standard security measures including:
        </p>
        <ul>
          <li>Fernet encryption (AES-128-CBC) for secrets at rest</li>
          <li>TLS for data in transit</li>
          <li>Encrypted database fields for sensitive configuration</li>
          <li>Role-based access control for the dashboard</li>
          <li>Immutable audit logging for all state changes</li>
        </ul>
        <p>
          For self-hosted instances, you are responsible for the security of your own server, including firewall configuration, access controls, and backup procedures.
        </p>

        <h2>5. Data Retention</h2>
        <p>
          We retain your information for as long as your account is active or as needed to provide the Service. Specifically:
        </p>
        <ul>
          <li><strong>Account data</strong> — Retained until you delete your account</li>
          <li><strong>Deployment logs</strong> — Stored on your instance, managed by your retention settings</li>
          <li><strong>Audit logs</strong> — Immutable and retained indefinitely for compliance</li>
          <li><strong>Backups</strong> — Managed by your backup configuration</li>
        </ul>

        <h2>6. Data Sharing</h2>
        <p>
          We do not sell, trade, or otherwise transfer your information to third parties. We may share information only in the following circumstances:
        </p>
        <ul>
          <li><strong>With your consent</strong> — When you explicitly authorize sharing</li>
          <li><strong>Service providers</strong> — Third-party services you integrate with (GitHub, Cloudflare, payment processors) receive only the data necessary for their function</li>
          <li><strong>Legal requirements</strong> — When required by law, regulation, or valid legal process</li>
          <li><strong>Security</strong> — To protect the rights, property, or safety of Grid, our users, or the public</li>
        </ul>

        <h2>7. Third-Party Services</h2>
        <p>The Grid platform may integrate with the following third-party services:</p>
        <ul>
          <li><strong>GitHub</strong> — For repository access and webhook management (via GitHub App or OAuth)</li>
          <li><strong>Cloudflare</strong> — For wildcard SSL certificates (optional, via API token)</li>
          <li><strong>Let&apos;s Encrypt</strong> — For automatic SSL certificate provisioning</li>
          <li><strong>AI Providers</strong> — OpenAI, Anthropic, Google, and others (optional, admin-configured)</li>
        </ul>
        <p>
          Each third-party integration is opt-in. No data is sent to external services unless you explicitly configure and enable the integration.
        </p>

        <h2>8. Your Rights</h2>
        <p>Depending on your jurisdiction, you may have the following rights:</p>
        <ul>
          <li><strong>Access</strong> — Request a copy of the personal data we hold about you</li>
          <li><strong>Correction</strong> — Request correction of inaccurate data</li>
          <li><strong>Deletion</strong> — Request deletion of your account and associated data</li>
          <li><strong>Portability</strong> — Request your data in a machine-readable format</li>
          <li><strong>Objection</strong> — Object to processing of your personal data</li>
        </ul>
        <p>
          Since Grid is self-hosted, most of these rights are exercised directly on your own instance. For account-level requests, contact us at <a href="mailto:privacy@smsly.cloud">privacy@smsly.cloud</a>.
        </p>

        <h2>9. Changes to This Policy</h2>
        <p>
          We may update this Privacy Policy from time to time. We will notify you of any material changes by posting the new policy on this page and updating the &quot;Last updated&quot; date. Your continued use of the Service after changes constitutes acceptance of the updated policy.
        </p>

        <h2>10. Contact Us</h2>
        <p>
          If you have questions about this Privacy Policy, please contact us at <a href="mailto:privacy@smsly.cloud">privacy@smsly.cloud</a> or visit our <Link href="https://github.com/SMSLYCLOUD/smsly-hosting">GitHub repository</Link>.
        </p>
      </div>
    </main>
  );
}
