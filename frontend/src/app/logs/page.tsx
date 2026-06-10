'use client';

import { use } from 'react';
import { LogsView } from '@/components/logs/LogsView';

export default function LogsPage(props: {
    searchParams: Promise<{ service?: string; query?: string }>;
}) {
    const searchParams = use(props.searchParams);
    return <LogsView searchParams={searchParams} embed={false} />;
}
