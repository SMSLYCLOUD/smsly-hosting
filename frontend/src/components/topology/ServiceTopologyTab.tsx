'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const ServiceTopologyTab = dynamic(
  () => import('../../../_impl/ServiceTopologyTab-impl').then((m) => m.ServiceTopologyTab),
  { ssr: false, loading: () => <Skeleton className="h-[500px] w-full" /> }
);

export { ServiceTopologyTab };
