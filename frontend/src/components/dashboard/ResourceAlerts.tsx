'use client';

import React, { useState, useEffect, memo } from 'react';
import { AlertTriangle, XCircle, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ResourceAlert {
    id: string;
    severity: 'INFO' | 'WARNING' | 'CRITICAL';
    metric: string;
    threshold: number;
    current_value: number;
    message: string;
    created_at: string;
}

function getHeaders(): Record<string, string> {
    return {};
}

function apiUrl(path: string) {
    const base = typeof window !== 'undefined' ? `${window.location.origin}/api/v1` : '/api/v1';
    return `${base}${path}`;
}

export const ResourceAlerts = memo(function ResourceAlerts({ serviceId }: { serviceId: string }) {
    const [alerts, setAlerts] = useState<ResourceAlert[]>([]);

    useEffect(() => {
        const fetchAlerts = async () => {
            try {
                const res = await fetch(apiUrl(`/resource-alerts/?service=${serviceId}`), {
                    credentials: 'include',
                    headers: getHeaders(),
                });
                if (res.ok) {
                    const data = await res.json();
                    setAlerts(Array.isArray(data) ? data : data.results);
                }
            } catch (e) {
                console.error(e);
            }
        };
        fetchAlerts();
    }, [serviceId]);

    if (alerts.length === 0) return null;

    const handleDismiss = async (id: string) => {
        try {
            await fetch(apiUrl(`/resource-alerts/${id}/dismiss/`), {
                method: 'POST',
                credentials: 'include',
                headers: getHeaders(),
            });
            setAlerts(prev => prev.filter(a => a.id !== id));
        } catch (e) {
            console.error(e);
        }
    };

    return (
        <div className="space-y-2 mb-6">
            {alerts.map(alert => (
                <div key={alert.id} className={`flex items-center justify-between p-3 rounded-lg border ${
                    alert.severity === 'CRITICAL' ? 'bg-red-500/10 border-red-500/20 text-red-400' :
                    alert.severity === 'WARNING' ? 'bg-yellow-500/10 border-yellow-500/20 text-yellow-400' :
                    'bg-blue-500/10 border-blue-500/20 text-blue-400'
                }`}>
                    <div className="flex items-center gap-3">
                        <AlertTriangle size={16} />
                        <span className="text-sm font-medium">{alert.message}</span>
                    </div>
                    <button onClick={() => handleDismiss(alert.id)} className="text-sm opacity-70 hover:opacity-100">Dismiss</button>
                </div>
            ))}
        </div>
    );
});
