'use client';

import { Activity, CheckCircle2, AlertTriangle, XCircle, RefreshCw, Clock } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';

const services = [
  { name: 'API Gateway', status: 'operational', uptime: '99.99%', responseTime: '42ms' },
  { name: 'Frontend CDN', status: 'operational', uptime: '100%', responseTime: '12ms' },
  { name: 'Build System', status: 'operational', uptime: '99.97%', responseTime: '890ms' },
  { name: 'Container Orchestrator', status: 'operational', uptime: '99.99%', responseTime: '28ms' },
  { name: 'Database Cluster', status: 'operational', uptime: '99.99%', responseTime: '3ms' },
  { name: 'Redis Cache', status: 'operational', uptime: '100%', responseTime: '1ms' },
  { name: 'Object Storage', status: 'operational', uptime: '99.99%', responseTime: '45ms' },
  { name: 'SSL Certificate Manager', status: 'operational', uptime: '100%', responseTime: '120ms' },
];

const incidents = [
  {
    date: 'Feb 8, 2026',
    title: 'Build System Degraded Performance',
    status: 'resolved',
    description: 'Build queue experienced elevated latency due to high demand. Additional build workers were provisioned. No deployments were affected.',
    duration: '23 minutes',
  },
  {
    date: 'Feb 1, 2026',
    title: 'Scheduled Maintenance — Database Cluster',
    status: 'resolved',
    description: 'Planned maintenance window for PostgreSQL version upgrade. Zero downtime achieved via rolling restart.',
    duration: '45 minutes',
  },
  {
    date: 'Jan 25, 2026',
    title: 'API Gateway — Elevated Error Rate',
    status: 'resolved',
    description: 'Brief spike in 502 errors due to upstream certificate renewal. Automated failover kicked in within 90 seconds.',
    duration: '4 minutes',
  },
];

const uptimeData = Array.from({ length: 90 }, (_, i) => ({
  day: i,
  status: i === 45 ? 'degraded' : i === 67 ? 'degraded' : 'operational',
}));

const statusConfig = {
  operational: { icon: CheckCircle2, color: 'text-emerald-500', bg: 'bg-emerald-500', label: 'Operational' },
  degraded: { icon: AlertTriangle, color: 'text-amber-500', bg: 'bg-amber-500', label: 'Degraded' },
  outage: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500', label: 'Major Outage' },
  resolved: { icon: CheckCircle2, color: 'text-emerald-500', bg: 'bg-emerald-500', label: 'Resolved' },
};

export default function StatusPage() {
  const allOperational = services.every(s => s.status === 'operational');

  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <Navbar />

      {/* Hero */}
      <section className="pt-32 pb-12 px-4 text-center">
        <div className="max-w-4xl mx-auto">
          <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-bold mb-6 ${
            allOperational
              ? 'bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300'
              : 'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300'
          }`}>
            {allOperational ? (
              <><CheckCircle2 className="w-4 h-4" /> All Systems Operational</>
            ) : (
              <><AlertTriangle className="w-4 h-4" /> Some Systems Degraded</>
            )}
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-4">
            System Status
          </h1>
          <p className="text-slate-500 dark:text-slate-400 flex items-center justify-center gap-2">
            <RefreshCw className="w-4 h-4" /> Updated every 60 seconds
          </p>
        </div>
      </section>

      {/* 90-Day Uptime Bar */}
      <section className="px-4 pb-12">
        <div className="max-w-4xl mx-auto">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">90-Day Uptime</span>
            <span className="text-sm font-bold text-emerald-600 dark:text-emerald-400">99.98%</span>
          </div>
          <div className="flex gap-[2px] h-8 rounded-lg overflow-hidden">
            {uptimeData.map((day, i) => (
              <div
                key={i}
                className={`flex-1 ${day.status === 'operational' ? 'bg-emerald-500' : day.status === 'degraded' ? 'bg-amber-500' : 'bg-red-500'} hover:opacity-80 transition-opacity`}
                title={`Day ${90 - i}: ${day.status}`}
              />
            ))}
          </div>
          <div className="flex justify-between mt-2 text-xs text-slate-400">
            <span>90 days ago</span>
            <span>Today</span>
          </div>
        </div>
      </section>

      {/* Service Status */}
      <section className="px-4 pb-24">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-6">Services</h2>
          <div className="rounded-2xl border border-slate-200 dark:border-slate-800 divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden bg-white dark:bg-slate-900">
            {services.map((service) => {
              const config = statusConfig[service.status as keyof typeof statusConfig];
              const Icon = config.icon;
              return (
                <div key={service.name} className="flex items-center justify-between px-6 py-4 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
                  <div className="flex items-center gap-3">
                    <Icon className={`w-5 h-5 ${config.color}`} />
                    <span className="font-medium text-slate-900 dark:text-white">{service.name}</span>
                  </div>
                  <div className="flex items-center gap-6">
                    <span className="text-xs text-slate-400 hidden sm:block">
                      <Clock className="w-3 h-3 inline mr-1" />{service.responseTime}
                    </span>
                    <span className="text-xs text-slate-400 hidden sm:block">{service.uptime} uptime</span>
                    <span className={`text-xs font-semibold ${config.color}`}>{config.label}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Incident History */}
      <section className="py-24 px-4 bg-slate-50 dark:bg-slate-900 border-t border-slate-200 dark:border-slate-800">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-8">Recent Incidents</h2>
          <div className="space-y-4">
            {incidents.map((incident, i) => {
              const config = statusConfig[incident.status as keyof typeof statusConfig];
              return (
                <div key={i} className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-800/50 p-6">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h3 className="font-bold text-slate-900 dark:text-white">{incident.title}</h3>
                      <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">{incident.date} · {incident.duration}</p>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full ${
                      incident.status === 'resolved'
                        ? 'bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300'
                        : 'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300'
                    }`}>
                      <Activity className="w-3 h-3" />
                      {config.label}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 dark:text-slate-400">{incident.description}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </main>
  );
}
