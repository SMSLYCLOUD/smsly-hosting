'use client';

/**
 * ConfirmDialog — drop-in replacement for window.confirm().
 *
 * Usage:
 *   1. Wrap your app with <ConfirmProvider>
 *   2. const confirm = useConfirm();
 *   3. const ok = await confirm({ title: '...', message: '...' });
 *
 * Supports destructive variant and optional `requireMatchText` typed confirmation.
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AlertTriangle, Info } from 'lucide-react';

/* ── Types ── */

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'default' | 'destructive' | 'warning';
  requireMatchText?: string;
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
  const [matchInput, setMatchInput] = useState('');
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm: ConfirmFn = useCallback((input) => {
    const options: ConfirmOptions =
      typeof input === 'string' ? { message: input } : input;
    setOpts(options);
    setMatchInput('');
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const handleClose = useCallback((result: boolean) => {
    setOpen(false);
    setMatchInput('');
    resolveRef.current?.(result);
    resolveRef.current = null;
  }, []);

  const isDestructive = opts.variant === 'destructive';
  const isWarning = opts.variant === 'warning';
  const needsMatch = Boolean(opts.requireMatchText);
  const matchSatisfied = !needsMatch || matchInput.trim() === opts.requireMatchText;

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={open} onOpenChange={(v) => { if (!v) handleClose(false); }}>
        <DialogContent className="sm:max-w-[420px] bg-card border-border text-card-foreground">
          <DialogHeader className="gap-3">
            <div className="flex items-center gap-3">
              <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${
                isDestructive
                  ? 'bg-red-500/10 text-red-500'
                  : isWarning
                  ? 'bg-amber-500/10 text-amber-500'
                  : 'bg-cyan-500/10 text-cyan-500'
              }`}>
                {isDestructive ? <AlertTriangle size={20} /> : isWarning ? <AlertTriangle size={20} /> : <Info size={20} />}
              </div>
              <DialogTitle className="text-base text-foreground">
                {opts.title || (isDestructive ? 'Are you sure?' : 'Confirm Action')}
              </DialogTitle>
            </div>
            <DialogDescription className="text-sm text-muted-foreground pl-[52px]">
              {opts.message}
            </DialogDescription>
          </DialogHeader>

          {needsMatch && (
            <div className="pl-[52px] space-y-2">
              <Label htmlFor="confirm-match-input" className="text-xs text-muted-foreground">
                Type <span className="font-mono font-semibold text-foreground">{opts.requireMatchText}</span> to confirm
              </Label>
              <Input
                id="confirm-match-input"
                value={matchInput}
                onChange={(e) => setMatchInput(e.target.value)}
                placeholder={opts.requireMatchText}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && matchSatisfied) {
                    e.preventDefault();
                    handleClose(true);
                  }
                }}
              />
            </div>
          )}

          <DialogFooter className="mt-2 gap-2 sm:gap-2">
            <Button
              variant="outline"
              onClick={() => handleClose(false)}
            >
              {opts.cancelText || 'Cancel'}
            </Button>
            <Button
              variant={isDestructive ? 'destructive' : 'default'}
              disabled={!matchSatisfied}
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
