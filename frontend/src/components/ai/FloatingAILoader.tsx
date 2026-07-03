'use client';

/**
 * Client-only wrapper around the FloatingAI chat widget.
 *
 * Next.js 15 App Router forbids ``ssr: false`` from Server Components
 * (see https://nextjs.org/docs/messages/no-ssg-only). ``app/layout.tsx``
 * is a Server Component — it sets ``viewport``/``metadata`` exports and
 * therefore cannot host ``next/dynamic({ ssr: false })`` directly.
 *
 * This module is a Client Component (the ``'use client'`` directive at
 * the top of the file), so it is allowed to use ``ssr: false``. It
 * re-exports the lazy-loaded FloatingAI as a default React component
 * that the Server-Component layout can render inside its existing
 * ``<LazyMount>`` boundary. The original behavior is preserved: the
 * chat widget still ships in a deferred chunk that only loads after
 * ``requestIdleCallback`` (or first user interaction) via ``LazyMount``.
 */
import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';

const FloatingAILoader = dynamic(
  () => import('./FloatingAI').then((m) => m.FloatingAI),
  {
    ssr: false,
    loading: () => (
      <Skeleton className="h-14 w-14 rounded-full fixed bottom-4 right-4 z-50" />
    ),
  }
);

export default FloatingAILoader;
