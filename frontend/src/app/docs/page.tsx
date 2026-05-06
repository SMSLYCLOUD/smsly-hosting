import Link from 'next/link';

export default function DocsPage() {
  return (
    <div className="container mx-auto py-12 max-w-4xl prose dark:prose-invert">
      <h1>Documentation</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 not-prose">
        <Link href="/docs/getting-started" className="p-6 border rounded-lg hover:bg-muted/50 transition block">
            <h3 className="text-xl font-bold mb-2">Getting Started</h3>
            <p className="text-muted-foreground">Deploy your first application in minutes.</p>
        </Link>
        <Link href="/docs/cli" className="p-6 border rounded-lg hover:bg-muted/50 transition block">
            <h3 className="text-xl font-bold mb-2">CLI Reference</h3>
            <p className="text-muted-foreground">Control Grid from your terminal.</p>
        </Link>
        <Link href="/docs/api" className="p-6 border rounded-lg hover:bg-muted/50 transition block">
            <h3 className="text-xl font-bold mb-2">API Reference</h3>
            <p className="text-muted-foreground">Automate everything with our REST API.</p>
        </Link>
        <Link href="/docs/addons" className="p-6 border rounded-lg hover:bg-muted/50 transition block">
            <h3 className="text-xl font-bold mb-2">Addons</h3>
            <p className="text-muted-foreground">Manage Databases, Redis, and more.</p>
        </Link>
      </div>
    </div>
  );
}
