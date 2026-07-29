"use client";

import React, { useState } from "react";
import { Edit2, Save, X, DollarSign, Cpu, HardDrive, Globe, Database, Network, Mail, Eye, RefreshCw, Box, Layout, Zap, Container, Check } from "lucide-react";
import { ResourcePrice } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useConfirm } from "@/components/ui/confirm-dialog";

const resourceTypeConfig: Record<string, { label: string; icon: React.ReactNode; badgeVariant: "info" | "success" | "warning" | "purple" | "default" | "secondary" | "destructive" | "outline" | "gray" }> = {
  compute: { label: "Compute", icon: <Cpu className="h-5 w-5 text-blue-500" />, badgeVariant: "info" },
  storage: { label: "Storage", icon: <HardDrive className="h-5 w-5 text-orange-500" />, badgeVariant: "warning" },
  bandwidth: { label: "Bandwidth", icon: <Globe className="h-5 w-5 text-cyan-500" />, badgeVariant: "info" },
  database: { label: "Database", icon: <Database className="h-5 w-5 text-green-500" />, badgeVariant: "success" },
  cache: { label: "Cache", icon: <Zap className="h-5 w-5 text-red-500" />, badgeVariant: "destructive" },
  dns: { label: "DNS", icon: <Network className="h-5 w-5 text-purple-500" />, badgeVariant: "purple" },
  load_balancer: { label: "Load Balancer", icon: <Layout className="h-5 w-5 text-blue-500" />, badgeVariant: "info" },
  cdn: { label: "CDN", icon: <Globe className="h-5 w-5 text-yellow-500" />, badgeVariant: "warning" },
  email: { label: "Email", icon: <Mail className="h-5 w-5 text-pink-500" />, badgeVariant: "secondary" },
  monitoring: { label: "Monitoring", icon: <Eye className="h-5 w-5 text-cyan-500" />, badgeVariant: "info" },
  backup: { label: "Backup", icon: <RefreshCw className="h-5 w-5 text-orange-500" />, badgeVariant: "warning" },
  ai_gpu: { label: "AI GPU", icon: <Zap className="h-5 w-5 text-violet-500" />, badgeVariant: "purple" },
  function: { label: "Function", icon: <Box className="h-5 w-5 text-amber-500" />, badgeVariant: "warning" },
  container: { label: "Container", icon: <Container className="h-5 w-5 text-blue-500" />, badgeVariant: "info" },
  vpc: { label: "VPC", icon: <Network className="h-5 w-5 text-emerald-500" />, badgeVariant: "success" },
};

interface ResourcePriceCardProps {
  price: ResourcePrice;
  onUpdate: (id: number, data: Partial<ResourcePrice>) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

export const ResourcePriceCard = React.memo(function ResourcePriceCard({ price, onUpdate, onDelete }: ResourcePriceCardProps) {
  const confirm = useConfirm();
  const [isEditing, setIsEditing] = useState(false);
  const [editData, setEditData] = useState<Partial<ResourcePrice>>({
    price_per_unit: price.price_per_unit,
    unit: price.unit,
    currency: price.currency,
    is_active: price.is_active,
  });
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleSave = async () => {
    try {
      setSaving(true);
      await onUpdate(price.id, editData);
      setIsEditing(false);
    } catch {
      setIsEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    const confirmed = await confirm({
      title: "Delete Resource Price?",
      message: `Are you sure you want to delete "${price.name}" pricing?`,
      variant: "destructive",
      confirmText: "Delete",
    });
    if (!confirmed) return;
    try {
      setDeleting(true);
      await onDelete(price.id);
    } catch {
      // error handled by parent
    } finally {
      setDeleting(false);
    }
  };

  const config = resourceTypeConfig[price.resource_type] || {
    label: price.resource_type,
    icon: <DollarSign className="h-5 w-5 text-muted-foreground" />,
    badgeVariant: "gray" as const,
  };

  const displayPrice = Intl.NumberFormat("en-US", {
    style: "currency",
    currency: price.currency || "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(price.price_per_unit);

  return (
    <Card className="flex flex-col">
      <CardContent className="flex-1 flex flex-col p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2 bg-muted rounded-lg shrink-0">{config.icon}</div>
            <div className="min-w-0">
              <h3 className="font-semibold text-foreground truncate">{price.name}</h3>
              <Badge variant={config.badgeVariant} className="mt-1">{config.label}</Badge>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {!isEditing ? (
              <>
                <Button variant="ghost" size="icon" onClick={() => setIsEditing(true)} disabled={saving || deleting}>
                  <Edit2 className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost" size="icon" onClick={handleDelete} disabled={saving || deleting}
                  className="text-destructive hover:text-destructive hover:bg-destructive/10"
                >
                  <X className="h-4 w-4" />
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" size="icon" onClick={handleSave} disabled={saving}>
                  {saving ? <Save className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                </Button>
                <Button variant="ghost" size="icon" onClick={() => setIsEditing(false)} disabled={saving}>
                  <X className="h-4 w-4" />
                </Button>
              </>
            )}
          </div>
        </div>

        {price.description && !isEditing && (
          <p className="text-sm text-muted-foreground line-clamp-2">{price.description}</p>
        )}

        <div className="space-y-3 pt-2 border-t border-border">
          {isEditing ? (
            <>
              <div className="space-y-1">
                <Label htmlFor={`price-${price.id}`}>Price per Unit</Label>
                <div className="relative">
                  <DollarSign className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    id={`price-${price.id}`}
                    type="number"
                    step="0.0001"
                    className="pl-9"
                    value={editData.price_per_unit}
                    onChange={(e) => setEditData({ ...editData, price_per_unit: parseFloat(e.target.value) || 0 })}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor={`unit-${price.id}`}>Unit</Label>
                  <Input
                    id={`unit-${price.id}`}
                    value={editData.unit}
                    onChange={(e) => setEditData({ ...editData, unit: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`currency-${price.id}`}>Currency</Label>
                  <Input
                    id={`currency-${price.id}`}
                    value={editData.currency}
                    onChange={(e) => setEditData({ ...editData, currency: e.target.value })}
                  />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  id={`active-${price.id}`}
                  checked={editData.is_active ?? true}
                  onCheckedChange={(checked) => setEditData({ ...editData, is_active: checked })}
                />
                <Label htmlFor={`active-${price.id}`} className="text-sm text-muted-foreground">Active</Label>
              </div>
            </>
          ) : (
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-muted-foreground">Price per Unit</p>
                <p className="font-mono text-lg font-semibold">{displayPrice}</p>
                <p className="text-xs text-muted-foreground">per {price.unit}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Currency</p>
                <p className="font-medium">{price.currency || "USD"}</p>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between pt-2">
          <span className={`px-2 py-0.5 text-xs rounded-full ${
            price.is_active
              ? "bg-emerald-500/10 text-emerald-400"
              : "bg-muted text-muted-foreground"
          }`}>
            {price.is_active ? "Active" : "Inactive"}
          </span>
          {price.tier && (
            <span className="text-xs text-muted-foreground uppercase">Tier: {price.tier}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
})
