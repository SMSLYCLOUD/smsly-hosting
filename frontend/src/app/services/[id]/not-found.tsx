import Link from 'next/link';
import { SearchX, ArrowLeft, Rocket } from 'lucide-react';

export default function ServiceNotFound() {
    return (
        <div className="h-screen flex items-center justify-center bg-background">
            <div className="max-w-md w-full px-6 text-center">
                <div className="flex justify-center mb-6">
                    <div className="h-16 w-16 rounded-2xl bg-muted flex items-center justify-center">
                        <SearchX className="h-8 w-8 text-muted-foreground" />
                    </div>
                </div>
                <h1 className="text-xl font-semibold mb-2">Service not found</h1>
                <p className="text-sm text-muted-foreground mb-8">
                    This service does not exist or you don&apos;t have access to it.
                    It may have been deleted or the link is incorrect.
                </p>
                <div className="flex items-center justify-center gap-3">
                    <Link
                        href="/services"
                        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
                    >
                        <ArrowLeft className="h-4 w-4" />
                        Back to Services
                    </Link>
                    <Link
                        href="/new"
                        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md border border-border hover:bg-muted"
                    >
                        <Rocket className="h-4 w-4" />
                        Deploy New
                    </Link>
                </div>
            </div>
        </div>
    );
}
