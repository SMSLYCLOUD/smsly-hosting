'use client';

import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const ServiceNode = dynamic(
  () => import('../../../../_impl/ServiceNode-impl').then((m) => m.ServiceNode),
  { ssr: false, loading: () => <Skeleton className="h-32 w-full" /> }
);

export { ServiceNode };
