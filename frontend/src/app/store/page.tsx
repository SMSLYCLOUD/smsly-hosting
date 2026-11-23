import type { Metadata } from 'next';
import { StoreCatalogClient } from '@/components/store/StoreCatalogClient';

export const metadata: Metadata = {
  title: 'Templates Catalog',
  description:
    'Browse one-click self-hosted application templates, AI stacks, databases, and developer tools ready to deploy on Trulay Grid.',
  alternates: { canonical: '/store' },
};

export default function AppStorePage() {
  return <StoreCatalogClient />;
}
