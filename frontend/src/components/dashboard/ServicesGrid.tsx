'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import {
  Server,
  Database,
  Activity,
  Terminal,
  ExternalLink,
  MoreVertical,
  Play,
  Square
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Service } from '@/lib/api';

interface ServicesGridProps {
  services: Service[];
}

export function ServicesGrid({ services }: ServicesGridProps) {
  const router = useRouter();

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 p-6">
      {services.map((service) => (
        <Card
            key={service.id}
            className="group hover:border-emerald-500/50 transition-all duration-300 bg-card/50 backdrop-blur-sm"
        >
          {/* Header */}
          <div className="p-4 border-b border-border/50 flex justify-between items-start">
            <div className="flex gap-3">
              <div className="p-2.5 rounded-lg bg-muted border border-border shadow-inner">
                {service.name.includes('db') || service.name.includes('postgres') ? (
                    <Database size={20} className="text-blue-500" />
                ) : service.name.includes('redis') ? (
                    <Activity size={20} className="text-red-500" />
                ) : (
                    <Server size={20} className="text-emerald-500" />
                )}
              </div>
              <div>
                <h3 className="font-bold text-sm text-foreground tracking-tight cursor-pointer hover:underline" onClick={() => router.push(`/services/${service.id}`)}>
                    {service.name}
                </h3>
                <p className="text-[11px] text-muted-foreground font-mono truncate w-32">
                    {service.latest_deployment?.commit_hash.substring(0,7) || 'HEAD'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${
                    service.latest_deployment?.status === 'ACTIVE' ? 'bg-emerald-500 animate-pulse' :
                    service.latest_deployment?.status === 'FAILED' ? 'bg-red-500' : 'bg-yellow-500'
                }`} />
                <Button variant="ghost" size="icon" className="h-6 w-6">
                    <MoreVertical size={14} />
                </Button>
            </div>
          </div>

          {/* Metrics / Info */}
          <div className="p-4 space-y-3">
            <div className="flex justify-between items-center text-xs text-muted-foreground font-mono">
                <span>CPU</span>
                <span className="text-foreground">{service.cpu_cores} vCPU</span>
            </div>
            <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full bg-blue-500 rounded-full" style={{ width: '25%' }} />
            </div>

            <div className="flex justify-between items-center text-xs text-muted-foreground font-mono">
                <span>MEM</span>
                <span className="text-foreground">{service.memory_mb} MB</span>
            </div>
            <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full bg-purple-500 rounded-full" style={{ width: '40%' }} />
            </div>
          </div>

          {/* Footer / Actions */}
          <div className="p-3 border-t border-border/50 bg-muted/20 flex justify-between items-center">
            <div className="flex gap-1">
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 hover:bg-emerald-500/10 hover:text-emerald-500"
                    title="Logs"
                    onClick={() => router.push(`/services/${service.id}?tab=logs`)}
                >
                    <Terminal size={14} />
                </Button>
                {service.public_domain && (
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 hover:bg-blue-500/10 hover:text-blue-500"
                        title="Open App"
                        onClick={() => window.open(`https://${service.public_domain}`, '_blank')}
                    >
                        <ExternalLink size={14} />
                    </Button>
                )}
            </div>
            <div className="flex gap-1">
                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-red-500">
                    <Square size={12} fill="currentColor" />
                </Button>
                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-emerald-500">
                    <Play size={12} fill="currentColor" />
                </Button>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
