'use client';

import React from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Github, Chrome } from 'lucide-react';
import { motion } from 'framer-motion';

export default function LoginPage() {
  const handleLogin = (provider: string) => {
    // Redirect to backend auth endpoint
    window.location.href = `${process.env.NEXT_PUBLIC_API_URL?.replace('/api/v1', '')}/accounts/${provider}/login/`;
  };

  return (
    <main className="min-h-screen bg-background flex items-center justify-center p-6 relative overflow-hidden">
      {/* Animated Background */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-emerald-500/20 via-background to-background" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_left,_var(--tw-gradient-stops))] from-blue-500/20 via-background to-background" />

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-md z-10"
      >
        <Card className="p-8 shadow-2xl border-primary/20 bg-card/50 backdrop-blur-xl">
            <div className="text-center mb-8">
                <div className="inline-block p-3 rounded-2xl bg-gradient-to-br from-emerald-400 to-cyan-500 text-white shadow-lg mb-4">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-8 h-8">
                        <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                        <path d="M2 17L12 22L22 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                        <path d="M2 12L12 17L22 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                </div>
                <h1 className="text-3xl font-bold tracking-tight mb-2">Welcome Back</h1>
                <p className="text-muted-foreground">Sign in to your SMSly Hosting console</p>
            </div>

            <div className="space-y-4">
                <Button
                    size="lg"
                    variant="outline"
                    className="w-full relative overflow-hidden group hover:border-foreground/50 transition-all"
                    onClick={() => handleLogin('github')}
                >
                    <Github className="mr-2 h-5 w-5" />
                    Continue with GitHub
                    <div className="absolute inset-0 bg-foreground/5 opacity-0 group-hover:opacity-100 transition-opacity" />
                </Button>
                <Button
                    size="lg"
                    variant="outline"
                    className="w-full relative overflow-hidden group hover:border-blue-500/50 transition-all"
                    onClick={() => handleLogin('google')}
                >
                    <Chrome className="mr-2 h-5 w-5 text-blue-500" />
                    Continue with Google
                    <div className="absolute inset-0 bg-blue-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />
                </Button>
            </div>

            <p className="mt-8 text-center text-xs text-muted-foreground">
                By continuing, you agree to our <a href="#" className="underline hover:text-foreground">Terms of Service</a> and <a href="#" className="underline hover:text-foreground">Privacy Policy</a>.
            </p>
        </Card>
      </motion.div>
    </main>
  );
}
