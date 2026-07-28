'use client';

import React, { useState, useRef, useEffect } from 'react';
import { m, AnimatePresence } from 'framer-motion';
import { X, Send, Bot, User, Loader2, MessageSquare } from 'lucide-react';
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

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (typeof window === 'undefined') return;
    type IdleScheduler = (cb: () => void, opts?: { timeout: number }) => number;
    const w = window as unknown as { requestIdleCallback?: IdleScheduler };
    if (typeof w.requestIdleCallback === 'function') {
      w.requestIdleCallback(() => scrollToBottom(), { timeout: 2000 });
    } else {
      setTimeout(() => scrollToBottom(), 2000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
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
      let data: any = null;

      for (const endpoint of endpoints) {
        const res = await fetch(endpoint, {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ message: trimmed }),
        });

        if (res.status === 404 || res.status === 405) {
          continue;
        }
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        data = await res.json();
        break;
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
        content: 'Sorry, I couldn\'t reach the AI service. Please check your connection and try again.',
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
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            onClick={() => setIsOpen(true)}
            className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-emerald-600 text-white shadow-lg flex items-center justify-center hover:scale-110 transition-transform"
            aria-label="Open Assistant"
          >
            <MessageSquare size={24} />
          </m.button>
        )}
      </AnimatePresence>

      {/* Chat Panel */}
      <AnimatePresence>
        {isOpen && (
          <m.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.95 }}
            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
            className="fixed bottom-6 right-6 z-50 w-[400px] h-[560px] bg-card border border-border rounded-2xl shadow-2xl flex flex-col overflow-hidden"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-emerald-50/50 dark:bg-emerald-950/20">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center">
                  <MessageSquare size={16} className="text-white" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-foreground">Assistant</h3>
                  <p className="text-[10px] text-muted-foreground">Grid Support</p>
                </div>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="p-1.5 rounded-md hover:bg-muted transition-colors"
                aria-label="Close Assistant"
              >
                <X size={16} />
              </button>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {msg.role === 'assistant' && (
                    <div className="w-6 h-6 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0 mt-1">
                      <MessageSquare size={12} className="text-emerald-600 dark:text-emerald-400" />
                    </div>
                  )}
                  <div
                    className={`max-w-[80%] px-3 py-2 rounded-xl text-sm leading-relaxed ${
                      msg.role === 'user'
                        ? 'bg-emerald-600 text-white rounded-br-sm'
                        : 'bg-muted text-foreground rounded-bl-sm'
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                    {msg.provider && msg.role === 'assistant' && (
                      <span className="text-[9px] opacity-50 mt-1 block">via {msg.provider}</span>
                    )}
                  </div>
                  {msg.role === 'user' && (
                    <div className="w-6 h-6 rounded-full bg-slate-500/20 flex items-center justify-center flex-shrink-0 mt-1">
                      <User size={12} className="text-slate-600 dark:text-slate-400" />
                    </div>
                  )}
                </div>
              ))}

              {isLoading && (
                <div className="flex gap-2 justify-start">
                  <div className="w-6 h-6 rounded-full bg-emerald-500/20 flex items-center justify-center flex-shrink-0">
                    <MessageSquare size={12} className="text-emerald-600 dark:text-emerald-400" />
                  </div>
                  <div className="bg-muted px-3 py-2 rounded-xl rounded-bl-sm">
                    <Loader2 size={14} className="animate-spin text-muted-foreground" />
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="px-4 py-3 border-t border-border">
              <div className="flex items-center gap-2">
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask about deployments, errors, config..."
                  className="flex-1 bg-muted rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                  disabled={isLoading}
                />
                <Button
                  size="sm"
                  onClick={sendMessage}
                  disabled={!input.trim() || isLoading}
                  className="bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg px-3 h-9"
                >
                  <Send size={14} />
                </Button>
              </div>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </>
  );
})
