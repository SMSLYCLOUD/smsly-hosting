"use client";

import { useState, useEffect } from "react";
import { Loader2, DollarSign, RefreshCw, Save } from "lucide-react";

import { billingApi, PricingPlan } from "@/lib/api";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { Switch } from "@/components/ui/switch";

export default function AdminPricingPage() {
  const [plans, setPlans] = useState<PricingPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [savingPlanId, setSavingPlanId] = useState<number | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const { toast } = useToast();

  const fetchData = async () => {
    try {
      setRefreshing(true);
      const data = await billingApi.adminGetPlans();
      setPlans(data);
    } catch (err: any) {
      if (err.response?.status === 403) {
        setAccessDenied(true);
      } else {
        toast({ title: "Failed to load pricing plans", variant: "destructive" });
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleUpdatePlan = async (id: number) => {
    const planToUpdate = plans.find((p) => p.id === id);
    if (!planToUpdate) return;

    try {
      setSavingPlanId(id);
      await billingApi.adminUpdatePlan(id, planToUpdate);
      toast({ title: `Plan "${planToUpdate.name}" updated successfully` });
      fetchData(); // Refresh to get the latest from server
    } catch (err) {
      toast({ title: "Failed to update plan", variant: "destructive" });
    } finally {
      setSavingPlanId(null);
    }
  };

  const handleChange = (id: number, field: keyof PricingPlan, value: string | number | boolean) => {
    setPlans(plans.map((p) => (p.id === id ? { ...p, [field]: value } : p)));
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
            You do not have permission to view or manage pricing plans. This area is restricted to staff administrators.
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
              Price Settings
            </h1>
            <p className="text-muted-foreground mt-1">Manage platform pricing plans and feature limits.</p>
          </div>

          <div className="flex items-center gap-3">
            <Button variant="outline" size="icon" onClick={fetchData} disabled={refreshing}>
              <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {plans.map((plan) => (
            <div key={plan.id} className="bg-card border border-border rounded-xl shadow-sm p-6 space-y-6">
              <div className="flex items-center justify-between pb-4 border-b border-border">
                <div>
                  <h2 className="text-xl font-semibold text-foreground">{plan.name}</h2>
                  <p className="text-sm text-muted-foreground">{plan.slug}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Label htmlFor={`active-${plan.id}`} className="text-sm text-muted-foreground">Active</Label>
                  <Switch
                    id={`active-${plan.id}`}
                    checked={plan.is_active}
                    onCheckedChange={(checked) => handleChange(plan.id, "is_active", checked)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor={`price-${plan.id}`}>Monthly Price (USD)</Label>
                  <div className="relative">
                    <DollarSign className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                      id={`price-${plan.id}`}
                      type="number"
                      className="pl-9"
                      value={plan.price_monthly_usd}
                      onChange={(e) => handleChange(plan.id, "price_monthly_usd", parseFloat(e.target.value) || 0)}
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`price-year-${plan.id}`}>Yearly Price (USD)</Label>
                  <div className="relative">
                    <DollarSign className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                      id={`price-year-${plan.id}`}
                      type="number"
                      className="pl-9"
                      value={plan.price_yearly_usd}
                      onChange={(e) => handleChange(plan.id, "price_yearly_usd", parseFloat(e.target.value) || 0)}
                    />
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                <h3 className="text-sm font-medium text-foreground">Resource Limits</h3>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Max Services</Label>
                    <Input
                      type="number"
                      value={plan.max_services}
                      onChange={(e) => handleChange(plan.id, "max_services", parseInt(e.target.value, 10) || 0)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Max CPU Cores</Label>
                    <Input
                      type="number"
                      step="0.1"
                      value={plan.max_cpu_cores}
                      onChange={(e) => handleChange(plan.id, "max_cpu_cores", parseFloat(e.target.value) || 0)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Max RAM (MB)</Label>
                    <Input
                      type="number"
                      value={plan.max_memory_mb}
                      onChange={(e) => handleChange(plan.id, "max_memory_mb", parseInt(e.target.value, 10) || 0)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Max Storage (GB)</Label>
                    <Input
                      type="number"
                      value={plan.max_storage_gb}
                      onChange={(e) => handleChange(plan.id, "max_storage_gb", parseInt(e.target.value, 10) || 0)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Max Addons</Label>
                    <Input
                      type="number"
                      value={plan.max_addons}
                      onChange={(e) => handleChange(plan.id, "max_addons", parseInt(e.target.value, 10) || 0)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Max Team Members</Label>
                    <Input
                      type="number"
                      value={plan.max_team_members}
                      onChange={(e) => handleChange(plan.id, "max_team_members", parseInt(e.target.value, 10) || 0)}
                    />
                  </div>
                </div>
              </div>

              <div className="pt-4 flex justify-end">
                <Button
                  onClick={() => handleUpdatePlan(plan.id)}
                  disabled={savingPlanId === plan.id}
                >
                  {savingPlanId === plan.id ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="mr-2 h-4 w-4" />
                  )}
                  Save Changes
                </Button>
              </div>
            </div>
          ))}
          {plans.length === 0 && (
             <div className="col-span-full py-12 text-center text-muted-foreground bg-card border border-border rounded-xl">
               No pricing plans found.
             </div>
          )}
        </div>
      </div>
    </DashboardShell>
  );
}
