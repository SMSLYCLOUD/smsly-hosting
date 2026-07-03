'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const TopologyCanvas = dynamic(
  () => import('../../../../_impl/TopologyCanvas-impl').then((m) => m.TopologyCanvas),
  { ssr: false, loading: () => <Skeleton className="h-[600px] w-full" /> }
);

export { TopologyCanvas };
