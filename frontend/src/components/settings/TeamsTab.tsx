"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Users, Building, Plus, Mail } from "lucide-react";
import { teamsApi } from "@/lib/api";

export function TeamsTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [teams, setTeams] = useState<any[]>([]);
  const [members, setMembers] = useState<any[]>([]);
  const [activeTeamId, setActiveTeamId] = useState<string>("");
  const [newTeamName, setNewTeamName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("MEMBER");

  const fetchTeams = async () => {
    try {
      const data = await teamsApi.list();
      setTeams(data);
      if (data.length > 0 && !activeTeamId) {
        setActiveTeamId(data[0].id);
      }
    } catch (err) {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const fetchMembers = async (teamId: string) => {
    try {
      const data = await teamsApi.members(teamId);
      setMembers(data);
    } catch (err) {
      setMembers([]);
    }
  };

  useEffect(() => {
    fetchTeams();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (activeTeamId) {
      fetchMembers(activeTeamId);
    }
  }, [activeTeamId]);

  const handleCreateTeam = async () => {
    if (!newTeamName) return;
    try {
      const team = await teamsApi.create(newTeamName);
      setTeams([...teams, team]);
      setActiveTeamId(team.id);
      setNewTeamName("");
      toast({ title: "Team created successfully" });
    } catch (err: any) {
      toast({ title: "Failed to create team", description: err.message, variant: "destructive" });
    }
  };

  const handleInvite = async () => {
    if (!inviteEmail || !activeTeamId) return;
    try {
      await teamsApi.inviteMember(activeTeamId, inviteEmail, inviteRole);
      toast({ title: `Invite sent to ${inviteEmail}` });
      setInviteEmail("");
      fetchMembers(activeTeamId);
    } catch (err: any) {
      toast({ title: "Failed to invite member", description: err.message, variant: "destructive" });
    }
  };

  if (loading) {
    return <div className="flex h-32 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>;
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building className="h-5 w-5" /> Organizations & Teams
          </CardTitle>
          <CardDescription>Manage your teams, SSO providers, and organizational structure.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-end gap-4">
            <div className="space-y-2 flex-1">
              <Label>Active Team</Label>
              <Select value={activeTeamId} onValueChange={setActiveTeamId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a team" />
                </SelectTrigger>
                <SelectContent>
                  {teams.map((t) => (
                    <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 flex-1">
              <Label>Create New Team</Label>
              <div className="flex gap-2">
                <Input placeholder="Engineering" value={newTeamName} onChange={(e) => setNewTeamName(e.target.value)} />
                <Button onClick={handleCreateTeam} variant="secondary"><Plus className="h-4 w-4 mr-1" /> Create</Button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" /> Team Members
          </CardTitle>
          <CardDescription>Manage role-based access (RBAC) for the active team.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex gap-4 items-end">
            <div className="space-y-2 flex-1">
              <Label>Invite Email</Label>
              <Input type="email" placeholder="colleague@example.com" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
            </div>
            <div className="space-y-2 w-48">
              <Label>Role</Label>
              <Select value={inviteRole} onValueChange={setInviteRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ADMIN">Admin</SelectItem>
                  <SelectItem value="MEMBER">Member</SelectItem>
                  <SelectItem value="VIEWER">Viewer</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button onClick={handleInvite}><Mail className="h-4 w-4 mr-2" /> Invite</Button>
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User / Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Joined</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-center text-muted-foreground">No members found.</TableCell>
                </TableRow>
              ) : (
                members.map((m: any) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-medium">{m.email || m.user?.email || "Pending Invite"}</TableCell>
                    <TableCell>{m.role}</TableCell>
                    <TableCell>{m.created_at ? new Date(m.created_at).toLocaleDateString() : "-"}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
