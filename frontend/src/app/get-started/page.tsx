'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { ArrowRight, Bot, User, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import ReactMarkdown from 'react-markdown';

interface Message {
  role: 'AI' | 'USER';
  content: string;
}

export default function LivingOnboarding() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'AI',
      content:
        'Hello! I am your AI Deployment Engineer. Paste a GitHub repository URL to analyze it, or ask anything about self-hosting on Grid.',
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    setInput('');
    setMessages((prev) => [...prev, { role: 'USER', content: trimmed }]);
    setLoading(true);

    try {
      const res = await fetch('/api/v1/ai-chat/', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed }),
      });

      if (res.status === 401 || res.status === 403) {
        setMessages((prev) => [
          ...prev,
          {
            role: 'AI',
            content:
              'Sign in at [Login](/login) to analyze repositories with the interactive AI deployment engineer, or follow the [Installation Guide](/docs/install).',
          },
        ]);
        return;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        { role: 'AI', content: data?.text || 'Analysis complete.' },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: 'AI',
          content:
            'Sorry, the onboarding AI endpoint is currently unavailable. Check [Getting Started](/docs/getting-started) or try again shortly.',
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center px-4 py-10">
      <div className="w-full max-w-3xl mb-6 text-center">
        <h1 className="text-3xl font-extrabold tracking-tight text-foreground mb-2">
          AI Deployment Onboarding
        </h1>
        <p className="text-sm text-muted-foreground">
          Analyze a repository or explore infrastructure architecture with Grid.{' '}
          <Link href="/docs/getting-started" className="text-primary font-medium hover:underline">
            Prefer step-by-step docs?
          </Link>
        </p>
      </div>

      <div
        role="log"
        aria-live="polite"
        aria-label="Onboarding assistant messages"
        className="max-w-3xl w-full space-y-4 flex-1 overflow-y-auto mb-6"
      >
        {messages.map((msg, i) => (
          <Card
            key={i}
            className={`p-4 ${msg.role === 'AI' ? 'bg-card' : 'bg-primary/5 border-primary/25'}`}
          >
            <div className="flex items-start gap-3">
              {msg.role === 'AI' ? (
                <Bot size={20} className="text-emerald-700 dark:text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />
              ) : (
                <User size={20} className="text-primary mt-0.5 shrink-0" aria-hidden="true" />
              )}
              <div className="prose prose-sm dark:prose-invert max-w-none min-w-0">
                <ReactMarkdown>{msg.content}</ReactMarkdown>
              </div>
            </div>
          </Card>
        ))}

        {loading && (
          <Card className="p-4 bg-card flex items-center gap-2 text-muted-foreground" role="status">
            <Loader2 size={16} className="animate-spin" aria-hidden="true" />
            <span>Thinking...</span>
          </Card>
        )}
        <div ref={scrollRef} />
      </div>

      <div className="relative w-full max-w-3xl">
        <label htmlFor="onboarding-prompt" className="sr-only">
          Paste a repository URL or ask an onboarding question
        </label>
        <Input
          id="onboarding-prompt"
          placeholder="Paste a repo URL or ask a question..."
          className="h-14 pl-5 pr-16 text-base rounded-full border-primary/25 focus-visible:ring-primary"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSend();
          }}
        />
        <Button
          type="button"
          size="icon"
          aria-label="Send message"
          className="absolute right-1.5 top-1.5 rounded-full h-11 w-11 bg-primary hover:bg-primary/90 text-primary-foreground"
          onClick={handleSend}
          disabled={loading || !input.trim()}
        >
          <ArrowRight size={20} aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
