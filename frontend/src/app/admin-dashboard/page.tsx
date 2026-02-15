'use client';

import { useEffect, useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Activity, Server, Box, DollarSign, Loader2, RefreshCw } from 'lucide-react';
import api from '@/lib/api';

interface PlatformStats {
  total_services: number;
  total_deployments: number;
  active_instances: number;
  revenue_estimate: number;
}

interface PlatformEvent {
  type: 'success' | 'error' | 'info';
  event: string;
  user: string;
  service: string;
  time: string;
}

export default function AdminDashboardPage() {
  const [stats, setStats] = useState<PlatformStats>({
    total_services: 0,
    total_deployments: 0,
    active_instances: 0,
    revenue_estimate: 0
  });
  const [events, setEvents] = useState<PlatformEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    try {
      // Fetch real data from multiple endpoints
      const [servicesRes, deploymentsRes, billingRes] = await Promise.allSettled([
        api.get('/services/'),
        api.get('/deployments/'),
        api.get('/billing/summary/'),
      ]);

      const services = servicesRes.status === 'fulfilled'
        ? (Array.isArray(servicesRes.value.data) ? servicesRes.value.data : servicesRes.value.data?.results || [])
        : [];
      const deployments = deploymentsRes.status === 'fulfilled'
        ? (Array.isArray(deploymentsRes.value.data) ? deploymentsRes.value.data : deploymentsRes.value.data?.results || [])
        : [];
      const billing = billingRes.status === 'fulfilled' ? billingRes.value.data : {};

      const activeCount = services.filter((s: any) => s.latest_deployment?.status === 'ACTIVE' || s.latest_deployment?.status === 'RUNNING').length;

      setStats({
        total_services: services.length,
        total_deployments: deployments.length,
        active_instances: activeCount,
        revenue_estimate: billing?.total_estimated_cost || 0,
      });

      // Build events from recent deployments
      const recentEvents: PlatformEvent[] = deployments.slice(0, 5).map((d: any) => ({
        type: d.status === 'ACTIVE' || d.status === 'RUNNING' ? 'success' : d.status === 'FAILED' ? 'error' : 'info',
        event: d.status === 'ACTIVE' ? 'Deployment Success' : d.status === 'FAILED' ? 'Build Failed' : `Status: ${d.status}`,
        user: d.triggered_by || d.user || '—',
        service: d.service_name || d.service?.name || `deploy-${d.id?.slice(0, 8)}`,
        time: d.created_at ? new Date(d.created_at).toLocaleString() : '—',
      }));
      setEvents(recentEvents);
    } catch (err) {
      console.error('Failed to fetch admin data:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchData();
  };

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="flex-1 p-6 md:p-12 max-w-7xl mx-auto w-full">
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-3xl font-bold text-foreground">Operator Command Center</h1>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium border border-border rounded-lg hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

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
            title="Est. Cost"
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
                {events.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-8 text-center text-muted-foreground">No recent events</td>
                  </tr>
                ) : (
                  events.map((evt, i) => <EventRow key={i} {...evt} />)
                )}
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
