"use client";

import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Github, Chrome } from "lucide-react";


export default function RegisterPage() {
    // Use dynamic origin detection — same approach as login page
    const BACKEND_URL = typeof window !== 'undefined'
        ? window.location.origin
        : process.env.NEXT_PUBLIC_API_URL || 'https://cloud.smsly.cloud';

    return (
        <div className="min-h-screen flex flex-col relative overflow-x-hidden">

            <div className="flex-1 flex items-center justify-center p-4 relative z-10">

            <Card className="w-full max-w-md card-premium rounded-2xl">
                <CardHeader className="text-center space-y-2">
                    <div className="mx-auto mb-4 flex flex-col items-center gap-2">
                        <Image src="/images/logo.svg" alt="CloudNeuron" width={48} height={48} className="h-12 w-12 mx-auto rounded-xl shadow-md" />
                        <span className="font-bold text-xl tracking-tight text-slate-900 dark:text-white">CloudNeuron</span>
                    </div>
                    <CardTitle className="text-2xl font-bold tracking-tight">Create your account</CardTitle>
                    <CardDescription>
                        Sign up for CloudNeuron to deploy your applications.
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

                    <Button className="w-full h-11 btn-shimmer bg-gradient-to-r from-emerald-600 to-green-600 hover:from-emerald-700 hover:to-green-700 text-white font-semibold shadow-lg shadow-emerald-500/20" asChild>
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
        </div>
    );
}
