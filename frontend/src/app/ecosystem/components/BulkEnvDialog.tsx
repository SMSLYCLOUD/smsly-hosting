'use client';

import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Loader2, Plus, Trash2, CheckCircle2, AlertCircle, Variable } from 'lucide-react';
import { ecosystemApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';

interface EnvRow {
  key: string;
  value: string;
}

interface AppItem {
  id: string;
  name: string;
  repo?: string;
  stack?: string;
}

interface BulkEnvDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  apps: AppItem[];
}

export function BulkEnvDialog({ open, onOpenChange, apps }: BulkEnvDialogProps) {
  const { toast } = useToast();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [envRows, setEnvRows] = useState<EnvRow[]>([{ key: '', value: '' }]);
  const [applying, setApplying] = useState(false);
  const [done, setDone] = useState(false);
  const [result, setResult] = useState<{ affected: number; message?: string } | null>(null);

  useEffect(() => {
    if (open) {
      setSelectedIds(new Set());
      setEnvRows([{ key: '', value: '' }]);
      setApplying(false);
      setDone(false);
      setResult(null);
    }
  }, [open]);

  const toggleApp = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedIds.size === apps.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(apps.map(a => a.id)));
    }
  };

  const addRow = () => setEnvRows([...envRows, { key: '', value: '' }]);

  const removeRow = (idx: number) => {
    if (envRows.length <= 1) return;
    setEnvRows(envRows.filter((_, i) => i !== idx));
  };

  const updateRow = (idx: number, field: keyof EnvRow, val: string) => {
    setEnvRows(envRows.map((row, i) => i === idx ? { ...row, [field]: val } : row));
  };

  const validEnvVars = envRows.filter(r => r.key.trim().length > 0);
  const envVarsRecord: Record<string, string> = {};
  validEnvVars.forEach(r => { envVarsRecord[r.key.trim()] = r.value; });

  const handleApply = async () => {
    if (selectedIds.size === 0 || validEnvVars.length === 0) return;
    setApplying(true);
    setDone(false);
    try {
      const data = await ecosystemApi.bulkUpdateEnvironment({
        app_ids: Array.from(selectedIds),
        env_vars: envVarsRecord,
      });
      setResult({ affected: selectedIds.size, message: data.message });
      setDone(true);
      toast({ title: `Env vars applied to ${selectedIds.size} app(s)` });
    } catch (err: any) {
      const msg = err?.response?.data?.error || err?.message || 'Failed to update env vars';
      toast({ title: 'Bulk env update failed', description: msg, variant: 'destructive' });
    } finally {
      setApplying(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Variable size={18} className="text-emerald-500" />
            Bulk Environment Update
          </DialogTitle>
          <DialogDescription>
            Set environment variables across multiple ecosystem apps at once.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-2">
          {/* App selector */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-muted-foreground">
                Select Apps ({selectedIds.size}/{apps.length})
              </span>
              <button
                onClick={toggleAll}
                className="text-xs text-primary hover:underline"
              >
                {selectedIds.size === apps.length ? 'Deselect All' : 'Select All'}
              </button>
            </div>
            <ScrollArea className="h-40 border border-border rounded-lg p-2">
              {apps.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No ecosystem apps available. Run a scan first.
                </p>
              ) : (
                <div className="space-y-1">
                  {apps.map(app => (
                    <label
                      key={app.id}
                      className={`flex items-center gap-2 p-2 rounded-md cursor-pointer transition-colors ${
                        selectedIds.has(app.id) ? 'bg-emerald-500/10' : 'hover:bg-muted'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedIds.has(app.id)}
                        onChange={() => toggleApp(app.id)}
                        className="rounded"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{app.name || app.repo || app.id}</p>
                        {app.stack && (
                          <span className="text-[10px] text-muted-foreground">{app.stack}</span>
                        )}
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </ScrollArea>
          </div>

          {/* Env var editor */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-muted-foreground">
                Environment Variables
              </span>
              <button
                onClick={addRow}
                className="text-xs flex items-center gap-1 text-primary hover:underline"
              >
                <Plus size={12} /> Add Variable
              </button>
            </div>
            <div className="space-y-2">
              {envRows.map((row, idx) => (
                <div key={idx} className="flex gap-2 items-center">
                  <Input
                    placeholder="KEY"
                    value={row.key}
                    onChange={e => updateRow(idx, 'key', e.target.value)}
                    className="font-mono text-xs w-2/5"
                  />
                  <Input
                    placeholder="value"
                    value={row.value}
                    onChange={e => updateRow(idx, 'value', e.target.value)}
                    className="font-mono text-xs flex-1"
                  />
                  <button
                    onClick={() => removeRow(idx)}
                    className="p-1.5 text-muted-foreground hover:text-red-500 transition-colors disabled:opacity-30"
                    disabled={envRows.length <= 1}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Result confirmation */}
          <AnimatePresence>
            {done && result && (
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="flex items-center gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-600 text-sm"
              >
                <CheckCircle2 size={16} />
                <span>Successfully updated <strong>{result.affected}</strong> app(s).</span>
                {result.message && <span className="text-muted-foreground">— {result.message}</span>}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-2 border-t border-border">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {done ? 'Close' : 'Cancel'}
          </Button>
          {!done && (
            <Button
              onClick={handleApply}
              disabled={selectedIds.size === 0 || validEnvVars.length === 0 || applying}
            >
              {applying ? (
                <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Applying...</>
              ) : (
                <>Apply to {selectedIds.size} App{selectedIds.size !== 1 ? 's' : ''}</>
              )}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
