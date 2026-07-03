'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const Topology3D = dynamic(
  () => import('../../../_impl/Topology3D-impl').then((m) => m.Topology3D),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { Topology3D };
