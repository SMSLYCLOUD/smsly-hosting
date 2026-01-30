"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import axios from "axios";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Github, Chrome, Loader2, Rocket, AlertCircle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";

export default function RegisterPage() {
    const router = useRouter();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");

    const handleRegister = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
            // Using standard dj-rest-auth or similar endpoint pattern
            const response = await axios.post(`${API_URL}/auth/registration/`, {
                username: name || email.split('@')[0], // Fallback if username required
                email,
                password
            });

            const token = response.data.key || response.data.token;
            if (token) {
                localStorage.setItem('auth_token', token);
                document.cookie = `auth_token=${token}; path=/; max-age=86400`;
                router.push('/');
            } else {
                 // Might require email verification step
                 router.push('/login?registered=true');
            }
        } catch (err: any) {
            console.error("Registration failed", err);
            setError(err.response?.data?.detail || "Registration failed. Please try again.");
        } finally {
            setLoading(false);
        }
    };

    const BACKEND_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace('/api/v1', '');

    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4 relative overflow-hidden">
            {/* Background decoration */}
            <div className="absolute top-0 left-0 w-full h-1/2 bg-gradient-to-b from-primary/5 to-transparent -z-10" />

            <Card className="w-full max-w-md shadow-lg border-border animate-fade-in">
                <CardHeader className="text-center space-y-2">
                    <div className="mx-auto w-12 h-12 bg-primary/10 rounded-xl flex items-center justify-center mb-4 ring-1 ring-primary/20">
                        <Rocket className="text-primary w-6 h-6" />
                    </div>
                    <CardTitle className="text-2xl font-bold tracking-tight">Create an account</CardTitle>
                    <CardDescription>
                        Get started with SMSly Hosting today
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                        <Button variant="outline" className="w-full" asChild>
                            <a href={`${BACKEND_URL}/accounts/github/login/`}>
                            <Github className="mr-2 h-4 w-4" />
                            GitHub
                            </a>
                        </Button>
                        <Button variant="outline" className="w-full" asChild>
                            <a href={`${BACKEND_URL}/accounts/google/login/`}>
                            <Chrome className="mr-2 h-4 w-4" />
                            Google
                            </a>
                        </Button>
                    </div>

                    <div className="relative my-4">
                        <div className="absolute inset-0 flex items-center">
                            <span className="w-full border-t" />
                        </div>
                        <div className="relative flex justify-center text-xs uppercase">
                            <span className="bg-background px-2 text-muted-foreground">
                                Or continue with email
                            </span>
                        </div>
                    </div>

                    <form onSubmit={handleRegister} className="space-y-4">
                        {error && (
                            <Alert variant="destructive">
                                <AlertCircle className="h-4 w-4" />
                                <AlertDescription>{error}</AlertDescription>
                            </Alert>
                        )}
                        <div className="space-y-2">
                            <Label htmlFor="name">Full Name</Label>
                            <Input
                                id="name"
                                placeholder="John Doe"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                required
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="email">Email</Label>
                            <Input
                                id="email"
                                type="email"
                                placeholder="name@example.com"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="password">Password</Label>
                            <Input
                                id="password"
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                            />
                        </div>
                        <Button type="submit" className="w-full bg-primary hover:bg-primary/90 text-primary-foreground" disabled={loading}>
                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {loading ? "Creating account..." : "Create account"}
                        </Button>
                    </form>

                </CardContent>
                <CardFooter className="flex justify-center text-sm text-muted-foreground">
                    Already have an account?&nbsp;
                    <Link href="/login" className="underline hover:text-primary transition-colors">
                        Sign in
                    </Link>
                </CardFooter>
            </Card>
        </div>
    );
}
