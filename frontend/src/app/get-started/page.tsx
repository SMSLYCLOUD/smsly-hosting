'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Bot, User, Loader2 } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import ReactMarkdown from 'react-markdown';
import axios from 'axios';

interface Message {
    role: 'AI' | 'USER';
    content: string;
}

export default function LivingOnboarding() {
    const router = useRouter();
    const [messages, setMessages] = useState<Message[]>([
        { role: 'AI', content: "Hello! I am your AI Deployment Engineer. Paste a GitHub repository URL to analyze it, or ask me anything about infrastructure." }
    ]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        scrollRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleSend = async () => {
        if (!input.trim()) return;

        const userMsg = input;
        setInput('');
        setMessages(prev => [...prev, { role: 'USER', content: userMsg }]);
        setLoading(true);

        try {
            const res = await axios.post(process.env.NEXT_PUBLIC_API_URL + '/ai/chat/', { message: userMsg });
            setMessages(prev => [...prev, { role: 'AI', content: res.data.text }]);
        } catch (e) {
            setMessages(prev => [...prev, { role: 'AI', content: "Sorry, I encountered an error processing that request." }]);
        } finally {
            setLoading(false);
        }
    };

    return (
        <main className="min-h-screen bg-background font-sans flex flex-col">
            <Navbar />

            <div className="flex-1 flex flex-col items-center justify-center p-6 relative overflow-hidden bg-dot-pattern">

                <div className="max-w-3xl w-full z-10 space-y-8">

                    {loading && (
                        <div className="text-center animate-in fade-in zoom-in duration-500">
                            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-100 text-emerald-800 text-xs font-bold uppercase tracking-wider mb-6 border border-emerald-200">
                                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                                Powered by Jules AI
                            </div>
                            <Card className="p-4 bg-card flex items-center gap-2 text-muted-foreground">
                                <Loader2 size={16} className="animate-spin" /> Thinking...
                            </Card>
                        </div>
                    )}
                    <div ref={scrollRef} />
                </div>

                <div className="relative">
                    <Input
                        placeholder="Paste a repo URL or ask a question..."
                        className="h-14 pl-6 pr-14 text-lg rounded-full shadow-xl border-primary/20 focus-visible:ring-primary"
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleSend()}
                        autoFocus
                    />
                    <Button
                        size="icon"
                        className="absolute right-2 top-2 rounded-full h-10 w-10 bg-primary hover:bg-primary/90"
                        onClick={handleSend}
                        disabled={loading}
                    >
                        <ArrowRight size={20} />
                    </Button>
                </div>
            </div>
        </main>
    );
}
