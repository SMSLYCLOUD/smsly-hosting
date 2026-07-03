'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const TrafficFlowAnimation = dynamic(
  () => import('../../../_impl/TrafficFlowAnimation-impl').then((m) => m.TrafficFlowAnimation),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { TrafficFlowAnimation };
