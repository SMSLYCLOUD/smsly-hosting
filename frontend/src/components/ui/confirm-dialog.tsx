'use client';

/**
 * ConfirmDialog — drop-in replacement for window.confirm().
 *
 * Usage:
 *   1. Wrap your app with <ConfirmProvider>
 *   2. const confirm = useConfirm();
 *   3. const ok = await confirm({ title: '...', message: '...' });
 *
 * Supports destructive variant for dangerous actions (red button).
 */

import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Info } from 'lucide-react';

/* ── Types ── */

interface ConfirmOptions {
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'default' | 'destructive' | 'warning';
}

type ConfirmFn = (opts: ConfirmOptions | string) => Promise<boolean>;

/* ── Context ── */

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error('useConfirm must be used inside <ConfirmProvider>');
  return fn;
}

/* ── Provider ── */

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<ConfirmOptions>({ message: '' });
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm: ConfirmFn = useCallback((input) => {
    const options: ConfirmOptions =
      typeof input === 'string' ? { message: input } : input;
    setOpts(options);
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const handleClose = useCallback((result: boolean) => {
    setOpen(false);
    resolveRef.current?.(result);
    resolveRef.current = null;
  }, []);

  const isDestructive = opts.variant === 'destructive';
  const isWarning = opts.variant === 'warning';

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={open} onOpenChange={(v) => { if (!v) handleClose(false); }}>
        <DialogContent className="sm:max-w-[420px] bg-[#0d1117] border-white/10">
          <DialogHeader className="gap-3">
            <div className="flex items-center gap-3">
              <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${
                isDestructive
                  ? 'bg-red-500/10 text-red-400'
                  : isWarning
                  ? 'bg-amber-500/10 text-amber-400'
                  : 'bg-cyan-500/10 text-cyan-400'
              }`}>
                {isDestructive ? <AlertTriangle size={20} /> : isWarning ? <AlertTriangle size={20} /> : <Info size={20} />}
              </div>
              <DialogTitle className="text-base text-white">
                {opts.title || (isDestructive ? 'Are you sure?' : 'Confirm Action')}
              </DialogTitle>
            </div>
            <DialogDescription className="text-sm text-gray-400 pl-[52px]">
              {opts.message}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-2 gap-2 sm:gap-2">
            <Button
              variant="outline"
              onClick={() => handleClose(false)}
              className="border-white/10 text-gray-300 hover:bg-white/5 hover:text-white"
            >
              {opts.cancelText || 'Cancel'}
            </Button>
            <Button
              variant={isDestructive ? 'destructive' : 'default'}
              onClick={() => handleClose(true)}
              className={
                isDestructive
                  ? 'bg-red-600 hover:bg-red-700 text-white'
                  : isWarning
                  ? 'bg-amber-600 hover:bg-amber-700 text-white'
                  : 'bg-cyan-600 hover:bg-cyan-700 text-white'
              }
            >
              {opts.confirmText || (isDestructive ? 'Delete' : 'Confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmContext.Provider>
  );
}
