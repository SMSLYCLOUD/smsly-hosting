'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Search, BookOpen, Rocket, Settings, Code, Shield, Database, Terminal, ArrowRight, FileText, Server, Globe, Download, RefreshCw, HardDrive, Key, Wrench } from 'lucide-react';


const categories = [
  {
    title: 'Getting Started',
    icon: Rocket,
    color: 'bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300',
    articles: [
      { title: 'Installation Guide', desc: 'Complete setup on Ubuntu — one command', href: '/docs/install' },
      { title: 'Quick Start (5 Minutes)', desc: 'Deploy your first app after installation', href: '/docs/install#fresh-installation' },
      { title: 'System Requirements', desc: 'CPU, RAM, OS, and port requirements', href: '/docs/install#system-requirements' },
      { title: 'Deployment Modes', desc: 'IP Mode vs SSL Mode explained', href: '/docs/install#deployment-modes' },
    ],
  },
  {
    title: 'Updates & Maintenance',
    icon: RefreshCw,
    color: 'bg-teal-100 dark:bg-teal-900/50 text-teal-700 dark:text-teal-300',
    articles: [
      { title: 'Update CloudNeuron', desc: 'Full, frontend-only, or backend-only update', href: '/docs/install#updating-cloudneuron' },
      { title: 'Rollback on Failure', desc: 'Automatic and manual rollback procedures', href: '/docs/install#rollback-on-failure' },
      { title: 'Managing Services', desc: 'View logs, restart containers, health checks', href: '/docs/install#managing-services' },
      { title: 'Uninstallation', desc: 'Complete or partial removal guide', href: '/docs/install#uninstallation' },
    ],
  },
  {
    title: 'Deployment',
    icon: Server,
    color: 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300',
    articles: [
      { title: 'Git Push Deploys', desc: 'Automatic deploys on every push', href: '/docs/install#updating-cloudneuron' },
      { title: 'Environment Variables', desc: 'Managing secrets and configuration', href: '/docs/install#credential-locations' },
      { title: 'Custom Domains & SSL', desc: 'Set up your own domain with auto-SSL', href: '/docs/install#ssl--custom-domains' },
      { title: 'Preview Environments', desc: 'Ephemeral environments per PR', href: '/docs/install#managing-services' },
    ],
  },
  {
    title: 'Configuration',
    icon: Settings,
    color: 'bg-violet-100 dark:bg-violet-900/50 text-violet-700 dark:text-violet-300',
    articles: [
      { title: 'Build Configuration', desc: 'Customize your build process', href: '/docs/install#fresh-installation' },
      { title: 'Scaling & Resources', desc: 'CPU, memory, and replica settings', href: '/docs/install#system-requirements' },
      { title: 'Networking', desc: 'Ports, internal services, and routing', href: '/docs/install#container-map' },
      { title: 'Health Checks', desc: 'Configure liveness and readiness probes', href: '/docs/install#health-check' },
    ],
  },
  {
    title: 'Database',
    icon: Database,
    color: 'bg-cyan-100 dark:bg-cyan-900/50 text-cyan-700 dark:text-cyan-300',
    articles: [
      { title: 'Database Backup', desc: 'Manual and automated backup procedures', href: '/docs/install#database-operations' },
      { title: 'Database Restore', desc: 'Restore from backup files', href: '/docs/install#restore-from-backup' },
      { title: 'Reset Admin Password', desc: 'Recover admin access via CLI', href: '/docs/install#reset-admin-password' },
      { title: 'Connection Pooling', desc: 'PgBouncer and connection management', href: '/docs/install#database-operations' },
    ],
  },
  {
    title: 'Security',
    icon: Shield,
    color: 'bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-300',
    articles: [
      { title: 'Security Hardening', desc: 'Post-install checklist and UFW firewall', href: '/docs/install#security-hardening' },
      { title: 'Zero Trust Architecture', desc: 'How CloudNeuron secures your infrastructure', href: '/docs/install#security-hardening' },
      { title: 'Secret Management', desc: 'Encrypted env vars with rotation', href: '/docs/install#credential-locations' },
      { title: 'Troubleshooting', desc: 'Common issues and solutions', href: '/docs/install#troubleshooting' },
    ],
  },
  {
    title: 'API Reference',
    icon: Code,
    color: 'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300',
    articles: [
      { title: 'REST API Overview', desc: 'Authentication, pagination, and errors', href: '/docs/install#credential-locations' },
      { title: 'Services API', desc: 'Create, update, and manage services', href: '/docs/install#managing-services' },
      { title: 'Deployments API', desc: 'Trigger and monitor deployments', href: '/docs/install#updating-cloudneuron' },
      { title: 'Templates API', desc: 'List and deploy from templates', href: '/docs/install#fresh-installation' },
    ],
  },
];

const popularArticles = [
  { title: 'Install CloudNeuron', icon: Download, href: '/docs/install' },
  { title: 'Update Software', icon: RefreshCw, href: '/docs/install#updating-cloudneuron' },
  { title: 'Custom Domains', icon: Globe, href: '/docs/install#ssl--custom-domains' },
  { title: 'Environment Variables', icon: Terminal, href: '/docs/install#credential-locations' },
  { title: 'Database Backups', icon: HardDrive, href: '/docs/install#database-operations' },
  { title: 'Troubleshooting', icon: Wrench, href: '/docs/install#troubleshooting' },
];

export default function DocsPage() {
  const [search, setSearch] = useState('');

  const filteredCategories = search
    ? categories.map(cat => ({
        ...cat,
        articles: cat.articles.filter(
          a => a.title.toLowerCase().includes(search.toLowerCase()) || a.desc.toLowerCase().includes(search.toLowerCase())
        ),
      })).filter(cat => cat.articles.length > 0)
    : categories;

  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">


      {/* Hero */}
      <section className="pt-32 pb-12 px-4 text-center bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-4xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 text-sm font-medium rounded-full mb-6">
            <BookOpen className="w-3.5 h-3.5" />
            Documentation
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6">
            How Can We Help?
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-400 mb-8">
            Everything you need to install, deploy, and scale with CloudNeuron.
          </p>

          {/* Search */}
          <div className="max-w-xl mx-auto relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={20} />
            <input
              type="text"
              placeholder="Search documentation..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-12 pr-4 py-4 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white text-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition-all"
            />
          </div>
        </div>
      </section>

      {/* Popular Articles */}
      {!search && (
        <section className="py-12 px-4 border-b border-slate-200 dark:border-slate-800">
          <div className="max-w-6xl mx-auto">
            <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-6">Popular Articles</h2>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              {popularArticles.map((article) => (
                <Link
                  key={article.title}
                  href={article.href}
                  className="flex flex-col items-center gap-2 p-4 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-emerald-300 dark:hover:border-emerald-700 hover:shadow-md transition-all text-center group"
                >
                  <article.icon className="w-6 h-6 text-emerald-600 dark:text-emerald-400 group-hover:scale-110 transition-transform" />
                  <span className="text-xs font-medium text-slate-700 dark:text-slate-300">{article.title}</span>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Documentation Grid */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
            {filteredCategories.map((category) => (
              <div key={category.title} className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 overflow-hidden hover:shadow-lg transition-shadow">
                <div className="p-6 border-b border-slate-100 dark:border-slate-800">
                  <div className="flex items-center gap-3">
                    <div className={`p-2 rounded-lg ${category.color}`}>
                      <category.icon className="w-5 h-5" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-900 dark:text-white">{category.title}</h3>
                  </div>
                </div>
                <div className="divide-y divide-slate-100 dark:divide-slate-800">
                  {category.articles.map((article) => (
                    <Link
                      key={article.title}
                      href={article.href}
                      className="flex items-center justify-between px-6 py-4 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors group"
                    >
                      <div>
                        <p className="font-medium text-slate-900 dark:text-white group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition-colors text-sm">{article.title}</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{article.desc}</p>
                      </div>
                      <ArrowRight className="w-4 h-4 text-slate-300 dark:text-slate-600 group-hover:text-emerald-500 group-hover:translate-x-0.5 transition-all flex-shrink-0" />
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Update Software CTA ─── */}
      <section className="py-12 px-4 bg-gradient-to-r from-emerald-50 to-teal-50 dark:from-emerald-950/30 dark:to-teal-950/30 border-t border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-4xl mx-auto flex flex-col md:flex-row items-center gap-6">
          <div className="flex-shrink-0 p-4 bg-emerald-100 dark:bg-emerald-900/50 rounded-2xl">
            <RefreshCw className="w-8 h-8 text-emerald-700 dark:text-emerald-300" />
          </div>
          <div className="flex-1 text-center md:text-left">
            <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-1">Keep CloudNeuron Updated</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Update your installation from the terminal with a single command, or trigger an update from Settings → System.
            </p>
          </div>
          <div className="flex gap-3">
            <Link
              href="/docs/install#updating-cloudneuron"
              className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-bold text-emerald-700 dark:text-emerald-300 bg-white dark:bg-slate-900 border border-emerald-300 dark:border-emerald-700 rounded-xl hover:shadow-md transition-all"
            >
              <FileText className="w-4 h-4" /> Read Guide
            </Link>
            <Link
              href="/settings"
              className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-bold text-white bg-emerald-600 rounded-xl hover:bg-emerald-700 transition-all"
            >
              <Settings className="w-4 h-4" /> Update Now
            </Link>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-16 bg-slate-50 dark:bg-slate-900">
        <div className="max-w-4xl mx-auto px-4 text-center">
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white mb-3">Can&apos;t find what you&apos;re looking for?</h2>
          <p className="text-slate-600 dark:text-slate-400 mb-6">Our support team is here to help.</p>
          <Link
            href="/contact"
            className="inline-flex items-center gap-2 px-6 py-3 text-sm font-bold text-white bg-emerald-600 rounded-xl hover:bg-emerald-700 transition-all"
          >
            Contact Support <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>
    </main>
  );
}
