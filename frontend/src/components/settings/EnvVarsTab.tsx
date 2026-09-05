"use client";

import React, { useState, useEffect, useCallback } from "react";
import { servicesApi, EnvVar } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import {
  Plus,
  Trash2,
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  RotateCcw,
  Pencil,
  Check,
  X,
  Rocket,
  Loader2,
  FileText,
  Upload,
  Download,
  Zap,
} from "lucide-react";
import { toast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";

export function EnvVarsTab({ serviceId }: { serviceId: string }) {
  const confirm = useConfirm();
  const [vars, setVars] = useState<EnvVar[]>([]);
  const [loading, setLoading] = useState(true);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newIsSecret, setNewIsSecret] = useState(false);
  const [visibleValues, setVisibleValues] = useState<Record<string, boolean>>(
    {},
  );
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [showBulk, setShowBulk] = useState(false);
  const [bulkMode, setBulkMode] = useState<"import" | "export">("import");
  const [bulkText, setBulkText] = useState("");
  const [bulkSaving, setBulkSaving] = useState(false);
  const [unmaskedSecrets, setUnmaskedSecrets] = useState<Record<number, string>>({});

  const handleBulkImport = async () => {
    const lines = bulkText
      .trim()
      .split("\n")
      .filter((l) => l.trim() && !l.startsWith("#"));
    const parsed: { key: string; value: string }[] = [];
    for (const line of lines) {
      const eqIdx = line.indexOf("=");
      if (eqIdx === -1) continue;
      const key = line.slice(0, eqIdx).trim();
      const value = line.slice(eqIdx + 1).trim();
      if (key) parsed.push({ key, value });
    }
    if (parsed.length === 0) {
      toast({
        title: "No valid variables found",
        description: "Use KEY=VALUE format, one per line.",
        variant: "destructive",
      });
      return;
    }
    setBulkSaving(true);
    let added = 0;
    let updated = 0;
    try {
      for (const { key, value } of parsed) {
        const existing = vars.find((v) => v.key === key);

        // Skip overwriting system or addon variables
        if (existing && (existing.source === 'ADDON' || existing.source === 'SYSTEM')) {
          continue;
        }

        if (existing) {
          // Update: delete old, create new
          await servicesApi.deleteEnvVar(serviceId, existing.id);
          await servicesApi.createEnvVar(serviceId, {
            key,
            value,
            is_secret: existing.is_secret,
          });
          updated++;
        } else {
          await servicesApi.createEnvVar(serviceId, {
            key,
            value,
            is_secret: false,
          });
          added++;
        }
      }
      await loadVars();
      setHasChanges(true);
      setShowBulk(false);
      setBulkText("");
      toast({
        title: `Imported ${added + updated} variables`,
        description: `${added} added, ${updated} updated.`,
      });
    } catch (err) {
      toast({ title: "Bulk import failed", variant: "destructive" });
    } finally {
      setBulkSaving(false);
    }
  };

  const loadVars = useCallback(async () => {
    try {
      const data = await servicesApi.getEnvVars(serviceId);
      setVars(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => {
    void loadVars();
    const interval = setInterval(loadVars, 10000);
    return () => clearInterval(interval);
  }, [loadVars]);

  const handleAdd = async () => {
    if (!newKey || !newValue) return;
    try {
      await servicesApi.createEnvVar(serviceId, {
        key: newKey,
        value: newValue,
        is_secret: newIsSecret,
      });
      setNewKey("");
      setNewValue("");
      setNewIsSecret(false);
      await loadVars();
      setHasChanges(true);
      toast({ title: "Variable added" });
    } catch (err) {
      toast({ title: "Failed to add variable", variant: "destructive" });
    }
  };

  const handleDelete = async (id: number) => {
    if (
      !(await confirm({
        title: "Delete variable?",
        message: "Are you sure you want to delete this environment variable?",
        variant: "destructive",
        confirmText: "Delete",
      }))
    )
      return;
    try {
      await servicesApi.deleteEnvVar(serviceId, id);
      await loadVars();
      setHasChanges(true);
      toast({ title: "Variable deleted" });
    } catch (err) {
      toast({ title: "Failed to delete variable", variant: "destructive" });
    }
  };

  const startEdit = (v: EnvVar) => {
    setEditingId(v.id);
    setEditValue(v.value);
    // Make sure the value is visible while editing
    setVisibleValues((prev) => ({ ...prev, [v.id]: true }));
  };

  const handleDownloadEnv = () => {
    const blob = new Blob([bulkText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = '.env';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleBulkExportToggle = async () => {
    if (!showBulk || bulkMode !== "export") {
      setBulkMode("export");

      // Fetch all secret values that are currently masked
      const missingSecretIds = vars
        .filter(v => v.is_secret && v.source !== 'ADDON' && v.source !== 'SYSTEM' && !unmaskedSecrets[v.id])
        .map(v => v.id);

      const newUnmaskedSecrets = { ...unmaskedSecrets };

      for (const id of missingSecretIds) {
        try {
            const val = await servicesApi.getEnvVarValue(serviceId, id);
            newUnmaskedSecrets[id] = val;
        } catch (error) {
            console.error(`Error fetching secret value for var ${id}`);
        }
      }
      setUnmaskedSecrets(newUnmaskedSecrets);

      const exportVars = vars
        .filter(v => v.source !== 'ADDON' && v.source !== 'SYSTEM')
        .map(v => {
          const val = newUnmaskedSecrets[v.id] || v.value || '';
          return `${v.key}=${val}`;
        });
      setBulkText(exportVars.join('\n'));
    }
    setShowBulk(true);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditValue("");
  };

  const handleSaveEdit = async (v: EnvVar) => {
    if (editValue === v.value) {
      cancelEdit();
      return;
    }
    setSaving(true);
    try {
      // Delete old, create new with same key (no PATCH endpoint)
      await servicesApi.deleteEnvVar(serviceId, v.id);
      await servicesApi.createEnvVar(serviceId, {
        key: v.key,
        value: editValue,
        is_secret: v.is_secret,
      });
      await loadVars();
      setEditingId(null);
      setEditValue("");
      setHasChanges(true);
      toast({ title: "Variable updated" });
    } catch (err) {
      toast({ title: "Failed to update variable", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleRedeploy = async () => {
    setDeploying(true);
    try {
      await servicesApi.deploy(serviceId, "HEAD");
      setHasChanges(false);
      toast({
        title: "🚀 Deployment started",
        description: "Your service is redeploying with the updated variables.",
      });
    } catch (err) {
      toast({ title: "Failed to deploy", variant: "destructive" });
    } finally {
      setDeploying(false);
    }
  };

  const handleApplyNow = async () => {
    if (
      !(await confirm({
        title: "Apply variables now?",
        message:
          "Recreates the running container from the SAME image with the current variables (seconds of downtime, no rebuild). Rolls back automatically on failure.",
        confirmText: "Apply now",
      }))
    )
      return;
    setDeploying(true);
    try {
      const res = await servicesApi.applyEnv(serviceId, { confirm: true });
      setHasChanges(false);
      toast({
        title: "⚡ Variables applied",
        description: res.message || `Container ${res.container || ""} recreated with fresh variables.`,
      });
    } catch (err: any) {
      toast({
        title: "Apply failed",
        description: err?.response?.data?.error || "Could not apply variables.",
        variant: "destructive",
      });
    } finally {
      setDeploying(false);
    }
  };

  const toggleVisibility = (id: number) => {
    setVisibleValues((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const handleToggleLock = async (v: EnvVar) => {
    try {
      await servicesApi.patchEnvVar(serviceId, v.id, {
        is_locked: !v.is_locked,
      });
      await loadVars();
      toast({
        title: v.is_locked ? "Variable unlocked" : "Variable locked",
        description: v.is_locked
          ? `${v.key} can now be overridden by auto-injection.`
          : `${v.key} is now protected from auto-injection.`,
      });
    } catch (err) {
      toast({ title: "Failed to toggle lock", variant: "destructive" });
    }
  };

  if (loading)
    return (
      <div className="p-4 text-center">Loading environment variables...</div>
    );

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
      {/* Redeploy Banner */}
      {hasChanges && (
        <div className="sticky top-0 z-50 animate-in slide-in-from-top-2 fade-in">
          <div className="flex items-center justify-between p-4 bg-gradient-to-r from-red-600/95 to-rose-600/95 backdrop-blur rounded-xl border border-red-500/30 shadow-lg shadow-red-500/20">
            <div className="flex items-center gap-3">
              <div className="w-2 h-2 rounded-full bg-white animate-pulse" />
              <p className="text-sm font-medium text-white">
                ⚠ Variables changed — redeploy to apply
              </p>
            </div>
            <Button
              onClick={handleRedeploy}
              disabled={deploying}
              className="bg-white text-red-700 hover:bg-white/90 font-semibold shadow-sm"
              size="sm"
            >
              {deploying ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <Rocket className="w-4 h-4 mr-2" />
              )}
              {deploying ? "Deploying..." : "Redeploy Now"}
            </Button>
            <Button
              onClick={handleApplyNow}
              disabled={deploying}
              className="bg-white/20 text-white hover:bg-white/30 font-semibold shadow-sm"
              size="sm"
              title="Recreate the container from the same image with fresh variables — no rebuild"
            >
              {deploying ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <Zap className="w-4 h-4 mr-2" />
              )}
              {deploying ? "Applying..." : "Apply now"}
            </Button>
          </div>
        </div>
      )}

      <Card className="p-6 border-border shadow-md">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h3 className="font-bold text-lg">Environment Variables</h3>
            <p className="text-sm text-muted-foreground">
              Configured for the build and runtime environments.
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => {
              if (!showBulk || bulkMode !== "import") {
                setBulkMode("import");
                setBulkText("");
              }
              setShowBulk(true);
            }} className={showBulk && bulkMode === "import" ? "bg-accent" : ""} size="sm">
              <Upload className="w-4 h-4 mr-2" />
              Bulk Import
            </Button>
            <Button variant="outline" onClick={handleBulkExportToggle} className={showBulk && bulkMode === "export" ? "bg-accent" : ""} size="sm">
              <Download className="w-4 h-4 mr-2" />
              Bulk Export
            </Button>
            <Button variant="outline" onClick={loadVars} size="sm">
              <RotateCcw className="w-4 h-4 mr-2" /> Refresh
            </Button>
          </div>
        </div>

        {/* Bulk Editor Panel */}
        {showBulk && (
          <div className="mb-6 bg-muted/30 p-4 rounded-lg border border-dashed border-primary/40 animate-in slide-in-from-top-2 fade-in">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h4 className="font-semibold text-sm">{bulkMode === "import" ? "Bulk Import" : "Bulk Export"} (.env format)</h4>
                <p className="text-xs text-muted-foreground">
                  {bulkMode === "import"
                    ? "Paste your .env file contents here. Existing variables will be updated (excluding SYSTEM/ADDON), new ones created."
                    : "Copy these variables or download as a .env file. SYSTEM and ADDON variables are excluded."}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => setShowBulk(false)}
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
            <textarea
              value={bulkText}
              onChange={(e) => bulkMode === "import" && setBulkText(e.target.value)}
              readOnly={bulkMode === "export"}
              placeholder={`SECRET_KEY=my-super-secret-key\nDEBUG=False\nALLOWED_HOSTS=example.com,localhost\n# Comments are ignored`}
              className={`w-full h-48 font-mono text-sm p-3 rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-y ${bulkMode === "export" ? "opacity-90" : ""}`}
            />
            <div className="flex items-center justify-between mt-3">
              <p className="text-xs text-muted-foreground">
                {bulkMode === "import"
                  ? (bulkText.trim()
                    ? `${
                        bulkText
                          .trim()
                          .split("\n")
                          .filter(
                            (l) => l.trim() && !l.startsWith("#") && l.includes("="),
                          ).length
                      } variables detected`
                    : "Waiting for input...")
                  : `${bulkText.trim().split('\n').filter(l => l.trim()).length} variables exported`}
              </p>
              <div className="flex gap-2">
                {bulkMode === "export" && (
                  <Button variant="outline" onClick={handleDownloadEnv} disabled={!bulkText.trim()}>
                    <Download className="w-4 h-4 mr-2" />
                    Download .env
                  </Button>
                )}
                {bulkMode === "import" && (
                  <Button
                    onClick={handleBulkImport}
                    disabled={bulkSaving || !bulkText.trim()}
                  >
                    {bulkSaving ? (
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <Upload className="w-4 h-4 mr-2" />
                    )}
                    {bulkSaving ? "Importing..." : "Import All"}
                  </Button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Add New Variable Form */}
        <div className="flex gap-4 mb-8 bg-muted/30 p-4 rounded-lg border border-border">
          <div className="flex-1">
            <Input
              placeholder="KEY_NAME"
              className="font-mono uppercase"
              value={newKey}
              onChange={(e) =>
                setNewKey(
                  e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""),
                )
              }
            />
          </div>
          <div className="flex-1">
            <Input
              placeholder="Value"
              type={newIsSecret ? "password" : "text"}
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant={newIsSecret ? "default" : "outline"}
              size="icon"
              onClick={() => setNewIsSecret(!newIsSecret)}
              title="Toggle Secret"
            >
              <Lock
                className={`w-4 h-4 ${newIsSecret ? "text-primary-foreground" : "text-muted-foreground"}`}
              />
            </Button>
            <Button onClick={handleAdd}>
              <Plus className="w-4 h-4 mr-2" /> Add
            </Button>
          </div>
        </div>

        {/* List Variables */}
        <div className="space-y-2">
          {vars.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground border-2 border-dashed border-border rounded-lg">
              No environment variables configured.
            </div>
          ) : (
            vars.map((v) => (
              <div
                key={v.id}
                className={`flex items-center gap-4 p-3 bg-card border rounded-lg group transition-colors ${v.is_locked ? "border-amber-500/40 bg-amber-500/5" : ""} ${v.value?.startsWith("CHANGE_ME") ? "border-red-500/50 bg-red-500/5" : "border-border hover:border-primary/50"}`}
              >
                <div
                  className={`flex-1 font-mono font-bold text-sm min-w-[120px] ${v.value?.startsWith("CHANGE_ME") ? "text-red-500" : "text-primary"}`}
                >
                  {v.key}
                  {v.is_locked && (
                    <span className="ml-2 text-[9px] bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded inline-flex items-center gap-0.5">
                      <Lock className="w-2.5 h-2.5" /> LOCKED
                    </span>
                  )}
                  {v.source === 'ADDON' && (
                    <span className="ml-2 text-[9px] bg-purple-500/20 text-purple-300 px-1.5 py-0.5 rounded">ADDON</span>
                  )}
                  {v.source === 'SYSTEM' && (
                    <span className="ml-2 text-[9px] bg-blue-500/20 text-blue-300 px-1.5 py-0.5 rounded">SYSTEM</span>
                  )}
                  {v.value?.startsWith("CHANGE_ME") && (
                    <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 font-medium normal-case">
                      needs value
                    </span>
                  )}
                </div>

                {/* Value: display or edit mode */}
                <div className="flex-[2] font-mono text-sm relative">
                  {editingId === v.id ? (
                    <div className="flex items-center gap-2">
                      <Input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="font-mono text-sm h-8"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleSaveEdit(v);
                          if (e.key === "Escape") cancelEdit();
                        }}
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-emerald-500 hover:text-emerald-400 hover:bg-emerald-500/10"
                        onClick={() => handleSaveEdit(v)}
                        disabled={saving}
                      >
                        {saving ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Check className="w-4 h-4" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-muted-foreground"
                        onClick={cancelEdit}
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ) : (
                    <>
                      {v.is_secret && !visibleValues[v.id] ? (
                        <span className="text-muted-foreground flex items-center gap-2">
                          <Lock className="w-3 h-3" /> ••••••••••••••••
                        </span>
                      ) : (
                        <span className="break-all">{v.value}</span>
                      )}
                    </>
                  )}
                </div>

                {/* Lock Toggle — always visible */}
                {editingId !== v.id && v.source !== 'ADDON' && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className={`h-8 w-8 ${v.is_locked ? "text-amber-500 hover:text-amber-400" : "text-muted-foreground hover:text-amber-500"}`}
                    onClick={() => handleToggleLock(v)}
                    title={v.is_locked ? "Unlock (allow auto-injection override)" : "Lock (prevent auto-injection override)"}
                  >
                    {v.is_locked ? <Lock className="w-4 h-4" /> : <LockOpen className="w-4 h-4" />}
                  </Button>
                )}

                {/* Other Actions — hover only */}
                {editingId !== v.id && (
                  <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                    {/* Edit Button */}
                    {v.source !== 'ADDON' && (
                        <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-muted-foreground hover:text-primary"
                        onClick={() => startEdit(v)}
                        title="Edit value"
                        >
                        <Pencil className="w-4 h-4" />
                        </Button>
                    )}

                    {/* Show/Hide Secret */}
                    {v.is_secret && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => toggleVisibility(v.id)}
                      >
                        {visibleValues[v.id] ? (
                          <EyeOff className="w-4 h-4" />
                        ) : (
                          <Eye className="w-4 h-4" />
                        )}
                      </Button>
                    )}

                    {/* Delete */}
                    {v.source !== 'ADDON' && (
                        <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                        onClick={() => handleDelete(v.id)}
                        >
                        <Trash2 className="w-4 h-4" />
                        </Button>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="mt-8 pt-6 border-t border-border space-y-2">
          <p className="text-xs text-muted-foreground">
            <strong className="text-foreground">Note:</strong> Changes to
            environment variables need a redeploy (full rebuild) or <strong className="text-foreground">Apply now</strong> (seconds,
            same image) to take effect in the running container.
          </p>
          <p className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Lock className="w-3 h-3 text-amber-500" />
            <strong className="text-foreground">Locked</strong> variables are protected from auto-injection by the platform during deployment.
          </p>
        </div>
      </Card>
    </div>
  );
}
