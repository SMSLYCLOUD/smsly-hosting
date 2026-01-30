'use client';

import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { AlertCircle, Home } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="max-w-md w-full p-8 text-center shadow-lg border-primary/20">
        <div className="mx-auto w-16 h-16 bg-muted rounded-full flex items-center justify-center mb-6">
          <AlertCircle className="w-8 h-8 text-muted-foreground" />
        </div>
        <h1 className="text-4xl font-bold text-foreground mb-2">404</h1>
        <h2 className="text-xl font-semibold text-muted-foreground mb-6">Page Not Found</h2>
        <p className="text-sm text-muted-foreground mb-8">
          The resource you are looking for might have been removed, had its name changed, or is temporarily unavailable.
        </p>
        <Link href="/">
          <Button className="w-full bg-primary hover:bg-primary/90 text-primary-foreground gap-2">
            <Home size={16} /> Return Home
          </Button>
        </Link>
      </Card>
    </div>
  );
}
