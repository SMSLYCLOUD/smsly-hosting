"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { scopedRegistryApi } from "@/lib/api";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Trash2, Plus, Loader2, RefreshCw, Globe, Lock, Server } from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";

interface ScopedRegistryItem {
  id: string;
  scope_type: string;
  scope_id?: string;
  scope_label?: string;
  registry_url: string;
  username?: string;
  has_password?: boolean;
  is_internal: boolean;
  allowed_registry_hosts: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface ResolvedInfo {
  effective_url: string;
  has_username: boolean;
  has_password: boolean;
  is_scoped: boolean;
  scoped_registry_id: string | null;
  scope_type: string;
  scope_id: string;
  is_default: boolean;
}

interface ScopedRegistryTabProps {
  scopeType?: string;
  scopeId?: string;
  title?: string;
  description?: string;
}

const scopeTypeLabels: Record<string, string> = {
  organization: "Organization",
  team: "Team",
  project: "Project",
};

export function ScopedRegistryTab({
  scopeType,
  scopeId,
  title = "Scoped Registry",
  description = "Configure a container registry for this scope. Settings cascade down: Project → Team → Organization → Platform default.",
}: ScopedRegistryTabProps) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [registries, setRegistries] = useState<ScopedRegistryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resolved, setResolved] = useState<ResolvedInfo | null>(null);
  const [resolving, setResolving] = useState(false);

  const [form, setForm] = useState({
    scope_type: scopeType || "project",
    scope_id: scopeId || "",
    registry_url: "",
    username: "",
    password: "",
    is_internal: false,
    is_active: true,
    allowed_registry_hosts: "",
  });

  const [editingId, setEditingId] = useState<string | null>(null);

  const fetchRegistries = useCallback(async () => {
    try {
      setLoading(true);
      const params: { scope_type?: string; scope_id?: string } = {};
      if (scopeType) params.scope_type = scopeType;
      if (scopeId) params.scope_id = scopeId;
      const data = await scopedRegistryApi.list(params);
      setRegistries(data);
      // Auto-fill the form with the current registry's values so the
      // operator sees the live config instead of blank inputs. Without
      // this, the Configure panel looks unconfigured even when a
      // scoped registry exists and is actively serving deploys.
      const current = Array.isArray(data) ? data.find((r: ScopedRegistryItem) => r.is_active) || data[0] : null;
      if (current && !editingId) {
        setForm((prev: typeof form) => ({
          ...prev,
          registry_url: current.registry_url || "",
          username: current.username || "",
          password: "",
          is_internal: current.is_internal,
          is_active: current.is_active,
          allowed_registry_hosts: (current.allowed_registry_hosts || []).join(", "),
        }));
      }
    } catch {
      toast({ title: "Error", description: "Failed to load scoped registries", variant: "destructive" });
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast, scopeType, scopeId]);

  const fetchResolved = useCallback(async () => {
    if (!scopeType || !scopeId) return;
    try {
      setResolving(true);
      const data = await scopedRegistryApi.resolve({ scope_type: scopeType, scope_id: scopeId });
      setResolved(data);
    } catch {
      // resolve endpoint might not be available or scope doesn't exist yet
      setResolved(null);
    } finally {
      setResolving(false);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    fetchRegistries();
    fetchResolved();
  }, [fetchRegistries, fetchResolved]);

  const handleCreate = async () => {
    const targetScopeType = scopeType || form.scope_type;
    const targetScopeId = scopeId || form.scope_id;
    if (!form.registry_url || !targetScopeType || !targetScopeId) {
      toast({ title: "Missing fields", description: "Registry URL, Scope Type, and Scope ID are required", variant: "destructive" });
      return;
    }
    try {
      setSaving(true);
      await scopedRegistryApi.create({
        scope_type: targetScopeType,
        scope_id: targetScopeId,
        registry_url: form.registry_url,
        username: form.username || undefined,
        password: form.password || undefined,
        is_internal: form.is_internal,
        is_active: form.is_active,
        allowed_registry_hosts: form.allowed_registry_hosts
          ? form.allowed_registry_hosts.split(",").map(h => h.trim()).filter(Boolean)
          : undefined,
      });
      toast({ title: "Created", description: "Registry scope configured." });
      setForm({ scope_type: scopeType || "project", scope_id: scopeId || "", registry_url: "", username: "", password: "", is_internal: false, is_active: true, allowed_registry_hosts: "" });
      fetchRegistries();
      fetchResolved();
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.response?.data?.error || "Failed to create registry scope";
      toast({ title: "Error", description: msg, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async (id: string) => {
    if (!form.registry_url) {
      toast({ title: "Missing fields", description: "Registry URL is required", variant: "destructive" });
      return;
    }
    try {
      setSaving(true);
      await scopedRegistryApi.update(id, {
        registry_url: form.registry_url,
        username: form.username || undefined,
        password: form.password || undefined,
        is_internal: form.is_internal,
        is_active: form.is_active,
        allowed_registry_hosts: form.allowed_registry_hosts
          ? form.allowed_registry_hosts.split(",").map(h => h.trim()).filter(Boolean)
          : [],
      });
      toast({ title: "Updated", description: "Registry scope saved." });
      setEditingId(null);
      setForm({ scope_type: scopeType || "project", scope_id: scopeId || "", registry_url: "", username: "", password: "", is_internal: false, is_active: true, allowed_registry_hosts: "" });
      fetchRegistries();
      fetchResolved();
    } catch (err: any) {
      const msg = err.response?.data?.detail || "Failed to update registry scope";
      toast({ title: "Error", description: msg, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!await confirm({ title: "Remove Registry Scope?", message: "This removes the scoped registry configuration. The scope will fall back to its parent." })) return;
    try {
      await scopedRegistryApi.delete(id);
      toast({ title: "Removed", description: "Registry scope removed." });
      fetchRegistries();
      fetchResolved();
    } catch {
      toast({ title: "Error", description: "Failed to delete registry scope", variant: "destructive" });
    }
  };

  const handleEdit = (item: ScopedRegistryItem) => {
    setEditingId(item.id);
    setForm({
      scope_type: item.scope_type || scopeType || "project",
      scope_id: item.scope_id || scopeId || "",
      registry_url: item.registry_url || "",
      // Pre-fill the username so the operator sees the current value
      // instead of a blank field that looks like nothing is configured.
      username: item.username || "",
      // Password is always blank in edit mode — the backend stores it
      // encrypted and never returns it. Leaving blank keeps the saved one.
      password: "",
      is_internal: item.is_internal,
      is_active: item.is_active,
      allowed_registry_hosts: (item.allowed_registry_hosts || []).join(", "),
    });
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setForm({ scope_type: scopeType || "project", scope_id: scopeId || "", registry_url: "", username: "", password: "", is_internal: false, is_active: true, allowed_registry_hosts: "" });
  };

  const scopeLabel = scopeType ? scopeTypeLabels[scopeType] || scopeType : "Scope";

  return (
    <div className="space-y-6">
      {/* Resolved effective config card */}
      {scopeType && scopeId && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base flex items-center gap-2">
                  <Server className="h-4 w-4 text-muted-foreground" />
                  Effective Registry for this {scopeLabel}
                </CardTitle>
                <CardDescription>
                  Resolved by walking: Project → Team → Organization → Platform default
                </CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={fetchResolved} disabled={resolving}>
                <RefreshCw className={`h-3 w-3 mr-1 ${resolving ? "animate-spin" : ""}`} />
                Refresh
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {resolved ? (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-muted-foreground">Registry URL</span>
                  <p className="font-mono text-xs mt-0.5">{resolved.effective_url || "(none)"}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Authentication</span>
                  <p className="mt-0.5">
                    {resolved.has_username && resolved.has_password ? (
                      <Badge variant="default" className="text-xs">Configured</Badge>
                    ) : (
                      <Badge variant="secondary" className="text-xs">None</Badge>
                    )}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Source</span>
                  <p className="mt-0.5">
                    {resolved.is_default ? (
                      <Badge variant="outline" className="text-xs">Platform Default</Badge>
                    ) : (
                      <Badge variant="default" className="text-xs">Scoped</Badge>
                    )}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Scope</span>
                  <p className="capitalize text-xs mt-0.5">{resolved.scope_type}</p>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {resolving ? "Resolving..." : "Unable to resolve effective registry config."}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Existing scoped registries table */}
      <Card>
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center p-6">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : registries.length === 0 ? (
            <div className="text-center text-sm text-muted-foreground py-6 border rounded-md">
              {scopeType && scopeId
                ? "No registry scope configured for this entity."
                : "No scoped registries found."}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Scope</TableHead>
                  <TableHead>Registry URL</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {registries.map((reg) => (
                  <TableRow key={reg.id}>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <Badge variant="outline" className="w-fit capitalize text-[10px]">
                          {scopeTypeLabels[reg.scope_type] || reg.scope_type}
                        </Badge>
                        <span className="text-xs font-medium text-muted-foreground">
                          {reg.scope_label || reg.scope_id || "-"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{reg.registry_url || "(inherited)"}</TableCell>
                    <TableCell>
                      {reg.is_internal ? (
                        <Badge variant="secondary" className="gap-1">
                          <Lock className="h-3 w-3" /> Internal
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="gap-1">
                          <Globe className="h-3 w-3" /> External
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {reg.is_active ? (
                        <Badge variant="default" className="text-xs">Active</Badge>
                      ) : (
                        <Badge variant="secondary" className="text-xs">Inactive</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right flex justify-end gap-2">
                      <Button size="sm" variant="outline" onClick={() => handleEdit(reg)}>
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => handleDelete(reg.id)} className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Add/Edit form */}
      <Card>
        <CardHeader>
          <CardTitle>{editingId ? "Edit Registry Scope" : "Configure Registry Scope"}</CardTitle>
          <CardDescription>
            {editingId
              ? "Update the registry configuration for this scope."
              : scopeType && scopeId
              ? `Set a custom container registry override for this ${scopeLabel.toLowerCase()}.`
              : "Attach a container registry override to an organization, team, or project."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            {!scopeType && !editingId && (
              <>
                <div className="space-y-2">
                  <Label>Scope Type</Label>
                  <Select
                    value={form.scope_type}
                    onValueChange={v => setForm({ ...form, scope_type: v })}
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="organization">Organization</SelectItem>
                      <SelectItem value="team">Team</SelectItem>
                      <SelectItem value="project">Project</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Target Scope ID (UUID)</Label>
                  <Input
                    placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
                    value={form.scope_id}
                    onChange={e => setForm({ ...form, scope_id: e.target.value })}
                  />
                </div>
              </>
            )}
            <div className="space-y-2 col-span-2">
              <Label>Registry URL</Label>
              <Input
                placeholder="e.g. registry.example.com:5000"
                value={form.registry_url}
                onChange={e => setForm({ ...form, registry_url: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Username</Label>
              <Input
                placeholder="Registry login username"
                value={form.username}
                onChange={e => setForm({ ...form, username: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Password / Token</Label>
              <Input
                type="password"
                placeholder={editingId ? "Leave blank to keep current" : "Registry password or token"}
                value={form.password}
                onChange={e => setForm({ ...form, password: e.target.value })}
              />
            </div>
            <div className="flex items-center gap-3">
              <Switch
                checked={form.is_internal}
                onCheckedChange={v => setForm({ ...form, is_internal: v })}
              />
              <Label>Internal Registry (mesh VPN)</Label>
            </div>
            <div className="flex items-center gap-3">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm({ ...form, is_active: v })}
              />
              <Label>Active</Label>
            </div>
            <div className="space-y-2 col-span-2">
              <Label>Allowed Registry Hosts</Label>
              <Input
                placeholder="Comma-separated hosts, e.g. gcr.io, ghcr.io"
                value={form.allowed_registry_hosts}
                onChange={e => setForm({ ...form, allowed_registry_hosts: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Additional hosts appended to the platform-wide allowlist for this scope.
              </p>
            </div>
          </div>
        </CardContent>
        <CardFooter className="gap-2">
          <Button onClick={editingId ? () => handleUpdate(editingId) : handleCreate} disabled={saving}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : editingId ? null : <Plus className="h-4 w-4 mr-2" />}
            {editingId ? "Save Changes" : "Configure Registry"}
          </Button>
          {editingId && (
            <Button variant="outline" onClick={handleCancelEdit}>
              Cancel
            </Button>
          )}
        </CardFooter>
      </Card>
    </div>
  );
}
