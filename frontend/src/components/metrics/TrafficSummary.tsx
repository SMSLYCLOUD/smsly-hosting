'use client';

import React from 'react';
import { Card } from '@/components/ui/card';
import { Globe, MapPin, Users, Activity } from 'lucide-react';

interface TrafficSummaryProps {
    totalRequests: number;
    uniqueCountries: number;
    uniqueIps: number;
    topCountries: Array<{ code: string; name: string; count: number; percentage: number }>;
}

export function TrafficSummary({ totalRequests, uniqueCountries, uniqueIps, topCountries }: TrafficSummaryProps) {
    return (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card className="p-4 border border-border bg-card">
                <div className="flex items-center justify-between">
                    <div>
                        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Total Requests</p>
                        <p className="text-2xl font-bold mt-1 text-cyan-500">{totalRequests.toLocaleString()}</p>
                    </div>
                    <Activity className="w-8 h-8 text-cyan-500/30" />
                </div>
            </Card>

            <Card className="p-4 border border-border bg-card">
                <div className="flex items-center justify-between">
                    <div>
                        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Countries</p>
                        <p className="text-2xl font-bold mt-1 text-blue-500">{uniqueCountries}</p>
                    </div>
                    <Globe className="w-8 h-8 text-blue-500/30" />
                </div>
            </Card>

            <Card className="p-4 border border-border bg-card">
                <div className="flex items-center justify-between">
                    <div>
                        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Unique IPs</p>
                        <p className="text-2xl font-bold mt-1 text-purple-500">{uniqueIps.toLocaleString()}</p>
                    </div>
                    <Users className="w-8 h-8 text-purple-500/30" />
                </div>
            </Card>

            <Card className="p-4 border border-border bg-card">
                <div className="flex items-center justify-between">
                    <div>
                        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Top Location</p>
                        <p className="text-2xl font-bold mt-1 text-emerald-500">
                            {topCountries[0]?.code || '—'}
                        </p>
                        <p className="text-[10px] text-muted-foreground">
                            {topCountries[0] ? `${topCountries[0].percentage}% of traffic` : 'No data'}
                        </p>
                    </div>
                    <MapPin className="w-8 h-8 text-emerald-500/30" />
                </div>
            </Card>
        </div>
    );
}
