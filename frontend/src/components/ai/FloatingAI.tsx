'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Bot, X, Send, Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { motion, AnimatePresence } from 'framer-motion';
import axios from 'axios';

export function FloatingAI() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<{role: 'AI'|'USER', text: string}[]>([
      { role: 'AI', text: "I am ready to assist. How can I optimize your infrastructure?" }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const handleSend = async () => {
      if (!input.trim() || loading) return;
      const userText = input;
      setInput('');
      setLoading(true);
      setMessages(prev => [...prev, { role: 'USER', text: userText }]);

      try {
          const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
          const res = await axios.post(API_URL + '/ai/chat/', { message: userText });
          setMessages(prev => [...prev, { role: 'AI', text: res.data.text }]);
      } catch (e) {
          setMessages(prev => [...prev, { role: 'AI', text: "I'm having trouble connecting to my neural core. Please try again." }]);
      } finally {
          setLoading(false);
      }
  };

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-4">
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0, scale: 0.9, y: 20 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.9, y: 20 }}
                    className="w-96 origin-bottom-right"
                >
                    <Card className="h-[32rem] flex flex-col shadow-2xl border-primary/20 backdrop-blur-xl bg-background/95">
                        <div className="p-4 border-b flex justify-between items-center bg-primary/5">
                            <div className="flex items-center gap-2 font-bold text-primary">
                                <div className="p-1.5 bg-primary/10 rounded-lg">
                                    <Sparkles size={16} />
                                </div>
                                DevOps Copilot
                            </div>
                            <Button variant="ghost" size="icon" className="h-8 w-8 hover:bg-destructive/10 hover:text-destructive" onClick={() => setIsOpen(false)}>
                                <X size={16} />
                            </Button>
                        </div>

                        <div className="flex-1 p-4 overflow-y-auto space-y-4" ref={scrollRef}>
                            {messages.map((m, i) => (
                                <div key={i} className={`flex ${m.role === 'USER' ? 'justify-end' : 'justify-start'}`}>
                                    <div className={`max-w-[85%] p-3 rounded-2xl text-sm shadow-sm ${
                                        m.role === 'USER'
                                            ? 'bg-primary text-primary-foreground rounded-br-sm'
                                            : 'bg-muted text-foreground rounded-bl-sm border border-border'
                                    }`}>
                                        {m.text}
                                    </div>
                                </div>
                            ))}
                            {loading && (
                                <div className="flex justify-start">
                                    <div className="bg-muted p-3 rounded-2xl rounded-bl-sm border border-border flex items-center gap-2 text-muted-foreground text-xs">
                                        <Loader2 size={12} className="animate-spin" /> Thinking...
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="p-3 border-t bg-muted/20">
                            <form
                                className="flex gap-2"
                                onSubmit={(e) => { e.preventDefault(); handleSend(); }}
                            >
                                <Input
                                    placeholder="Ask about logs, costs, or scaling..."
                                    value={input}
                                    onChange={e => setInput(e.target.value)}
                                    className="bg-background focus-visible:ring-primary"
                                    disabled={loading}
                                />
                                <Button type="submit" size="icon" disabled={loading || !input.trim()} className={loading ? "opacity-50" : ""}>
                                    <Send size={16} />
                                </Button>
                            </form>
                        </div>
                    </Card>
                </motion.div>
            )}
        </AnimatePresence>

        <Button
            onClick={() => setIsOpen(!isOpen)}
            className="h-14 w-14 rounded-full shadow-lg bg-primary hover:bg-primary/90 transition-all hover:scale-105 hover:shadow-primary/25"
        >
            {isOpen ? <X size={24} /> : <Bot size={28} />}
        </Button>
    </div>
  );
}
