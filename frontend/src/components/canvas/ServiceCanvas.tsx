'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const ServiceCanvas = dynamic(
  () => import('../../../_impl/ServiceCanvas-impl').then((m) => m.ServiceCanvas),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> }
);

export { ServiceCanvas };
