'use client';

import React, { useEffect, useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Users, UserPlus, Trash2, Mail } from 'lucide-react';
import { teamsApi, Team, TeamMember } from '@/lib/api';
import { useAuth } from '@/components/auth-provider';

export default function TeamPage() {
  const { user } = useAuth();
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(false);

  // Invite State
  const [showInviteDialog, setShowInviteDialog] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('MEMBER');
  const [inviteLoading, setInviteLoading] = useState(false);

  useEffect(() => {
    // Initial load
    const storedId = localStorage.getItem('smsly_active_team');
    if (storedId) setActiveTeamId(storedId);

    // Listen for changes
    const handleTeamChange = (e: CustomEvent) => {
      setActiveTeamId(e.detail);
    };

    window.addEventListener('smsly:team-changed', handleTeamChange as EventListener);
    return () => {
      window.removeEventListener('smsly:team-changed', handleTeamChange as EventListener);
    };
  }, []);

  useEffect(() => {
    if (activeTeamId) {
      loadTeamData(activeTeamId);
    } else {
      setTeam(null);
      setMembers([]);
    }
  }, [activeTeamId]);

  const loadTeamData = async (id: string) => {
    setLoading(true);
    try {
      const [teamData, membersData] = await Promise.all([
        teamsApi.get(id),
        teamsApi.members(id)
      ]);
      setTeam(teamData);
      setMembers(membersData);
    } catch (error) {
      console.error("Failed to load team data", error);
    } finally {
      setLoading(false);
    }
  };

  const handleInvite = async () => {
    if (!activeTeamId || !inviteEmail) return;
    setInviteLoading(true);
    try {
      await teamsApi.inviteMember(activeTeamId, inviteEmail, inviteRole);
      setShowInviteDialog(false);
      setInviteEmail('');
      // Refresh list
      loadTeamData(activeTeamId);
    } catch (error) {
      console.error("Failed to invite member", error);
      alert("Failed to invite member. Please try again.");
    } finally {
      setInviteLoading(false);
    }
  };

  const handleRemove = async (memberId: number) => {
    if (!activeTeamId) return;
    if (!confirm("Are you sure you want to remove this member?")) return;

    try {
      await teamsApi.removeMember(activeTeamId, memberId);
      loadTeamData(activeTeamId); // Refresh
    } catch (error) {
      console.error("Failed to remove member", error);
      alert("Failed to remove member.");
    }
  };

  if (!activeTeamId) {
    return (
      <DashboardShell>
        <div className="container max-w-4xl mx-auto p-6">
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              Please select a team from the sidebar to manage members.
            </CardContent>
          </Card>
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="container max-w-4xl mx-auto p-6 space-y-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              <Users className="h-8 w-8" />
              {team ? team.name : 'Loading...'}
            </h1>
            <p className="text-muted-foreground">Manage team members and roles.</p>
          </div>

          <Dialog open={showInviteDialog} onOpenChange={setShowInviteDialog}>
            <DialogTrigger asChild>
              <Button>
                <UserPlus className="mr-2 h-4 w-4" />
                Invite Member
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Invite Team Member</DialogTitle>
                <DialogDescription>
                  Send an invitation to join {team?.name}.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <Label>Email Address</Label>
                  <Input
                    placeholder="colleague@example.com"
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Role</Label>
                  <Select value={inviteRole} onValueChange={setInviteRole}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="ADMIN">Admin</SelectItem>
                      <SelectItem value="MEMBER">Member</SelectItem>
                      <SelectItem value="VIEWER">Viewer</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setShowInviteDialog(false)}>Cancel</Button>
                <Button onClick={handleInvite} disabled={inviteLoading}>
                  {inviteLoading ? "Sending..." : "Send Invite"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Members</CardTitle>
            <CardDescription>
              {members.length} active member{members.length !== 1 ? 's' : ''}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center text-xs font-bold">
                          {member.username.substring(0, 2).toUpperCase()}
                        </div>
                        {member.username}
                        {user?.email === member.email && <span className="text-xs text-muted-foreground ml-2">(You)</span>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center px-2 py-1 rounded-md text-xs font-medium
                        ${member.role === 'ADMIN' ? 'bg-purple-500/10 text-purple-500' :
                          member.role === 'MEMBER' ? 'bg-blue-500/10 text-blue-500' :
                          'bg-gray-500/10 text-gray-500'}`}>
                        {member.role}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{member.email}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleRemove(member.user)}
                        disabled={user?.email === member.email} // Prevent removing self
                        className="text-red-500 hover:text-red-600 hover:bg-red-500/10"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {members.length === 0 && !loading && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center py-8 text-muted-foreground">
                      No members found.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </DashboardShell>
  );
}
