'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { Globe, RefreshCw } from 'lucide-react';
import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';
import { servicesApi, systemApi } from '@/lib/api';

import 'maplibre-gl/dist/maplibre-gl.css';

const MapComponent = dynamic(() => import('./WorldTrafficMapImpl'), {
    ssr: false,
    loading: () => <Skeleton className="h-[400px] w-full rounded-lg" />,
});

interface CountryTraffic {
    code: string;
    name: string;
    count: number;
    percentage: number;
    unique_ips: number;
}

interface TrafficGeoData {
    countries: CountryTraffic[];
    top_cities: Array<{ city: string; country: string; count: number }>;
    total_requests: number;
    unique_ips: number;
    unique_countries: number;
    last_updated: string | null;
}

interface WorldTrafficMapProps {
    serviceId: string;
    trafficData?: TrafficGeoData | null;
}

export function WorldTrafficMap({ serviceId, trafficData: externalData }: WorldTrafficMapProps) {
    const [data, setData] = useState<TrafficGeoData | null>(externalData ?? null);
    const [loading, setLoading] = useState(!externalData);
    const [mapToken, setMapToken] = useState('');

    const fetchData = useCallback(async () => {
        try {
            const res = await servicesApi.getTrafficGeo(serviceId);
            setData(res);
        } catch (err) {
            console.error('Failed to load traffic geo:', err);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    // Sync external data when parent re-fetches
    useEffect(() => {
        if (externalData) {
            setData(externalData);
            setLoading(false);
        }
    }, [externalData]);

    useEffect(() => {
        systemApi.getConfig().then((config: any) => {
            setMapToken(config?.MAPBOX_TOKEN || '');
        });
        // Only poll when we own the data (no parent-provided data)
        if (!externalData) {
            fetchData();
            const interval = setInterval(fetchData, 60000);
            return () => clearInterval(interval);
        }
    }, [serviceId, fetchData, externalData]);

    if (loading) {
        return (
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <Globe className="w-5 h-5 text-cyan-500" />
                    <h3 className="font-bold">Traffic Map</h3>
                </div>
                <Skeleton className="h-[400px] w-full rounded-lg" />
            </Card>
        );
    }

    if (!data || data.countries.length === 0) {
        return (
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <Globe className="w-5 h-5 text-cyan-500" />
                    <h3 className="font-bold">Traffic Map</h3>
                </div>
                <div className="h-[300px] flex items-center justify-center text-muted-foreground text-sm">
                    No traffic data yet. Traffic will appear as requests hit your service.
                </div>
            </Card>
        );
    }

    return (
        <Card className="p-6 border-border shadow-md">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                    <Globe className="w-5 h-5 text-cyan-500" />
                    <h3 className="font-bold">Traffic Map</h3>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{data.total_requests.toLocaleString()} requests</span>
                    <span>{data.unique_countries} countries</span>
                    {data.last_updated && (
                        <span title={data.last_updated}>
                            Updated {new Date(data.last_updated).toLocaleTimeString()}
                        </span>
                    )}
                    <button onClick={fetchData} className="p-1 hover:bg-muted rounded">
                        <RefreshCw className="w-3 h-3" />
                    </button>
                </div>
            </div>

            <MapComponent
                token={mapToken}
                countries={data.countries}
                totalRequests={data.total_requests}
            />

            <TrafficLegend countries={data.countries} />
        </Card>
    );
}

function TrafficLegend({ countries }: { countries: CountryTraffic[] }) {
    return (
        <div className="mt-4 grid grid-cols-2 md:grid-cols-5 gap-2">
            {countries.slice(0, 10).map(c => (
                <div key={c.code} className="flex items-center gap-2 text-xs p-2 rounded bg-muted/30">
                    <div className="w-2 h-2 rounded-full bg-cyan-500" />
                    <span className="font-medium">{c.code}</span>
                    <span className="text-muted-foreground">{c.percentage}%</span>
                </div>
            ))}
        </div>
    );
}
