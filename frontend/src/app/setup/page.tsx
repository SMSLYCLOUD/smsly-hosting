'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import axios from 'axios';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Rocket, ShieldCheck, Clock, AlertTriangle } from 'lucide-react';

export default function SetupPage() {
    const router = useRouter();
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [timeLeft, setTimeLeft] = useState<number | null>(null);
    const [error, setError] = useState("");

    // Form State
    const [email, setEmail] = useState("admin@smsly.io");
    const [password, setPassword] = useState("");
    const [envVars, setEnvVars] = useState({
        SMTP_HOST: "",
        SMTP_PORT: "587",
        OPENAI_API_KEY: ""
    });

    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

    useEffect(() => {
        checkStatus();
    }, []);

    const checkStatus = async () => {
        try {
            const res = await axios.get(`${API_URL}/deployments/setup/status/`);
            if (res.data.is_setup) {
                // Already setup, redirect to login
                router.push('/login');
            } else {
                setTimeLeft(res.data.time_remaining);
                setLoading(false);
            }
        } catch (e) {
            console.error(e);
            setError("Failed to connect to backend. Ensure server is running.");
            setLoading(false);
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        setError("");

        try {
            await axios.post(`${API_URL}/deployments/setup/init/`, {
                email,
                password,
                env_vars: envVars
            });
            // Success
            router.push('/login?setup=success');
        } catch (err: any) {
            setError(err.response?.data?.error || "Setup failed.");
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) {
        return <div className="min-h-screen flex items-center justify-center bg-background">Loading setup...</div>;
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4">
            <div className="absolute top-0 left-0 w-full h-1/2 bg-gradient-to-b from-primary/10 to-transparent -z-10" />

            <Card className="w-full max-w-2xl shadow-xl border-primary/20">
                <CardHeader className="text-center border-b border-border/50 pb-6 bg-muted/20">
                    <div className="mx-auto w-16 h-16 bg-primary/20 rounded-full flex items-center justify-center mb-4">
                        <Rocket className="w-8 h-8 text-primary" />
                    </div>
                    <CardTitle className="text-3xl font-bold">Welcome to SMSly Hosting</CardTitle>
                    <CardDescription className="text-lg mt-2">
                        Complete your initial configuration to secure the platform.
                    </CardDescription>

                    {timeLeft !== null && (
                        <div className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-full bg-orange-500/10 text-orange-600 border border-orange-500/20">
                            <Clock className="w-4 h-4" />
                            <span className="font-mono font-bold">
                                {Math.floor(timeLeft / 60)}:{(timeLeft % 60).toString().padStart(2, '0')}
                            </span>
                            <span className="text-sm">remaining to complete setup</span>
                        </div>
                    )}
                </CardHeader>

                <CardContent className="p-8 space-y-8">
                    {error && (
                        <Alert variant="destructive">
                            <AlertTriangle className="h-4 w-4" />
                            <AlertTitle>Error</AlertTitle>
                            <AlertDescription>{error}</AlertDescription>
                        </Alert>
                    )}

                    <form onSubmit={handleSubmit} className="space-y-8">
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 pb-2 border-b border-border">
                                <ShieldCheck className="w-5 h-5 text-primary" />
                                <h3 className="font-semibold text-lg">Admin Credentials</h3>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Admin Email</Label>
                                    <Input
                                        value={email}
                                        onChange={e => setEmail(e.target.value)}
                                        required
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>New Password</Label>
                                    <Input
                                        type="password"
                                        value={password}
                                        onChange={e => setPassword(e.target.value)}
                                        placeholder="Min 8 characters"
                                        required
                                        minLength={8}
                                    />
                                </div>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="flex items-center gap-2 pb-2 border-b border-border">
                                <SettingsIcon className="w-5 h-5 text-primary" />
                                <h3 className="font-semibold text-lg">Environment Configuration</h3>
                            </div>
                            <div className="grid grid-cols-1 gap-4">
                                <div className="space-y-2">
                                    <Label>SMTP Host (Optional)</Label>
                                    <Input
                                        placeholder="smtp.gmail.com"
                                        value={envVars.SMTP_HOST}
                                        onChange={e => setEnvVars({...envVars, SMTP_HOST: e.target.value})}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>OpenAI API Key (Optional)</Label>
                                    <Input
                                        type="password"
                                        placeholder="sk-..."
                                        value={envVars.OPENAI_API_KEY}
                                        onChange={e => setEnvVars({...envVars, OPENAI_API_KEY: e.target.value})}
                                    />
                                </div>
                            </div>
                        </div>

                        <Button type="submit" size="lg" className="w-full text-lg font-bold h-12" disabled={submitting}>
                            {submitting ? 'Configuring Platform...' : 'Complete Setup & Login'}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}

function SettingsIcon({ className }: { className?: string }) {
    return (
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
            <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.1a2 2 0 0 1-1-1.72v-.51a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
            <circle cx="12" cy="12" r="3" />
        </svg>
    );
}
