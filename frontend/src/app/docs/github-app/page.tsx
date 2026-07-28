import Link from 'next/link';
import { ArrowLeft, ArrowRight, Github, Key, Globe, Bell, Shield, Settings } from 'lucide-react';

export default function GitHubAppSetupPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-slate-50/60 to-white dark:from-slate-950/60 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-slate-100 dark:bg-slate-800 rounded-xl">
              <Github className="w-5 h-5 text-slate-700 dark:text-slate-300" />
            </div>
            <span className="text-sm font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider">Integration</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
            GitHub App Setup
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
            Create and configure a GitHub App for automatic deployments, PR previews, and commit statuses.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">

        <div className="docs-callout docs-callout-info not-prose">
          <p className="!mt-0 text-sm">
            <strong>Why a GitHub App?</strong> A GitHub App provides automatic webhook management, commit deployment statuses in your PRs, and organization-level repo access — all without needing individual OAuth tokens for each user.
          </p>
        </div>

        <h2 id="prerequisites">Prerequisites</h2>
        <ul>
          <li>A GitHub account (personal or organization)</li>
          <li>Admin access to your Grid instance</li>
          <li>Your Grid instance must be publicly accessible (GitHub needs to send webhooks to it)</li>
        </ul>

        <h2 id="create-app">Step 1: Create the GitHub App</h2>
        <ol>
          <li>
            Go to <strong>GitHub → Settings → Developer settings → GitHub Apps → New GitHub App</strong>.
            <br />
            <span className="text-sm text-slate-500 dark:text-slate-400">
              Direct link: <code>https://github.com/settings/apps/new</code>
            </span>
          </li>
          <li>
            Fill in the basic information:
            <ul>
              <li><strong>GitHub App name</strong>: Something like <code>Grid Deploy</code> or <code>MyOrg Grid</code> (must be globally unique)</li>
              <li><strong>Homepage URL</strong>: Your Grid instance URL, e.g. <code>https://grid.example.com</code></li>
              <li><strong>Callback URL</strong>: <code>https://grid.example.com/auth/github/app/callback</code></li>
              <li><strong>Request user authorization (OAuth) during installation</strong>: Leave unchecked</li>
            </ul>
          </li>
        </ol>

        <h2 id="webhook-config">Step 2: Configure the Webhook</h2>
        <p>On the same creation page, under <strong>Webhook</strong>:</p>
        <ul>
          <li><strong>Active</strong>: Checked</li>
          <li><strong>Webhook URL</strong>: <code>https://grid.example.com/api/v1/webhooks/github/</code></li>
          <li><strong>Webhook secret</strong>: Generate a random string and save it. You&apos;ll need to set this as <code>GITHUB_WEBHOOK_SECRET</code> in your Grid environment.</li>
        </ul>

        <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-xl p-4 my-6 not-prose">
          <p className="text-sm text-amber-800 dark:text-amber-200">
            <strong>Important:</strong> The webhook secret must match the <code>GITHUB_WEBHOOK_SECRET</code> in your Grid <code>.env</code> file. If you already have a webhook secret from the OAuth integration, use the same one.
          </p>
        </div>

        <h2 id="permissions">Step 3: Set Permissions</h2>
        <p>Under <strong>Permissions → Repository permissions</strong>, set:</p>
        <div className="not-prose my-4">
          <div className="overflow-x-auto">
            <table className="w-full text-sm border border-slate-200 dark:border-slate-700 rounded-lg">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-800">
                  <th className="text-left p-3 font-semibold border-b border-slate-200 dark:border-slate-700">Permission</th>
                  <th className="text-left p-3 font-semibold border-b border-slate-200 dark:border-slate-700">Access</th>
                  <th className="text-left p-3 font-semibold border-b border-slate-200 dark:border-slate-700">Why</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Contents</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800"><code>Read-only</code></td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800 text-slate-600 dark:text-slate-400">Clone repos for builds</td>
                </tr>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Metadata</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800"><code>Read-only</code></td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800 text-slate-600 dark:text-slate-400">List repos and branches</td>
                </tr>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Pull requests</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800"><code>Read &amp; write</code></td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800 text-slate-600 dark:text-slate-400">PR preview environments</td>
                </tr>
                <tr>
                  <td className="p-3">Commit statuses</td>
                  <td className="p-3"><code>Read &amp; write</code></td>
                  <td className="p-3 text-slate-600 dark:text-slate-400">Deployment status badges on commits</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <p>Under <strong>Permissions → Account permissions</strong>, no changes needed.</p>

        <h2 id="events">Step 4: Subscribe to Events</h2>
        <p>Under <strong>Subscribe to events</strong>, check these:</p>
        <ul>
          <li><code>Installation</code> — Track when the app is installed or uninstalled</li>
          <li><code>Installation repositories</code> — Track repos added/removed from the installation</li>
          <li><code>Pull request</code> — Trigger preview environments on PRs</li>
          <li><code>Push</code> — Trigger deployments on push</li>
        </ul>

        <h2 id="create">Step 5: Create the App</h2>
        <p>Click <strong>Create GitHub App</strong>. After creation:</p>
        <ol>
          <li>
            <p><strong>Generate a private key</strong>: Scroll down to the &quot;Private keys&quot; section and click <strong>Generate a private key</strong>. A <code>.pem</code> file will be downloaded.</p>
          </li>
          <li>
            <p><strong>Note your App ID</strong>: It&apos;s shown at the top of the app settings page.</p>
          </li>
          <li>
            <p><strong>Note the App slug</strong>: Visible in the URL — <code>https://github.com/apps/YOUR-SLUG</code>.</p>
          </li>
        </ol>

        <h2 id="configure-grid">Step 6: Configure Grid</h2>
        <p>Add these to your Grid <code>.env</code> file (or set as environment variables):</p>
        <pre className="bg-slate-900 text-slate-100 rounded-xl p-4 overflow-x-auto text-sm">
          <code>{`# GitHub App Configuration
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"

# Webhook secret (same one you set in GitHub)
GITHUB_WEBHOOK_SECRET=your-random-secret-here`}</code></pre>

        <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-xl p-4 my-6 not-prose">
          <p className="text-sm text-amber-800 dark:text-amber-200">
            <strong>Private key formatting:</strong> The private key must include the <code>-----BEGIN RSA PRIVATE KEY-----</code> and <code>-----END RSA PRIVATE KEY-----</code> headers. Wrap it in quotes in your <code>.env</code> file. Newlines within the key are preserved.
          </p>
        </div>

        <p>Restart your Grid backend after updating the environment variables.</p>

        <h2 id="install-app">Step 7: Install the App on Your Repos</h2>
        <ol>
          <li>In Grid, go to <strong>Settings → GitHub → GitHub App</strong> and click <strong>Install App</strong>.</li>
          <li>GitHub will show you the installation page — select which repos to grant access to.</li>
          <li>After installation, you&apos;ll be redirected back to Grid. The installation is now linked to your account.</li>
        </ol>
        <p>Alternatively, install directly on GitHub:</p>
        <ul>
          <li>Go to <code>https://github.com/apps/YOUR-SLUG/installations/new</code></li>
          <li>Select repos and confirm</li>
        </ul>

        <h2 id="verify">Step 8: Verify</h2>
        <ul>
          <li>Push a commit to a connected repo — a deployment should trigger automatically</li>
          <li>Open a PR — a preview environment should be created</li>
          <li>Check the commit on GitHub — a deployment status badge should appear</li>
        </ul>

        <h2 id="permissions-explained">Why These Permissions?</h2>
        <div className="not-prose my-4 space-y-3">
          <div className="flex items-start gap-3 p-3 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-100 dark:border-slate-800">
            <Shield className="w-5 h-5 text-slate-500 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-white">Minimal by design</p>
              <p className="text-xs text-slate-600 dark:text-slate-400">Grid only requests read access to code and write access to commit statuses and PRs. It cannot modify your repositories.</p>
            </div>
          </div>
          <div className="flex items-start gap-3 p-3 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-100 dark:border-slate-800">
            <Key className="w-5 h-5 text-slate-500 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-white">Tokens are never stored</p>
              <p className="text-xs text-slate-600 dark:text-slate-400">Installation tokens are short-lived (1 hour) and generated on-demand. They are never persisted to the database.</p>
            </div>
          </div>
          <div className="flex items-start gap-3 p-3 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-100 dark:border-slate-800">
            <Bell className="w-5 h-5 text-slate-500 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-white">Automatic webhooks</p>
              <p className="text-xs text-slate-600 dark:text-slate-400">When you install the GitHub App on a repo, GitHub automatically sends push and PR events to Grid — no manual webhook configuration needed.</p>
            </div>
          </div>
        </div>

        <h2 id="troubleshooting">Troubleshooting</h2>

        <h3>Webhook deliveries failing (401/403)</h3>
        <p>Verify that <code>GITHUB_WEBHOOK_SECRET</code> in your Grid <code>.env</code> matches the webhook secret configured in the GitHub App settings.</p>

        <h3>App not showing up after install</h3>
        <p>Make sure the callback URL in the GitHub App settings matches your Grid instance URL exactly: <code>https://your-grid-domain.com/auth/github/app/callback</code></p>

        <h3>Private repos not building</h3>
        <p>Ensure the app has <strong>Contents: Read-only</strong> permission and that you&apos;ve selected the specific repos (or &quot;All repositories&quot;) during installation.</p>

        <h3>Commit statuses not appearing</h3>
        <p>Check that <strong>Commit statuses: Read &amp; write</strong> permission is set. Also verify the app installation covers the repository in question.</p>

        <h2 id="organization">Organization Installations</h2>
        <p>If you install the app on a GitHub organization:</p>
        <ul>
          <li>All members can deploy from org repos (once linked in Grid)</li>
          <li>Webhook events are scoped to the org</li>
          <li>The org admin may need to approve the installation depending on org settings</li>
        </ul>

        <h2 id="oauth-vs-app">OAuth vs GitHub App</h2>
        <p>Grid supports both OAuth and GitHub App integrations. They can coexist:</p>
        <div className="not-prose my-4">
          <div className="overflow-x-auto">
            <table className="w-full text-sm border border-slate-200 dark:border-slate-700 rounded-lg">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-800">
                  <th className="text-left p-3 font-semibold border-b border-slate-200 dark:border-slate-700">Feature</th>
                  <th className="text-left p-3 font-semibold border-b border-slate-200 dark:border-slate-700">OAuth</th>
                  <th className="text-left p-3 font-semibold border-b border-slate-200 dark:border-slate-700">GitHub App</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Auto webhooks</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Manual setup</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Automatic</td>
                </tr>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Commit statuses</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">No</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Yes</td>
                </tr>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Org-level access</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Per-user</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Per-installation</td>
                </tr>
                <tr>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Token storage</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Persistent (user token)</td>
                  <td className="p-3 border-b border-slate-100 dark:border-slate-800">Ephemeral (1hr install token)</td>
                </tr>
                <tr>
                  <td className="p-3">Rate limits</td>
                  <td className="p-3">Per-user</td>
                  <td className="p-3">Per-app (higher)</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> All Docs
          </Link>
          <Link href="/docs/deployments" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
            Deployments <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </main>
  );
}
