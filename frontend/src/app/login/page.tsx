'use client';

import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Github, Chrome, Mail, Lock, Loader2 } from 'lucide-react';
import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import axios from 'axios';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSocialLogin = (provider: string) => {
    // Redirect to backend auth endpoint
    window.location.href = `${process.env.NEXT_PUBLIC_API_URL?.replace('/api/v1', '')}/accounts/${provider}/login/`;
  };

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      // Assuming backend uses dj-rest-auth standard login endpoint
      // Adjust URL if prefix is different, e.g. /api/v1/auth/login/
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

      const response = await axios.post(`${API_URL}/auth/login/`, {
        username: email, // dj-rest-auth often uses 'username' or 'email' depending on config. Trying username=email first.
        email: email,
        password: password
      });

      if (response.data.key || response.data.access) {
        const token = response.data.key || response.data.access;
        // Store token for API calls
        localStorage.setItem('auth_token', token);
        // Set cookie for middleware redirection checks
        document.cookie = `auth_token=${token}; path=/; max-age=604800; SameSite=Lax`;
        router.push('/');
      }
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.non_field_errors?.[0] || 'Failed to login with provided credentials.');
    } finally {
      setLoading(false);
    }
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

            {/* Email Login Form */}
            <form onSubmit={handleEmailLogin} className="space-y-4 mb-6">
                {error && (
                    <div className="p-3 text-sm text-red-500 bg-red-500/10 border border-red-500/20 rounded-md">
                        {error}
                    </div>
                )}
                <div className="space-y-2">
                    <div className="relative">
                        <Mail className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                        <input
                            type="text"
                            placeholder="Email or Username"
                            className="w-full pl-9 pr-4 py-2 bg-background border border-input rounded-md focus:outline-none focus:ring-2 focus:ring-ring"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            required
                        />
                    </div>
                </div>
                <div className="space-y-2">
                    <div className="relative">
                        <Lock className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                        <input
                            type="password"
                            placeholder="Password"
                            className="w-full pl-9 pr-4 py-2 bg-background border border-input rounded-md focus:outline-none focus:ring-2 focus:ring-ring"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            required
                        />
                    </div>
                </div>
                <Button
                    type="submit"
                    className="w-full bg-primary hover:bg-primary/90"
                    disabled={loading}
                >
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : 'Sign In'}
                </Button>
            </form>

            <div className="relative mb-6">
                <div className="absolute inset-0 flex items-center">
                    <span className="w-full border-t border-muted" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                    <span className="bg-background px-2 text-muted-foreground">Or continue with</span>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <Button
                    variant="outline"
                    className="w-full relative overflow-hidden group hover:border-foreground/50 transition-all"
                    onClick={() => handleSocialLogin('github')}
                >
                    <Github className="mr-2 h-4 w-4" />
                    <span className="ml-2">GitHub</span>
                </Button>
                <Button
                    variant="outline"
                    className="w-full relative overflow-hidden group hover:border-blue-500/50 transition-all"
                    onClick={() => handleSocialLogin('google')}
                >
                    <Chrome className="mr-2 h-4 w-4 text-blue-500" />
                    <span className="ml-2">Google</span>
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
