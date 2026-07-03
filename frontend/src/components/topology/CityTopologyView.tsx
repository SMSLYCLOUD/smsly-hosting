'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const CityTopologyView = dynamic(
  () => import('../../../_impl/CityTopologyView-impl').then((m) => m.CityTopologyView),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { CityTopologyView };
