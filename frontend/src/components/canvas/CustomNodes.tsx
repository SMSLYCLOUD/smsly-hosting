'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const ServiceNode = dynamic(
  () => import('../../../_impl/CustomNodes-impl').then((m) => m.ServiceNode),
  { ssr: false, loading: () => <Skeleton className="h-32 w-full" /> }
);

const DatabaseNode = dynamic(
  () => import('../../../_impl/CustomNodes-impl').then((m) => m.DatabaseNode),
  { ssr: false, loading: () => <Skeleton className="h-32 w-full" /> }
);

const RedisNode = dynamic(
  () => import('../../../_impl/CustomNodes-impl').then((m) => m.RedisNode),
  { ssr: false, loading: () => <Skeleton className="h-32 w-full" /> }
);

export { ServiceNode, DatabaseNode, RedisNode };
