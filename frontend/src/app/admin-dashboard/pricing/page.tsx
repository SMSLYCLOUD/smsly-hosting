"use client";

import { useState, useEffect, useCallback } from "react";
import { Loader2, DollarSign, RefreshCw, Plus } from "lucide-react";

import { resourcePriceApi, ResourcePrice } from "@/lib/api";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ResourcePriceCard } from "@/components/billing/ResourcePriceCard";
import { Badge } from "@/components/ui/badge";

const RESOURCE_TYPES = [
  "compute", "storage", "bandwidth", "database", "cache", "dns",
  "load_balancer", "cdn", "email", "monitoring", "backup",
  "ai_gpu", "function", "container", "vpc",
];

const UNIT_OPTIONS = ["hour", "month", "gb", "mb", "tb", "request", "unit", "gb_month", "mb_month"];

function emptyPrice(): Partial<ResourcePrice> {
  return {
    resource_type: "compute",
    name: "",
    description: "",
    price_per_unit: 0,
    unit: "hour",
    currency: "USD",
    is_active: true,
    tier: "",
  };
}

export default function AdminPricingPage() {
  const [prices, setPrices] = useState<ResourcePrice[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<string>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [formData, setFormData] = useState<Partial<ResourcePrice>>(emptyPrice());
  const [creating, setCreating] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const { toast } = useToast();

  const fetchData = useCallback(async () => {
    try {
      setRefreshing(true);
      const data = await resourcePriceApi.list();
      setPrices(Array.isArray(data) ? data : data?.results || []);
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 403) {
        setAccessDenied(true);
      } else {
        toast({ title: "Failed to load resource prices", variant: "destructive" });
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const filtered = filter === "all" ? prices : prices.filter((p) => p.resource_type === filter);

  const handleUpdate = async (id: number, data: Partial<ResourcePrice>) => {
    try {
      await resourcePriceApi.update(String(id), data);
      setPrices((prev) => prev.map((p) => (p.id === id ? { ...p, ...data } : p)));
      toast({ title: "Resource price updated" });
    } catch {
      toast({ title: "Failed to update resource price", variant: "destructive" });
      throw new Error("Update failed");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await resourcePriceApi.delete(String(id));
      setPrices((prev) => prev.filter((p) => p.id !== id));
      toast({ title: "Resource price deleted" });
    } catch {
      toast({ title: "Failed to delete resource price", variant: "destructive" });
    }
  };

  const handleCreate = async () => {
    if (!formData.name?.trim()) {
      toast({ title: "Name is required", variant: "destructive" });
      return;
    }
    try {
      setCreating(true);
      const created = await resourcePriceApi.create(formData);
      setPrices((prev) => [...prev, created]);
      setCreateOpen(false);
      setFormData(emptyPrice());
      toast({ title: "Resource price created" });
    } catch {
      toast({ title: "Failed to create resource price", variant: "destructive" });
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      </DashboardShell>
    );
  }

  if (accessDenied) {
    return (
      <DashboardShell>
        <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
          <div className="bg-destructive/10 text-destructive rounded-full p-4 mb-4">
            <DollarSign className="w-8 h-8" />
          </div>
          <h2 className="text-2xl font-bold mb-2">Admin Access Required</h2>
          <p className="text-muted-foreground max-w-md">
            You do not have permission to manage resource prices. This area is restricted to staff administrators.
          </p>
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="flex-1 p-6 md:p-12 max-w-7xl mx-auto w-full">
        <div className="flex flex-col md:flex-row md:items-center justify-between mb-8 gap-4">
          <div>
            <h1 className="text-3xl font-bold text-foreground flex items-center gap-3">
              <DollarSign className="w-8 h-8 text-primary" />
              Resource Prices
            </h1>
            <p className="text-muted-foreground mt-1">Manage per-unit pricing for platform resources.</p>
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Add Price
            </Button>
            <Button variant="outline" size="icon" onClick={fetchData} disabled={refreshing}>
              <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-6">
          <Badge
            variant={filter === "all" ? "default" : "outline"}
            className="cursor-pointer"
            onClick={() => setFilter("all")}
          >
            All
          </Badge>
          {RESOURCE_TYPES.map((type) => (
            <Badge
              key={type}
              variant={filter === type ? "default" : "outline"}
              className="cursor-pointer capitalize"
              onClick={() => setFilter(type)}
            >
              {type.replace(/_/g, " ")}
            </Badge>
          ))}
        </div>

        {filtered.length === 0 ? (
          <div className="py-12 text-center text-muted-foreground bg-card border border-border rounded-xl">
            {prices.length === 0
              ? "No resource prices configured yet. Click \"Add Price\" to create one."
              : "No prices match the selected filter."}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            {filtered.map((price) => (
              <ResourcePriceCard
                key={price.id}
                price={price}
                onUpdate={handleUpdate}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add Resource Price</DialogTitle>
            <DialogDescription>Create a new pricing entry for a platform resource.</DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>Resource Type</Label>
                <Select
                  value={formData.resource_type}
                  onValueChange={(v) => setFormData({ ...formData, resource_type: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RESOURCE_TYPES.map((t) => (
                      <SelectItem key={t} value={t} className="capitalize">{t.replace(/_/g, " ")}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Tier</Label>
                <Input
                  value={formData.tier}
                  onChange={(e) => setFormData({ ...formData, tier: e.target.value })}
                  placeholder="e.g. standard, premium"
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Name</Label>
              <Input
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="e.g. Standard Compute CPU"
              />
            </div>

            <div className="space-y-2">
              <Label>Description</Label>
              <Textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Optional description of this pricing entry"
                rows={2}
              />
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>Price per Unit</Label>
                <Input
                  type="number"
                  step="0.0001"
                  value={formData.price_per_unit}
                  onChange={(e) => setFormData({ ...formData, price_per_unit: parseFloat(e.target.value) || 0 })}
                />
              </div>
              <div className="space-y-2">
                <Label>Unit</Label>
                <Select
                  value={formData.unit}
                  onValueChange={(v) => setFormData({ ...formData, unit: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {UNIT_OPTIONS.map((u) => (
                      <SelectItem key={u} value={u}>{u}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Currency</Label>
                <Input
                  value={formData.currency}
                  onChange={(e) => setFormData({ ...formData, currency: e.target.value })}
                  placeholder="USD"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Switch
                id="create-active"
                checked={formData.is_active ?? true}
                onCheckedChange={(checked) => setFormData({ ...formData, is_active: checked })}
              />
              <Label htmlFor="create-active">Active</Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setCreateOpen(false); setFormData(emptyPrice()); }}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={creating}>
              {creating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DashboardShell>
  );
}
