"use client";

import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Github, Chrome } from "lucide-react";

export default function RegisterPage() {
    // Use backend base URL (without /api/v1) for OAuth endpoints
    const BACKEND_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace('/api/v1', '');

    return (
        <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900 p-4">
            <Card className="w-full max-w-md shadow-xl border-slate-200 dark:border-slate-800">
                <CardHeader className="text-center space-y-2">
                    <div className="mx-auto mb-4">
                        <Image src="/images/logo.png" alt="SMSLY" width={120} height={40} className="h-10 w-auto mx-auto" />
                    </div>
                    <CardTitle className="text-2xl font-bold tracking-tight">Create your account</CardTitle>
                    <CardDescription>
                        Sign up for SMSLY Hosting to deploy your applications.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <Button variant="outline" className="w-full h-11 relative" asChild>
                        <a href={`${BACKEND_URL}/accounts/github/login/`}>
                            <Github className="mr-2 h-4 w-4" />
                            Sign up with GitHub
                        </a>
                    </Button>
                    <Button variant="outline" className="w-full h-11 relative" asChild>
                        <a href={`${BACKEND_URL}/accounts/google/login/`}>
                            <Chrome className="mr-2 h-4 w-4" />
                            Sign up with Google
                        </a>
                    </Button>

                    <div className="relative">
                        <div className="absolute inset-0 flex items-center">
                            <span className="w-full border-t" />
                        </div>
                        <div className="relative flex justify-center text-xs uppercase">
                            <span className="bg-background px-2 text-muted-foreground">
                                Or continue with email
                            </span>
                        </div>
                    </div>

                    <Button className="w-full h-11 bg-emerald-600 hover:bg-emerald-700 text-white" asChild>
                        <a href={`${BACKEND_URL}/accounts/signup/`}>
                            Sign up with Email
                        </a>
                    </Button>

                </CardContent>
                <CardFooter className="flex justify-center text-sm text-muted-foreground">
                    Already have an account?&nbsp;
                    <Link href="/login" className="underline hover:text-primary">
                        Sign in
                    </Link>
                </CardFooter>
            </Card>
        </div>
    );
}
