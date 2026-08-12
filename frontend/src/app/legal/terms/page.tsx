import Link from 'next/link';
import { ArrowLeft, FileText } from 'lucide-react';

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-slate-50/60 to-white dark:from-slate-900/40 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-slate-500 dark:text-slate-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Home
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-slate-100 dark:bg-slate-800 rounded-xl">
              <FileText className="w-5 h-5 text-slate-700 dark:text-slate-300" />
            </div>
            <span className="text-sm font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider">Legal</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
            Terms of Service
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
            The rules and guidelines governing your use of the Grid platform.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <p className="text-sm text-slate-400 dark:text-slate-500">Last updated: July 27, 2026</p>

        <h2>1. Acceptance of Terms</h2>
        <p>
          By accessing or using Grid (&quot;the Service&quot;), operated by Trulay (&quot;we,&quot; &quot;our,&quot; or &quot;us&quot;), you agree to be bound by these Terms of Service (&quot;Terms&quot;). If you do not agree to these Terms, do not use the Service.
        </p>
        <p>
          These Terms apply to all visitors, users, and others who access or use the Service. By using the Service on behalf of an organization, you represent that you have the authority to bind that organization to these Terms.
        </p>

        <h2>2. Description of Service</h2>
        <p>
          Grid is a free, open-source Platform-as-a-Service (PaaS) that allows you to deploy, manage, and scale applications on your own infrastructure. The Service includes:
        </p>
        <ul>
          <li>A web-based dashboard for managing deployments, services, and infrastructure</li>
          <li>A REST API for programmatic access</li>
          <li>A CLI tool for terminal-based management</li>
          <li>Managed addons (databases, caching, vector search)</li>
          <li>AI-powered intelligence and autoscaling features</li>
          <li>Multi-server deployment and transfer capabilities</li>
        </ul>

        <h2>3. Account Registration</h2>
        <p>
          You are responsible for maintaining the confidentiality of your account credentials. You agree to notify us immediately of any unauthorized use of your account. You are responsible for all activities that occur under your account.
        </p>
        <p>
          You must be at least 18 years old (or the age of majority in your jurisdiction) to create an account and use the Service.
        </p>

        <h2>4. Acceptable Use</h2>
        <p>You agree not to use the Service to:</p>
        <ul>
          <li>Host, distribute, or transmit malicious software, malware, or content designed to harm others</li>
          <li>Conduct denial-of-service attacks or engage in network abuse</li>
          <li>Violate any applicable laws, regulations, or third-party rights</li>
          <li>Attempt to gain unauthorized access to other systems, networks, or data</li>
          <li>Send unsolicited communications (spam) or phishing attempts</li>
          <li>Host content that is illegal, harmful, threatening, abusive, or defamatory</li>
          <li>Resell or redistribute the Service without written authorization</li>
          <li>Circumvent usage limits, rate limits, or security measures</li>
          <li>Interfere with or disrupt the Service or servers connected to the Service</li>
        </ul>

        <h2>5. Your Data</h2>
        <p>
          You retain all rights to your data, source code, and deployments. Since Grid is self-hosted, your data remains on your infrastructure. We do not claim ownership over your content.
        </p>
        <p>
          You are solely responsible for the legality of the content you deploy and the data you store. You must comply with all applicable data protection laws, including GDPR, CCPA, and other privacy regulations relevant to your jurisdiction.
        </p>

        <h2>6. Open Source License</h2>
        <p>
          Grid is released under the MIT License. You are free to use, modify, and distribute the software in accordance with the license terms. The open-source nature of Grid does not alter these Terms for the hosted Service provided by Trulay.
        </p>

        <h2>7. Service Availability</h2>
        <p>
          We strive to maintain high availability but do not guarantee uninterrupted access to the Service. The Service may be temporarily unavailable due to:
        </p>
        <ul>
          <li>Scheduled maintenance</li>
          <li>Emergency maintenance or security patches</li>
          <li>Third-party service outages (cloud providers, DNS services)</li>
          <li>Force majeure events</li>
        </ul>
        <p>
          For self-hosted instances, availability depends on your own infrastructure, configuration, and maintenance.
        </p>

        <h2>8. Pricing and Payment</h2>
        <p>
          Grid is free and open-source. Paid plans may be offered for additional features, support, or managed hosting. Prices are listed on our <Link href="/pricing">pricing page</Link> and may change with 30 days&apos; notice.
        </p>
        <p>
          All payments are non-refundable unless otherwise required by applicable law. You may cancel your subscription at any time.
        </p>

        <h2>9. Intellectual Property</h2>
        <p>
          The Service, including its design, code, features, and documentation, is owned by Trulay and protected by copyright, trademark, and other intellectual property laws. You may not copy, modify, distribute, or reverse-engineer any part of the Service except as permitted by the open-source license.
        </p>

        <h2>10. Limitation of Liability</h2>
        <p>
          To the maximum extent permitted by law, Trulay and its affiliates shall not be liable for any indirect, incidental, special, consequential, or punitive damages, including but not limited to:
        </p>
        <ul>
          <li>Loss of profits, data, business, or goodwill</li>
          <li>Service interruptions or downtime</li>
          <li>Unauthorized access to your data</li>
          <li>Third-party actions or content</li>
        </ul>
        <p>
          Our total liability for any claims arising from the Service shall not exceed the amount you paid us in the 12 months preceding the claim, or $100, whichever is greater.
        </p>

        <h2>11. Disclaimer of Warranties</h2>
        <p>
          The Service is provided &quot;as is&quot; and &quot;as available&quot; without warranties of any kind, whether express or implied, including but not limited to implied warranties of merchantability, fitness for a particular purpose, and non-infringement. We do not warrant that the Service will be error-free, secure, or continuously available.
        </p>

        <h2>12. Indemnification</h2>
        <p>
          You agree to indemnify, defend, and hold harmless Trulay and its officers, directors, employees, and agents from any claims, damages, losses, liabilities, and expenses (including legal fees) arising from:
        </p>
        <ul>
          <li>Your use of the Service</li>
          <li>Your violation of these Terms</li>
          <li>Your violation of any rights of a third party</li>
          <li>Content you deploy or store on the Service</li>
        </ul>

        <h2>13. Termination</h2>
        <p>
          We may terminate or suspend your access to the Service immediately, without prior notice, for conduct that we determine, in our sole discretion, violates these Terms or is harmful to other users, us, or third parties, or for any other reason.
        </p>
        <p>
          Upon termination, your right to use the Service ceases immediately. You may request export of your data within 30 days of termination.
        </p>

        <h2>14. Changes to Terms</h2>
        <p>
          We reserve the right to modify these Terms at any time. We will provide notice of material changes by posting the updated Terms on this page and, where appropriate, by email. Your continued use of the Service after changes take effect constitutes acceptance of the updated Terms.
        </p>

        <h2>15. Governing Law</h2>
        <p>
          These Terms shall be governed by and construed in accordance with the laws of the jurisdiction in which Trulay operates, without regard to its conflict of law provisions. Any disputes shall be resolved in the courts of competent jurisdiction in that jurisdiction.
        </p>

        <h2>16. Severability</h2>
        <p>
          If any provision of these Terms is found to be unenforceable or invalid, that provision shall be limited or eliminated to the minimum extent necessary, and the remaining provisions shall remain in full force and effect.
        </p>

        <h2>17. Contact</h2>
        <p>
          If you have questions about these Terms, please contact us at <a href="mailto:legal@Trulay.co">legal@Trulay.co</a> or visit our <Link href="https://github.com/SMSLYCLOUD/smsly-hosting">GitHub repository</Link>.
        </p>
      </div>
    </main>
  );
}
