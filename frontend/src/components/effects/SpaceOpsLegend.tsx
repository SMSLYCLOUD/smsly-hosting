import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Info, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function SpaceOpsLegend() {
    const [isOpen, setIsOpen] = useState(false);

    return (
        <div className="fixed bottom-4 right-4 z-50">
            <AnimatePresence>
                {isOpen && (
                    <motion.div
                        initial={{ opacity: 0, y: 10, scale: 0.95 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 10, scale: 0.95 }}
                        className="mb-2 p-4 bg-card/95 backdrop-blur-md border border-border rounded-xl shadow-2xl text-xs w-72"
                    >
                        <div className="flex justify-between items-center mb-3 pb-2 border-b border-border/50">
                            <span className="font-bold uppercase tracking-wider text-muted-foreground">SpaceOps Visual Legend</span>
                            <button onClick={() => setIsOpen(false)} className="text-muted-foreground hover:text-foreground">
                                <X size={14} />
                            </button>
                        </div>
                        <ul className="space-y-2">
                            <li className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-[rgb(255,200,50)]" /> Calm planet = Healthy service</li>
                            <li className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-emerald-400" /> Comet stream = Active deployment</li>
                            <li className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-red-500" /> Red star = Failed deployment</li>
                            <li className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-amber-500" /> Amber stars = Warning / Anomaly</li>
                            <li className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-black border border-white/20" /> Black hole = Critical outage</li>
                            <li className="flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-white shadow-[0_0_8px_white]" /> White hole = Rollback / Recovery</li>
                        </ul>
                    </motion.div>
                )}
            </AnimatePresence>

            <Button
                variant="outline"
                size="icon"
                onClick={() => setIsOpen(!isOpen)}
                className="rounded-full shadow-lg bg-card/80 backdrop-blur-md border-border/50 hover:bg-muted"
            >
                <Info size={16} className="text-muted-foreground" />
            </Button>
        </div>
    );
}
