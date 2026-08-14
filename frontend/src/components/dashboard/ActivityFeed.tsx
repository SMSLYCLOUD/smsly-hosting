'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Clock, GitCommit, RotateCcw, Rocket, XCircle, CheckCircle2, Loader2, Timer } from 'lucide-react';
import api from '@/lib/api';
import { formatDistanceToNow } from 'date-fns';
import Link from 'next/link';

interface DeploymentEvent {
  id: string;
  service: string;
  service_name?: string;
  commit_hash: string;
  commit_message?: string;
  status: string;
  is_rollback?: boolean;
  created_at: string;
}

const statusConfig: Record<string, { icon: typeof Rocket; color: string; label: string }> = {
  ACTIVE: { icon: CheckCircle2, color: 'text-emerald-500', label: 'Deployed' },
  SUCCEEDED: { icon: CheckCircle2, color: 'text-emerald-500', label: 'Succeeded' },
  FAILED: { icon: XCircle, color: 'text-red-500', label: 'Failed' },
  BUILDING: { icon: Loader2, color: 'text-blue-500', label: 'Building' },
  DEPLOYING: { icon: Rocket, color: 'text-indigo-500', label: 'Deploying' },
  QUEUED: { icon: Timer, color: 'text-gray-500', label: 'Queued' },
  HEALTH_CHECK: { icon: Loader2, color: 'text-cyan-500', label: 'Health Check' },
  CANCELLED: { icon: XCircle, color: 'text-gray-400', label: 'Cancelled' },
};

export function ActivityFeed() {
  const [events, setEvents] = useState<DeploymentEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchActivity = async () => {
      try {
        const res = await api.get('/deployments/', { params: { ordering: '-created_at', page_size: 10 } });
        const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
        setEvents(data.slice(0, 10));
      } catch (err) {
        console.error('Failed to fetch activity:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchActivity();
    const interval = setInterval(fetchActivity, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Card className="card-enterprise h-full">
      <CardHeader className="flex flex-row items-center justify-between relative z-10">
        <CardTitle className="flex items-center gap-2">
          <Clock className="h-5 w-5 text-primary" />
          Recent Activity
        </CardTitle>
        <Badge variant="outline" className="text-xs">
          {events.length} events
        </Badge>
      </CardHeader>
      <CardContent className="relative z-10">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : events.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">No deployments yet.</p>
        ) : (
          <div className="space-y-1">
            {events.map((event) => {
              const cfg = statusConfig[event.status] || statusConfig.QUEUED;
              const Icon = event.is_rollback ? RotateCcw : cfg.icon;
              return (
                <Link
                  key={event.id}
                  href={`/services/${event.service}`}
                  className="flex items-center gap-3 p-2.5 rounded-lg hover:bg-primary/5 transition-colors group"
                >
                  <div className={`p-1.5 rounded-lg bg-muted/50 ${cfg.color}`}>
                    <Icon className={`h-3.5 w-3.5 ${event.status === 'BUILDING' || event.status === 'HEALTH_CHECK' ? 'animate-spin' : ''}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium truncate group-hover:text-primary transition-colors">
                        {event.service_name || event.service?.slice(0, 8)}
                      </span>
                      {event.is_rollback && (
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">rollback</Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {event.commit_hash && (
                        <span className="flex items-center gap-1 font-mono">
                          <GitCommit className="h-3 w-3" />
                          {event.commit_hash.slice(0, 7)}
                        </span>
                      )}
                      {event.commit_message && (
                        <span className="truncate max-w-[140px]">{event.commit_message}</span>
                      )}
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <Badge variant="outline" className={`text-[10px] ${cfg.color} border-current/20`}>
                      {cfg.label}
                    </Badge>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      {formatDistanceToNow(new Date(event.created_at), { addSuffix: true })}
                    </p>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
