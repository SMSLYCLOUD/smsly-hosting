'use client';

import React, { useState } from 'react';
import { Bot, X, Send } from 'lucide-react';
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

  const handleSend = async () => {
      if (!input.trim()) return;
      const userText = input;
      setInput('');
      setMessages(prev => [...prev, { role: 'USER', text: userText }]);

      try {
          const res = await axios.post(process.env.NEXT_PUBLIC_API_URL + '/ai/chat/', { message: userText });
          setMessages(prev => [...prev, { role: 'AI', text: res.data.text }]);
      } catch (e) {
          setMessages(prev => [...prev, { role: 'AI', text: "Error connecting to AI brain." }]);
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
                    className="w-96"
                >
                    <Card className="h-96 flex flex-col shadow-2xl border-primary/20 backdrop-blur-xl bg-background/90">
                        <div className="p-4 border-b flex justify-between items-center bg-muted/50">
                            <div className="flex items-center gap-2 font-bold text-primary">
                                <Bot size={18} /> DevOps Copilot
                            </div>
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setIsOpen(false)}>
                                <X size={14} />
                            </Button>
                        </div>
                        <div className="flex-1 p-4 overflow-y-auto space-y-4">
                            {messages.map((m, i) => (
                                <div key={i} className={`flex ${m.role === 'USER' ? 'justify-end' : 'justify-start'}`}>
                                    <div className={`max-w-[80%] p-3 rounded-lg text-sm ${
                                        m.role === 'USER' ? 'bg-primary text-primary-foreground' : 'bg-muted'
                                    }`}>
                                        {m.text}
                                    </div>
                                </div>
                            ))}
                        </div>
                        <div className="p-3 border-t flex gap-2">
                            <Input
                                placeholder="Ask AI..."
                                value={input}
                                onChange={e => setInput(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && handleSend()}
                                className="bg-background"
                            />
                            <Button size="icon" onClick={handleSend}><Send size={16} /></Button>
                        </div>
                    </Card>
                </motion.div>
            )}
        </AnimatePresence>

        <Button
            onClick={() => setIsOpen(!isOpen)}
            className="h-14 w-14 rounded-full shadow-xl bg-gradient-to-br from-emerald-500 to-cyan-500 hover:scale-110 transition-transform"
        >
            <Bot size={28} className="text-white" />
        </Button>
    </div>
  );
}
