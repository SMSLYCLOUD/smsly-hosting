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
import { Users, UserPlus, Trash2, Mail, PlusCircle } from 'lucide-react';
import { teamsApi, Team, TeamMember } from '@/lib/api';
import { useAuth } from '@/components/auth-provider';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/use-toast';

export default function TeamPage() {
  const { user } = useAuth();
  const confirm = useConfirm();
  const { toast } = useToast();
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(false);

  // Invite State
  const [showInviteDialog, setShowInviteDialog] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('MEMBER');
  const [inviteLoading, setInviteLoading] = useState(false);

  // Create Team State
  const [showCreateTeamDialog, setShowCreateTeamDialog] = useState(false);
  const [newTeamName, setNewTeamName] = useState('');
  const [createTeamLoading, setCreateTeamLoading] = useState(false);

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
    if (!inviteEmail) return;
    
    let currentTeamId = activeTeamId;
    
    if (!currentTeamId) {
      try {
        const newTeam = await teamsApi.create("My Team");
        currentTeamId = newTeam.id;
        setActiveTeamId(currentTeamId);
        localStorage.setItem('smsly_active_team', currentTeamId);
        window.dispatchEvent(new CustomEvent('smsly:team-changed', { detail: currentTeamId }));
        await loadTeamData(currentTeamId);
      } catch (err) {
        toast({ title: "Error", description: "Failed to create default team.", variant: "destructive" });
        return;
      }
    }
    
    setInviteLoading(true);
    try {
      await teamsApi.inviteMember(currentTeamId, inviteEmail, inviteRole);
      setShowInviteDialog(false);
      setInviteEmail('');
      toast({ title: "Success", description: "Invitation sent successfully." });
      await loadTeamData(currentTeamId);
    } catch (error) {
      console.error("Failed to invite member", error);
      toast({ title: "Error", description: "Failed to invite member. Please try again.", variant: "destructive" });
    } finally {
      setInviteLoading(false);
    }
  };

  const handleCreateTeam = async () => {
    if (!newTeamName.trim()) {
      toast({ title: "Error", description: "Team name is required.", variant: "destructive" });
      return;
    }
    setCreateTeamLoading(true);
    try {
      const newTeam = await teamsApi.create(newTeamName);
      setActiveTeamId(newTeam.id);
      localStorage.setItem('smsly_active_team', newTeam.id);
      window.dispatchEvent(new CustomEvent('smsly:team-changed', { detail: newTeam.id }));
      setShowCreateTeamDialog(false);
      setNewTeamName('');
      toast({ title: "Success", description: `Team "${newTeam.name}" created successfully.` });
      await loadTeamData(newTeam.id);
    } catch (error) {
      console.error("Failed to create team", error);
      toast({ title: "Error", description: "Failed to create team. Please try again.", variant: "destructive" });
    } finally {
      setCreateTeamLoading(false);
    }
  };

  const handleRemove = async (memberId: number) => {
    if (!activeTeamId) return;
    if (!await confirm({ title: 'Remove team member?', message: 'Are you sure you want to remove this member from the team?', variant: 'destructive', confirmText: 'Remove' })) return;

    try {
      await teamsApi.removeMember(activeTeamId, memberId);
      toast({ title: "Success", description: "Member removed successfully." });
      await loadTeamData(activeTeamId);
    } catch (error) {
      console.error("Failed to remove member", error);
      toast({ title: "Error", description: "Failed to remove member.", variant: "destructive" });
    }
  };

  if (!activeTeamId) {
    return (
      <DashboardShell>
        <div className="container max-w-4xl mx-auto p-6">
          <Card>
            <CardHeader>
              <CardTitle>Welcome to Team Management</CardTitle>
              <CardDescription>
                Create your first team to start managing members and collaborating.
              </CardDescription>
            </CardHeader>
            <CardContent className="py-10 text-center space-y-4">
              <p className="text-muted-foreground">
                You don't have any teams yet. Create one to get started.
              </p>
              <Dialog open={showCreateTeamDialog} onOpenChange={setShowCreateTeamDialog}>
                <DialogTrigger asChild>
                  <Button>
                    <PlusCircle className="mr-2 h-4 w-4" />
                    Create Team
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Create Team</DialogTitle>
                    <DialogDescription>
                      Add a new team to manage services and members.
                    </DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 py-2 pb-4">
                    <div className="space-y-2">
                      <Label htmlFor="name">Team Name</Label>
                      <Input
                        id="name"
                        placeholder="Acme Inc."
                        value={newTeamName}
                        onChange={(e) => setNewTeamName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleCreateTeam();
                        }}
                      />
                    </div>
                  </div>
                  <DialogFooter>
                    <Button
                      variant="outline"
                      onClick={() => setShowCreateTeamDialog(false)}
                    >
                      Cancel
                    </Button>
                    <Button onClick={handleCreateTeam} disabled={createTeamLoading}>
                      {createTeamLoading ? "Creating..." : "Create Team"}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
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
