'use client';

import Link from 'next/link';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { ArrowRight, Layout, PlusCircle } from 'lucide-react';

export default function Home() {
  return (
    <main className="min-h-screen bg-background flex flex-col">
      <Navbar />

      <div className="flex-1 flex flex-col items-center justify-center p-6 lg:p-24 text-center">
        <div className="max-w-3xl space-y-8">
            <h1 className="text-4xl md:text-6xl font-bold tracking-tight text-foreground">
                Manage Your <span className="text-primary">Deployments</span>
            </h1>
            <p className="text-xl text-muted-foreground max-w-2xl mx-auto">
                Monitor services, view logs, and deploy new applications from a central command center.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-2xl mx-auto text-left">
                <Link href="/services" className="group">
                    <div className="border border-border bg-card hover:bg-muted/50 p-6 rounded-xl transition-all shadow-sm hover:shadow-md h-full">
                        <div className="flex items-center justify-between mb-4">
                            <div className="p-3 bg-primary/10 rounded-lg text-primary">
                                <Layout size={24} />
                            </div>
                            <ArrowRight className="text-muted-foreground group-hover:translate-x-1 transition-transform" />
                        </div>
                        <h2 className="text-2xl font-bold mb-2">Services</h2>
                        <p className="text-muted-foreground">View active services, check health status, and manage configurations.</p>
                    </div>
                </Link>

                <Link href="/new" className="group">
                    <div className="border border-border bg-card hover:bg-muted/50 p-6 rounded-xl transition-all shadow-sm hover:shadow-md h-full">
                        <div className="flex items-center justify-between mb-4">
                            <div className="p-3 bg-emerald-500/10 rounded-lg text-emerald-500">
                                <PlusCircle size={24} />
                            </div>
                            <ArrowRight className="text-muted-foreground group-hover:translate-x-1 transition-transform" />
                        </div>
                        <h2 className="text-2xl font-bold mb-2">Deploy New</h2>
                        <p className="text-muted-foreground">Deploy a new application from GitHub or choose a template.</p>
                    </div>
                </Link>
            </div>
        </div>
      </div>
    </main>
  );
}
