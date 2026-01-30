'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, Server, Database, Globe, Loader2 } from "lucide-react";
import { servicesApi, Service } from '@/lib/api';

export default function DashboardPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const svcs = await servicesApi.list();
        setServices(svcs || []);
      } catch (err) {
        console.error('Failed to fetch services:', err);
        setServices([]);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const activeDeployments = services.filter(s => s.latest_deployment?.status === 'RUNNING').length;
  const totalServices = services.length;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-4">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Overview</h2>
      </div>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Services</CardTitle>
            <Server className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalServices}</div>
            <p className="text-xs text-muted-foreground">Deployed services</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Deployments</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{activeDeployments}</div>
            <p className="text-xs text-muted-foreground">Running smoothly</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Databases</CardTitle>
            <Database className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">-</div>
            <p className="text-xs text-muted-foreground">Coming soon</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Cloud Providers</CardTitle>
            <Globe className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">1</div>
            <p className="text-xs text-muted-foreground">Local Docker</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
        <Card className="col-span-4">
          <CardHeader>
            <CardTitle>Recent Services</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {services.length === 0 ? (
                <p className="text-sm text-muted-foreground">No services deployed yet. Create your first service!</p>
              ) : (
                services.slice(0, 5).map((svc) => (
                  <div key={svc.id} className="flex items-center justify-between">
                    <div className="space-y-1">
                      <p className="text-sm font-medium leading-none">{svc.name}</p>
                      <p className="text-sm text-muted-foreground">{svc.repository_url || 'Docker deployment'}</p>
                    </div>
                    <div className={`font-medium text-xs px-2 py-1 rounded ${svc.latest_deployment?.status === 'RUNNING' ? 'bg-emerald-500/20 text-emerald-500' :
                        svc.latest_deployment?.status === 'FAILED' ? 'bg-red-500/20 text-red-500' :
                          'bg-yellow-500/20 text-yellow-500'
                      }`}>
                      {svc.latest_deployment?.status || 'PENDING'}
                    </div>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
        <Card className="col-span-3">
          <CardHeader>
            <CardTitle>Quick Actions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-sm text-muted-foreground space-y-2">
              <p>• <a href="/new" className="text-primary hover:underline">Deploy a new service</a></p>
              <p>• <a href="/store" className="text-primary hover:underline">Browse app templates</a></p>
              <p>• <a href="/services" className="text-primary hover:underline">View all services</a></p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
