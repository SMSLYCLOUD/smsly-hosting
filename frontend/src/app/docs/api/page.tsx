'use client';

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

export default function ApiRefPage() {
  return (
    <div className="container mx-auto py-12 max-w-5xl">
      <h1 className="text-3xl font-bold mb-6">API Reference</h1>
      <p className="mb-8 text-muted-foreground">
        Our API is fully documented using OpenAPI (Swagger).
        You can explore the schema below or view the raw JSON.
      </p>

      <div className="border rounded-lg p-8 text-center bg-muted/20">
        <p>Interactive API docs coming soon.</p>
        <p className="text-sm text-muted-foreground mt-2">
            Base URL: <code>https://api.cloud.smsly.cloud/api/v1</code>
        </p>
      </div>
    </div>
  );
}
