'use client';

import { useEffect } from 'react';
import PlatformNotice from '@/components/public/PlatformNotice';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <PlatformNotice
      badge="Runtime Notice"
      title="Request could not be completed"
      message="An unexpected platform error occurred while loading this page."
      secondaryMessage="No internal diagnostic details are exposed on this screen."
      showRetry
      onRetry={() => reset()}
    />
  );
}
