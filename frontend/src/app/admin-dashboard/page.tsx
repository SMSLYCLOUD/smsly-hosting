'use client';

import { useEffect, useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Activity, Server, Box, DollarSign } from 'lucide-react';

export default function AdminDashboardPage() {
  const [stats, setStats] = useState({
    total_services: 0,
    total_deployments: 0,
    active_instances: 0,
    revenue_estimate: 0
  });

  useEffect(() => {
    // Simulate fetching aggregated stats
    setTimeout(() => {
        setStats({
            total_services: 142,
            total_deployments: 893,
            active_instances: 256,
            revenue_estimate: 12500
        });
    }, 500);
  }, []);

  return (
    <DashboardShell>

      <div className="flex-1 p-6 md:p-12 max-w-7xl mx-auto w-full">
        <h1 className="text-3xl font-bold mb-8 text-foreground">Operator Command Center</h1>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
            <StatsCard
                title="Total Services"
                value={stats.total_services}
                icon={<Server size={20} className="text-blue-500" />}
                color="border-blue-500"
            />
            <StatsCard
                title="Active Instances"
                value={stats.active_instances}
                icon={<Activity size={20} className="text-green-500" />}
                color="border-green-500"
            />
            <StatsCard
                title="Total Deployments"
                value={stats.total_deployments}
                icon={<Box size={20} className="text-purple-500" />}
                color="border-purple-500"
            />
            <StatsCard
                title="Est. Revenue"
                value={`$${stats.revenue_estimate.toLocaleString()}`}
                icon={<DollarSign size={20} className="text-yellow-500" />}
                color="border-yellow-500"
            />
        </div>

        <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
            <div className="p-6 border-b border-border">
                <h3 className="text-xl font-bold">Recent Platform Events</h3>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                    <thead className="bg-muted/50">
                        <tr>
                            <th className="px-6 py-3 font-medium text-muted-foreground">Event</th>
                            <th className="px-6 py-3 font-medium text-muted-foreground">User</th>
                            <th className="px-6 py-3 font-medium text-muted-foreground">Service</th>
                            <th className="px-6 py-3 font-medium text-muted-foreground">Time</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                        <EventRow type="success" event="Deployment Success" user="user_123" service="django-api-prod" time="2 mins ago" />
                        <EventRow type="error" event="Build Failed" user="user_456" service="react-frontend" time="15 mins ago" />
                        <EventRow type="info" event="Add-on Provisioned" user="user_789" service="redis-cache-01" time="1 hour ago" />
                    </tbody>
                </table>
            </div>
        </div>
      </div>
    </DashboardShell>
  );
}

function StatsCard({ title, value, icon, color }: any) {
    return (
        <div className={`bg-card p-6 rounded-xl shadow-sm border-l-4 ${color} border-y border-r border-border`}>
            <div className="flex items-center justify-between mb-2">
                <h3 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">{title}</h3>
                {icon}
            </div>
            <p className="text-3xl font-bold text-foreground">{value}</p>
        </div>
    );
}

function EventRow({ type, event, user, service, time }: any) {
    const color = type === 'success' ? 'text-emerald-500' : type === 'error' ? 'text-red-500' : 'text-blue-500';
    return (
        <tr className="hover:bg-muted/50 transition-colors">
            <td className={`px-6 py-4 font-medium ${color}`}>{event}</td>
            <td className="px-6 py-4 text-foreground">{user}</td>
            <td className="px-6 py-4 text-muted-foreground">{service}</td>
            <td className="px-6 py-4 text-muted-foreground">{time}</td>
        </tr>
    );
}
