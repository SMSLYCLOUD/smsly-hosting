import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'System Status',
  description:
    'Live platform health status, service availability, and infrastructure diagnostics for Trulay Grid.',
  alternates: { canonical: '/status' },
};

export default function StatusLayout({ children }: { children: React.ReactNode }) {
  return children;
}
