'use client';

import { use } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { TopologyView } from '@/components/topology/TopologyView';

export default function TopologyPage(props: {
    searchParams: Promise<{ service?: string }>;
}) {
    const searchParams = use(props.searchParams);
    return (
        <DashboardShell>
            <TopologyView searchParams={searchParams} embed={false} />
        </DashboardShell>
    );
}
