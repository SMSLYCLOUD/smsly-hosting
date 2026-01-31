"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Github, Chrome } from "lucide-react";

export default function LoginPage() {
  // Use backend base URL (without /api/v1) for OAuth endpoints
  const BACKEND_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace('/api/v1', '');

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900 p-4">
      <Card className="w-full max-w-md shadow-xl border-slate-200 dark:border-slate-800">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto w-12 h-12 bg-indigo-600 rounded-xl flex items-center justify-center mb-4">
            <span className="text-white font-bold text-xl">S</span>
          </div>
          <CardTitle className="text-2xl font-bold tracking-tight">Welcome back</CardTitle>
          <CardDescription>
            Sign in to SMSLY Hosting to manage your infrastructure.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button variant="outline" className="w-full h-11 relative" asChild>
            <a href={`${BACKEND_URL}/accounts/github/login/`}>
              <Github className="mr-2 h-4 w-4" />
              Sign in with GitHub
            </a>
          </Button>
          <Button variant="outline" className="w-full h-11 relative" asChild>
            <a href={`${BACKEND_URL}/accounts/google/login/`}>
              <Chrome className="mr-2 h-4 w-4" />
              Sign in with Google
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

          {/* Email/Pass form could go here, focusing on Social for now per request */}
          <Button className="w-full h-11 bg-indigo-600 hover:bg-indigo-700 text-white">
            Sign in with Email
          </Button>

        </CardContent>
        <CardFooter className="flex justify-center text-sm text-muted-foreground">
          Don&apos;t have an account?&nbsp;
          <Link href="/register" className="underline hover:text-primary">
            Sign up
          </Link>
        </CardFooter>
      </Card>
    </div>
  );
}
