"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Loader2, Users, Search, RefreshCw, MoreVertical, ShieldAlert, CheckCircle2 } from "lucide-react";

import { coreApi } from "@/lib/api";
import api from "@/lib/api";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/use-toast";
import { RequirePermission } from "@/components/RequirePermission";
import { AccessDenied } from "@/components/AccessDenied";
import { PERMISSION } from "@/hooks/usePermissions";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const { toast } = useToast();

  const fetchData = async () => {
    try {
      setRefreshing(true);
      await api.get("/system/config/"); // verify admin
      const data = await coreApi.adminGetUsers();
      setUsers(data);
    } catch (err: unknown) {
      toast({ title: "Failed to load users", variant: "destructive" });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleUserStatus = async (userId: number, currentStatus: boolean) => {
    try {
      await coreApi.adminUpdateUser(userId, { is_active: !currentStatus });
      toast({ title: `User ${!currentStatus ? 'activated' : 'deactivated'} successfully` });
      fetchData();
    } catch (err) {
      toast({ title: "Failed to update user status", variant: "destructive" });
    }
  };

  const filteredUsers = users.filter((u) =>
    u.username.toLowerCase().includes(search.toLowerCase()) ||
    u.email.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) {
    return (
      <RequirePermission code={PERMISSION.ADMIN_ACCESS} fallback={<AccessDenied />}>
        <DashboardShell>
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        </DashboardShell>
      </RequirePermission>
    );
  }

  return (
    <RequirePermission code={PERMISSION.ADMIN_ACCESS} fallback={<AccessDenied />}>
      <DashboardShell>
        <div className="flex-1 p-6 md:p-12 max-w-7xl mx-auto w-full">
        <div className="flex flex-col md:flex-row md:items-center justify-between mb-8 gap-4">
          <div>
            <h1 className="text-3xl font-bold text-foreground flex items-center gap-3">
              <Users className="w-8 h-8 text-primary" />
              User Management
            </h1>
            <p className="text-muted-foreground mt-1">Manage platform users, roles, and status.</p>
          </div>

          <div className="flex items-center gap-3">
            <div className="relative w-64">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search users..."
                className="pl-9 bg-card"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <Button variant="outline" size="icon" onClick={fetchData} disabled={refreshing}>
              <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-6 py-4 font-medium text-muted-foreground">User</th>
                  <th className="px-6 py-4 font-medium text-muted-foreground">Role</th>
                  <th className="px-6 py-4 font-medium text-muted-foreground">Status</th>
                  <th className="px-6 py-4 font-medium text-muted-foreground">Joined</th>
                  <th className="px-6 py-4 font-medium text-muted-foreground text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-12 text-center text-muted-foreground">
                      No users found.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((u) => (
                    <tr key={u.id} className="hover:bg-muted/30 transition-colors">
                      <td className="px-6 py-4">
                        <div className="font-semibold text-foreground">{u.username}</div>
                        <div className="text-xs text-muted-foreground">{u.email}</div>
                      </td>
                      <td className="px-6 py-4">
                        {u.is_superuser ? (
                          <Badge className="bg-purple-500/10 text-purple-600 border-purple-500/20">Super Admin</Badge>
                        ) : u.is_staff ? (
                          <Badge className="bg-blue-500/10 text-blue-600 border-blue-500/20">Staff</Badge>
                        ) : (
                          <Badge variant="outline" className="text-slate-500">User</Badge>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        {u.is_active ? (
                          <span className="flex items-center gap-1.5 text-emerald-600 font-medium text-xs">
                            <CheckCircle2 size={14} /> Active
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-red-500 font-medium text-xs">
                            <ShieldAlert size={14} /> Suspended
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-muted-foreground text-xs">
                        {new Date(u.date_joined).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8">
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuLabel>Actions</DropdownMenuLabel>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onClick={() => toggleUserStatus(u.id, u.is_active)}
                              className={u.is_active ? "text-red-500" : "text-emerald-500"}
                            >
                              {u.is_active ? "Suspend User" : "Activate User"}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </DashboardShell>
    </RequirePermission>
  );
}
