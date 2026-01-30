'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log the error to an analytics service
    console.error('Application Error:', error);
  }, [error]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="max-w-md w-full p-8 text-center shadow-lg border-destructive/20">
        <div className="mx-auto w-16 h-16 bg-destructive/10 rounded-full flex items-center justify-center mb-6">
          <AlertTriangle className="w-8 h-8 text-destructive" />
        </div>
        <h1 className="text-2xl font-bold text-foreground mb-2">Something went wrong!</h1>
        <p className="text-sm text-muted-foreground mb-6">
          An unexpected error occurred. Our team has been notified.
        </p>
        <div className="bg-muted/50 p-3 rounded-md mb-6 text-xs font-mono text-left overflow-auto max-h-32">
            {error.message || "Unknown error"}
            {error.digest && <div className="mt-1 text-muted-foreground">Digest: {error.digest}</div>}
        </div>
        <div className="flex gap-4">
            <Button variant="outline" className="flex-1" onClick={() => window.location.href = '/'}>
                Go Home
            </Button>
            <Button className="flex-1 bg-primary hover:bg-primary/90" onClick={reset}>
                <RefreshCw className="mr-2 h-4 w-4" /> Try Again
            </Button>
        </div>
      </Card>
    </div>
  );
}
