import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Get Started with AI Onboarding',
  description:
    'Analyze your Git repository or ask architecture questions with the Trulay Grid deployment assistant.',
  alternates: { canonical: '/get-started' },
};

export default function GetStartedLayout({ children }: { children: React.ReactNode }) {
  return children;
}
