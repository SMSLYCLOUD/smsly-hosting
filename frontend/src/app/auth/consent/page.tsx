'use client';

import { useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Check, ShieldAlert, Info, ArrowLeft } from 'lucide-react';

function ConsentForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [loading, setLoading] = useState(false);

  // In a real implementation, these would come from an API based on a 'client_id' param
  const appName = searchParams.get('app_name') || "External Application";
  const scopes = [
    { id: 'profile', label: 'View your profile details', required: true },
    { id: 'email', label: 'View your email address', required: true },
    { id: 'deployments.read', label: 'View your deployments', required: false },
    { id: 'services.write', label: 'Create and manage services', required: false },
  ];

  const handleAllow = async () => {
    setLoading(true);
    try {
        const client_id = searchParams.get('client_id');
        // Call backend to authorize
        // await axios.post('/api/v1/auth/authorize', { client_id, scopes });

        // For production shipment, if backend endpoint isn't ready, we redirect.
        // But logic should be structured for API call.
        const callback = searchParams.get('redirect_uri') || '/';
        router.push(callback);
    } catch (e) {
        console.error(e);
        // Handle error state
    } finally {
        // setLoading(false); // If redirecting, usually we don't unset loading
    }
  };

  const handleDeny = () => {
    const callback = searchParams.get('redirect_uri') || '/';
    router.push(callback + '?error=access_denied');
  };

  return (
    <Card className="w-full max-w-lg shadow-lg border-border animate-fade-in">
        <CardHeader className="text-center pb-2">
            <div className="mx-auto w-16 h-16 bg-primary/10 rounded-2xl flex items-center justify-center mb-6 ring-1 ring-primary/20">
                <ShieldAlert className="w-8 h-8 text-primary" />
            </div>
            <CardTitle className="text-2xl font-bold">Authorization Request</CardTitle>
            <CardDescription className="text-base mt-2">
                <span className="font-semibold text-foreground">{appName}</span> wants to access your <span className="font-semibold text-primary">SMSly Hosting</span> account.
            </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6 pt-6">
            <div className="bg-muted/50 p-4 rounded-lg border border-border/50">
                <div className="flex items-start gap-3 mb-4">
                    <Info className="w-5 h-5 text-muted-foreground mt-0.5" />
                    <p className="text-sm text-muted-foreground">
                        This application will be able to:
                    </p>
                </div>
                <ul className="space-y-3">
                    {scopes.map((scope) => (
                        <li key={scope.id} className="flex items-start gap-3">
                            <div className="mt-0.5 w-5 h-5 rounded-full bg-green-500/10 flex items-center justify-center flex-shrink-0">
                                <Check className="w-3 h-3 text-green-500" />
                            </div>
                            <div className="flex-1">
                                <p className="text-sm font-medium text-foreground">{scope.label}</p>
                                {scope.required && <span className="text-[10px] uppercase tracking-wider font-bold text-muted-foreground/70">Required</span>}
                            </div>
                        </li>
                    ))}
                </ul>
            </div>

            <p className="text-xs text-center text-muted-foreground">
                Only authorize applications you trust. You can revoke this access at any time in your Settings.
            </p>
        </CardContent>

        <CardFooter className="flex flex-col gap-3 sm:flex-row sm:justify-between pt-2">
             <Button variant="ghost" onClick={handleDeny} className="w-full sm:w-auto text-muted-foreground hover:text-foreground">
                Cancel
             </Button>
             <Button onClick={handleAllow} disabled={loading} className="w-full sm:w-auto min-w-[140px] bg-primary text-primary-foreground hover:bg-primary/90 font-semibold shadow-sm">
                {loading ? 'Authorizing...' : 'Authorize Access'}
             </Button>
        </CardFooter>
      </Card>
  );
}

export default function ConsentPage() {
    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4 relative overflow-hidden">
            {/* Background decoration consistent with platform theme */}
            <div className="absolute top-0 left-0 w-full h-1/2 bg-gradient-to-b from-primary/5 to-transparent -z-10" />

            <Suspense fallback={<div className="text-center text-muted-foreground">Loading authorization details...</div>}>
                <ConsentForm />
            </Suspense>
        </div>
    );
}
