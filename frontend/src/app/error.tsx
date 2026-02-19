'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { AlertTriangle } from 'lucide-react';

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
    <div className="flex flex-col items-center justify-center min-h-screen bg-background text-foreground p-4">
      <AlertTriangle className="w-16 h-16 text-destructive mb-4" />
      <h2 className="text-3xl font-bold mb-2">Something went wrong!</h2>
      <p className="text-muted-foreground mb-6 text-center max-w-md">
        We encountered an unexpected error. Please try again later.
      </p>
      <div className="flex gap-4">
        <Button onClick={() => reset()}>Try again</Button>
        <Button variant="outline" onClick={() => window.location.href = '/'}>
            Return Home
        </Button>
      </div>
    </div>
  );
}
