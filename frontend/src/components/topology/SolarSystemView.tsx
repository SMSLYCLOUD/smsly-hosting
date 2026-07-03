'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const SolarSystemView = dynamic(
  () => import('../../../_impl/SolarSystemView-impl').then((m) => m.SolarSystemView),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { SolarSystemView };
