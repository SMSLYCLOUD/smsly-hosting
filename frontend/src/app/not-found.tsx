'use client';

import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { FileQuestion } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background text-foreground p-4">
      <FileQuestion className="w-16 h-16 text-muted-foreground mb-4" />
      <h2 className="text-3xl font-bold mb-2">Page Not Found</h2>
      <p className="text-muted-foreground mb-6">Could not find requested resource</p>
      <Link href="/">
        <Button>Return Home</Button>
      </Link>
    </div>
  );
}
