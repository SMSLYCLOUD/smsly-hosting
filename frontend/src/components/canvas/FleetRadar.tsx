'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const FleetRadar = dynamic(
  () => import('../../../_impl/FleetRadar-impl').then((m) => m.FleetRadar),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { FleetRadar };
