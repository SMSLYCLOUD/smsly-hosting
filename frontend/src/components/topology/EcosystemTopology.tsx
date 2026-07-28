'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const EcosystemTopology = dynamic(
  () => import('./EcosystemTopology-impl').then((m) => m.EcosystemTopology),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { EcosystemTopology };
