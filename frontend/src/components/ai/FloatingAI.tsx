'use client';

import React, { useState, useRef, useEffect } from 'react';
import { m, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, Send, User, Loader2, MessageSquare } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  provider?: string;
  timestamp: Date;
}

export const FloatingAI = React.memo(function FloatingAI() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: "Hi! I'm your Grid Assistant. I can help with deployments, troubleshooting, and configuration. Ask me anything!",
      provider: 'System',
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const toggleBtnRef = useRef<HTMLButtonElement>(null);
  const prefersReducedMotion = useReducedMotion();

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({
      behavior: prefersReducedMotion ? 'auto' : 'smooth',
    });
  };

  useEffect(() => {
    if (!isOpen) return;
    scrollToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, isOpen]);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false);
        toggleBtnRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen]);

  const sendMessage = async () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    const userMsg: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);

    try {
      const endpoints = ['/api/v1/cloud/intelligence/ask/', '/api/v1/cloud/intelligence/chat/'];
      let data: { response?: string; provider?: string } | null = null;
      let authRequired = false;

      for (const endpoint of endpoints) {
        const res = await fetch(endpoint, {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ message: trimmed }),
        });

        if (res.status === 401 || res.status === 403) {
          authRequired = true;
          break;
        }
        if (res.status === 404 || res.status === 405) {
          continue;
        }
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        data = await res.json();
        break;
      }

      if (authRequired) {
        setMessages((prev) => [
          ...prev,
          {
            id: `auth-${Date.now()}`,
            role: 'assistant',
            content: 'Please sign in at /login to ask the cluster AI assistant live diagnostics, or browse /docs for guides.',
            provider: 'System',
            timestamp: new Date(),
          },
        ]);
        return;
      }

      if (!data) {
        throw new Error('No compatible AI endpoint available.');
      }

      const assistantMsg: Message = {
        id: `ai-${Date.now()}`,
        role: 'assistant',
        content: data.response || 'No response received.',
        provider: data.provider || 'AI',
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch {
      const errorMsg: Message = {
        id: `err-${Date.now()}`,
        role: 'assistant',
        content: "Sorry, I couldn't reach the AI service. Please check your connection and try again.",
        provider: 'System',
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <>
      {/* Floating Toggle Button */}
      <AnimatePresence>
        {!isOpen && (
          <m.button
            ref={toggleBtnRef}
            type="button"
            initial={prefersReducedMotion ? false : { scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { scale: 0.9, opacity: 0 }}
            onClick={() => setIsOpen(true)}
            className="fixed bottom-4 right-4 z-50 w-14 h-14 rounded-full bg-emerald-700 hover:bg-emerald-800 text-white shadow-lg flex items-center justify-center transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Open Grid Assistant"
          >
            <MessageSquare size={22} aria-hidden="true" />
          </m.button>
        )}
      </AnimatePresence>

      {/* Chat Panel */}
      <AnimatePresence>
        {isOpen && (
          <m.div
            role="dialog"
            aria-modal="false"
            aria-label="Grid Support Assistant"
            initial={prefersReducedMotion ? false : { opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.96 }}
            transition={{ duration: 0.18 }}
            className="fixed bottom-4 right-4 z-50 w-[calc(100vw-2rem)] sm:w-[400px] h-[min(560px,calc(100dvh-5rem))] bg-card border border-border rounded-2xl shadow-2xl flex flex-col overflow-hidden"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-emerald-50/60 dark:bg-emerald-950/30">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-full bg-emerald-700 flex items-center justify-center shrink-0">
                  <MessageSquare size={15} className="text-white" aria-hidden="true" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-foreground leading-tight">Assistant</p>
                  <p className="text-xs text-muted-foreground leading-tight">Grid Support</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setIsOpen(false);
                  toggleBtnRef.current?.focus();
                }}
                className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] -mr-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="Close Assistant"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>

            {/* Messages */}
            <div role="log" aria-live="polite" aria-label="Assistant messages" className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {msg.role === 'assistant' && (
                    <div className="w-6 h-6 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0 mt-1" aria-hidden="true">
                      <MessageSquare size={12} className="text-emerald-700 dark:text-emerald-400" />
                    </div>
                  )}
                  <div
                    className={`max-w-[82%] px-3 py-2 rounded-xl text-sm leading-relaxed ${
                      msg.role === 'user'
                        ? 'bg-emerald-700 text-white rounded-br-sm'
                        : 'bg-muted text-foreground rounded-bl-sm'
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                    {msg.provider && msg.role === 'assistant' && (
                      <span className="text-[11px] opacity-75 mt-1 block">via {msg.provider}</span>
                    )}
                  </div>
                  {msg.role === 'user' && (
                    <div className="w-6 h-6 rounded-full bg-slate-500/20 flex items-center justify-center flex-shrink-0 mt-1" aria-hidden="true">
                      <User size={12} className="text-slate-700 dark:text-slate-300" />
                    </div>
                  )}
                </div>
              ))}

              {isLoading && (
                <div className="flex gap-2 justify-start" role="status" aria-label="Assistant is typing">
                  <div className="w-6 h-6 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0" aria-hidden="true">
                    <MessageSquare size={12} className="text-emerald-700 dark:text-emerald-400" />
                  </div>
                  <div className="bg-muted px-3 py-2 rounded-xl rounded-bl-sm">
                    <Loader2 size={14} className="animate-spin text-muted-foreground" aria-hidden="true" />
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="px-4 py-3 border-t border-border">
              <div className="flex items-center gap-2">
                <label htmlFor="floating-ai-input" className="sr-only">
                  Ask Grid Assistant
                </label>
                <input
                  id="floating-ai-input"
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask about deployments, errors, config..."
                  className="flex-1 min-w-0 h-11 bg-muted rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  disabled={isLoading}
                />
                <Button
                  type="button"
                  size="icon"
                  onClick={sendMessage}
                  disabled={!input.trim() || isLoading}
                  aria-label="Send message"
                  className="bg-emerald-700 hover:bg-emerald-800 text-white rounded-lg min-h-[44px] min-w-[44px] shrink-0"
                >
                  <Send size={15} aria-hidden="true" />
                </Button>
              </div>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </>
  );
});
