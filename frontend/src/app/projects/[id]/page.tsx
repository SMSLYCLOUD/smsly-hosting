'use client';

/**
 * Project detail page — Railway-style project view with services cards,
 * quick-add/move service, project settings.
 */

import React, { useState, useEffect, useCallback, Suspense } from 'react';
import { useRouter, useParams, useSearchParams } from 'next/navigation';
import { projectsApi, servicesApi, teamsApi, projectMembersApi, Project, Service, Team, ProjectMember } from '@/lib/api';
import { PROJECT_EMOJI_OPTIONS, PROJECT_COLOR_OPTIONS } from '@/lib/project-constants';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ArrowLeft, Plus, FolderOpen, Settings2,
  GitBranch, Globe, Layers, Trash2, X, Save, RefreshCcw, Server,
  UserPlus, Mail, Shield, Users, Loader2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { ScopedRegistryTab } from '@/components/settings/ScopedRegistryTab';

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#10b981',
  BUILDING: '#3b82f6',
  DEPLOYING: '#818cf8',
  QUEUED: '#fbbf24',
  FAILED: '#ef4444',
  CANCELLED: '#f97316',
  UNKNOWN: '#6366f1',
};

function ProjectDetailContent() {
  const router = useRouter();
  const confirm = useConfirm();
  const params = useParams();
  const searchParams = useSearchParams();
  const projectId = params.id as string;
  const initialTab = searchParams.get('tab') || 'services';
  const { toast } = useToast();

  const [project, setProject] = useState<Project | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [allServices, setAllServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'services' | 'settings' | 'registry'>(initialTab as 'services' | 'settings' | 'registry');
  const [showAddService, setShowAddService] = useState(false);

  // Settings state
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editEmoji, setEditEmoji] = useState('📦');
  const [editColor, setEditColor] = useState('#6366f1');
  const [editSubnet, setEditSubnet] = useState('');
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // Team & Members
  const [teams, setTeams] = useState<Team[]>([]);
  const [editTeamId, setEditTeamId] = useState<string | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<string>('MEMBER');
  const [inviting, setInviting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [proj, svcs, teamList] = await Promise.all([
        projectsApi.get(projectId),
        projectsApi.services(projectId),
        teamsApi.list().catch(() => []),
      ]);
      setProject(proj);
      setServices(svcs);
      setEditName(proj.name);
      setEditDesc(proj.description || '');
      setEditSubnet(proj.internal_subnet || '');
      setEditEmoji(proj.icon_emoji);
      setEditColor(proj.color);
      setTeams(teamList);
    } catch (err) {
      console.error('Failed to load project:', err);
      toast({ title: 'Error', description: 'Failed to load project', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { load(); }, [load]);

  // Load ungrouped services for the add modal
  const loadUngrouped = useCallback(async () => {
    try {
      const all = await servicesApi.list();
      // Show services not in this project
      setAllServices(all.filter(s => s.project !== projectId));
    } catch {
      console.error('Failed to load services for add modal');
    }
  }, [projectId]);

  const handleMoveService = async (serviceId: string) => {
    try {
      await projectsApi.moveService(projectId, serviceId);
      setShowAddService(false);
      toast({ title: 'Service added to project' });
      load();
    } catch {
      toast({ title: 'Error', description: 'Failed to add service', variant: 'destructive' });
    }
  };

  const handleRemoveService = async (e: React.MouseEvent, serviceId: string) => {
    e.stopPropagation();
    try {
      await projectsApi.removeService(projectId, serviceId);
      setServices(prev => prev.filter(s => s.id !== serviceId));
      toast({ title: 'Service removed from project' });
    } catch {
      toast({ title: 'Error', description: 'Failed to remove service', variant: 'destructive' });
    }
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    try {
      const updated = await projectsApi.update(projectId, {
        name: editName.trim(),
        description: editDesc.trim(),
        icon_emoji: editEmoji,
        color: editColor,
        internal_subnet: editSubnet.trim(),
      });
      setProject(updated);
      toast({ title: 'Project updated' });
    } catch (err: any) {
      toast({ title: 'Error', description: 'Failed to update project', variant: 'destructive' });
    } finally {
      setSaving(false);
    }
  };

  const loadMembers = useCallback(async () => {
    setMembersLoading(true);
    try {
      const data = await projectMembersApi.list(projectId);
      setMembers(data);
    } catch {
      setMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (tab === 'settings') {
      loadMembers();
    }
  }, [tab, loadMembers]);

  const handleInviteMember = async () => {
    if (!inviteEmail.trim()) return;
    setInviting(true);
    try {
      await projectMembersApi.invite(projectId, inviteEmail.trim(), inviteRole);
      toast({ title: 'Member invited', description: `${inviteEmail} added as ${inviteRole}` });
      setInviteEmail('');
      loadMembers();
    } catch (err: any) {
      toast({ title: 'Error', description: err?.response?.data?.error || 'Failed to invite member', variant: 'destructive' });
    } finally {
      setInviting(false);
    }
  };

  const handleRemoveMember = async (memberId: string, username: string) => {
    if (!await confirm({ title: 'Remove member?', message: `Remove ${username} from this project?`, variant: 'destructive', confirmText: 'Remove' })) return;
    try {
      await projectMembersApi.remove(projectId, memberId);
      setMembers(prev => prev.filter(m => m.id !== memberId));
      toast({ title: 'Member removed' });
    } catch {
      toast({ title: 'Error', description: 'Failed to remove member', variant: 'destructive' });
    }
  };

  const handleChangeRole = async (memberId: string, role: string) => {
    try {
      await projectMembersApi.changeRole(projectId, memberId, role);
      setMembers(prev => prev.map(m => m.id === memberId ? { ...m, role: role as ProjectMember['role'] } : m));
      toast({ title: 'Role updated' });
    } catch {
      toast({ title: 'Error', description: 'Failed to update role', variant: 'destructive' });
    }
  };

  const handleSyncEnvs = async () => {
    setSyncing(true);
    try {
      await projectsApi.syncEnvs(projectId);
      toast({ 
        title: 'Ecosystem Synced', 
        description: 'Environment variables propagated to all services in this project.' 
      });
    } catch (err: any) {
      toast({ 
        title: 'Sync Failed', 
        description: err?.response?.data?.error || 'Failed to sync ecosystem environments.', 
        variant: 'destructive' 
      });
    } finally {
      setSyncing(false);
    }
  };

  const formatTimeAgo = (dateStr?: string | null) => {
    if (!dateStr) return '';
    const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };

  if (loading) {
    return (
      <DashboardShell>
        <div className="container max-w-6xl py-10 relative z-10">
          <div className="h-8 w-48 bg-zinc-800 rounded-lg animate-pulse mb-6" />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-36 rounded-2xl bg-zinc-800/40 animate-pulse border border-zinc-800" />
            ))}
          </div>
        </div>
      </DashboardShell>
    );
  }

  if (!project) {
    return (
      <DashboardShell>
        <div className="container max-w-6xl py-10 relative z-10 text-center">
          <h2 className="text-xl font-bold text-white mb-2">Project not found</h2>
          <Button variant="ghost" onClick={() => router.push('/projects')}>
            <ArrowLeft className="w-4 h-4 mr-2" /> Back to Projects
          </Button>
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="container max-w-6xl py-10 relative z-10">
        {/* Back + Header */}
        <button
          onClick={() => router.push('/projects')}
          className="flex items-center gap-1.5 text-sm text-zinc-400 hover:text-white transition-colors mb-4"
        >
          <ArrowLeft className="w-4 h-4" /> All Projects
        </button>

        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-4">
            <div
              className="w-14 h-14 rounded-2xl flex items-center justify-center text-2xl shadow-lg"
              style={{ backgroundColor: project.color + '25', boxShadow: `0 0 30px ${project.color}15` }}
            >
              {project.icon_emoji}
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white">{project.name}</h1>
              {project.description && (
                <p className="text-sm text-zinc-400">{project.description}</p>
              )}
              <div className="flex items-center gap-3 mt-1 text-xs text-zinc-500">
                <span className="flex items-center gap-1"><FolderOpen className="w-3 h-3" /> {services.length} services</span>
                <span>·</span>
                <span>Created {formatTimeAgo(project.created_at)}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { setShowAddService(true); loadUngrouped(); }}
              className="text-zinc-400 hover:text-white"
            >
              <Plus className="w-4 h-4 mr-1" /> Add Service
            </Button>
            {services.length > 1 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={handleSyncEnvs}
                disabled={syncing}
                className="text-zinc-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-all border border-transparent hover:border-emerald-500/20"
              >
                <RefreshCcw className={cn("w-3.5 h-3.5 mr-1.5", syncing && "animate-spin")} />
                {syncing ? 'Syncing...' : 'Sync Ecosystem'}
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => router.push('/new')}
              className="bg-gradient-to-r from-emerald-500 to-green-600 hover:from-emerald-600 hover:to-green-700 text-white font-bold shadow-lg shadow-emerald-500/20"
            >
              <Plus className="w-4 h-4 mr-1" /> New Service
            </Button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-zinc-800 mb-6">
          {[
            { id: 'services' as const, label: 'Services', icon: Layers },
            { id: 'registry' as const, label: 'Registry', icon: Server },
            { id: 'settings' as const, label: 'Settings', icon: Settings2 },
          ].map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`relative flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors ${
                tab === t.id
                  ? 'text-white'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <t.icon className="w-4 h-4" />
              {t.label}
              {tab === t.id && (
                <motion.div
                  layoutId="project-tab-indicator"
                  className="absolute bottom-0 left-0 right-0 h-0.5 rounded-full"
                  style={{ backgroundColor: project.color }}
                />
              )}
            </button>
          ))}
        </div>

        {/* Services Tab */}
        {tab === 'services' && (
          <>
            {services.length === 0 ? (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex flex-col items-center justify-center py-20 text-center"
              >
                <div className="text-5xl mb-4">🔗</div>
                <h2 className="text-lg font-bold text-white mb-2">No services in this project</h2>
                <p className="text-sm text-zinc-500 mb-4 max-w-sm">
                  Add existing services or create a new one to get started.
                </p>
                <div className="flex gap-3">
                  <Button
                    variant="outline"
                    onClick={() => { setShowAddService(true); loadUngrouped(); }}
                  >
                    <Plus className="w-4 h-4 mr-1" /> Add Existing
                  </Button>
                  <Button
                    onClick={() => router.push('/new')}
                    className="bg-emerald-600 hover:bg-emerald-700 text-white"
                  >
                    <Plus className="w-4 h-4 mr-1" /> New Service
                  </Button>
                </div>
              </motion.div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {services.map((svc, i) => {
                  const statusKey = svc.latest_deployment?.status || 'UNKNOWN';
                  const statusColor = STATUS_COLORS[statusKey] || '#6366f1';
                  return (
                    <motion.div
                      key={svc.id}
                      initial={{ opacity: 0, y: 15 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.04 }}
                      onClick={() => router.push(`/services/${svc.id}`)}
                      className="group relative rounded-xl border border-zinc-800/80 bg-zinc-900/50 p-4 cursor-pointer transition-all hover:border-zinc-600 hover:bg-zinc-800/50 hover:-translate-y-0.5"
                    >
                      {/* Status bar */}
                      <div className="absolute top-0 left-0 w-1 h-full rounded-l-xl" style={{ backgroundColor: statusColor }} />

                      <div className="flex items-start justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: statusColor }} />
                          <h3 className="font-semibold text-sm text-white">{svc.name}</h3>
                        </div>
                        <button
                          onClick={e => handleRemoveService(e, svc.id)}
                          title="Remove from project"
                          className="p-1 rounded text-zinc-600 hover:text-red-400 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 transition-all"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>

                      <div className="space-y-1 text-xs text-zinc-500">
                        {svc.repository_url && (
                          <div className="flex items-center gap-1.5">
                            <GitBranch className="w-3 h-3" />
                            <span className="truncate">{svc.repository_url.replace(/https?:\/\//, '').replace('.git', '')}</span>
                          </div>
                        )}
                        {svc.branch && (
                          <div className="flex items-center gap-1.5 text-zinc-400">
                            ⎇ {svc.branch}
                          </div>
                        )}
                        {svc.public_domain && (
                          <div className="flex items-center gap-1.5">
                            <Globe className="w-3 h-3" />
                            <span className="truncate">{svc.public_domain}</span>
                          </div>
                        )}
                      </div>

                      <div className="flex items-center justify-between mt-3 pt-2 border-t border-zinc-800/60">
                        <span
                          className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full"
                          style={{ color: statusColor, backgroundColor: statusColor + '15' }}
                        >
                          {statusKey}
                        </span>
                        {svc.latest_deployment?.created_at && (
                          <span className="text-[10px] text-zinc-600">
                            {formatTimeAgo(svc.latest_deployment.created_at)}
                          </span>
                        )}
                      </div>
                    </motion.div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* Registry Tab */}
        {tab === 'registry' && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <ScopedRegistryTab
              scopeType="project"
              scopeId={projectId}
              title="Project Registry"
              description="Configure where this project's built images are pushed."
            />
          </motion.div>
        )}

        {/* Settings Tab */}
        {tab === 'settings' && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="max-w-2xl space-y-8"
          >
            {/* General Settings */}
            <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-5">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <Settings2 className="w-4 h-4 text-zinc-400" /> General
              </h3>

              <div>
                <label className="block text-sm font-medium text-zinc-400 mb-1.5">Project Name</label>
                <input
                  value={editName}
                  onChange={e => setEditName(e.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2.5 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-zinc-400 mb-1.5">Description</label>
                <textarea
                  value={editDesc}
                  onChange={e => setEditDesc(e.target.value)}
                  rows={3}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2.5 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-zinc-400 mb-1.5">Icon</label>
                <div className="flex flex-wrap gap-1.5">
                  {PROJECT_EMOJI_OPTIONS.map(emoji => (
                    <button
                      key={emoji}
                      type="button"
                      onClick={() => setEditEmoji(emoji)}
                      className={`text-lg w-9 h-9 rounded-lg flex items-center justify-center transition-all ${
                        editEmoji === emoji
                          ? 'bg-indigo-500/20 ring-2 ring-indigo-500 scale-110'
                          : 'bg-zinc-800 hover:bg-zinc-700'
                      }`}
                    >
                      {emoji}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-zinc-400 mb-1.5">Accent Color</label>
                <div className="flex flex-wrap gap-2">
                  {PROJECT_COLOR_OPTIONS.map(color => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setEditColor(color)}
                      className={`w-8 h-8 rounded-full transition-all ${
                        editColor === color ? 'ring-2 ring-white scale-110' : 'hover:scale-105'
                      }`}
                      style={{ backgroundColor: color }}
                    />
                  ))}
                </div>
              </div>

              {/* Internal Network */}
              <div className="border-t border-zinc-800 pt-5">
                <label className="block text-sm font-medium text-zinc-400 mb-1.5">
                  Internal Network Subnet (CIDR)
                </label>
                <input
                  value={editSubnet}
                  onChange={e => setEditSubnet(e.target.value)}
                  placeholder="172.30.224.0/24 (platform default)"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2.5 font-mono text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
                <p className="text-xs text-zinc-500 mt-2">
                  Docker bridge subnet for this project&apos;s services. Services in
                  the same project talk to each other host-internally on this
                  bridge — no public DNS, no TLS. Leave empty to use the
                  platform default. Applies to the scoped network on the next
                  ecosystem deploy.
                </p>
              </div>

              {/* Team */}
              <div>
                <label className="block text-sm font-medium text-zinc-400 mb-1.5">Team</label>
                <select
                  value={editTeamId || ''}
                  onChange={e => setEditTeamId(e.target.value || null)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2.5 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="">No team (personal project)</option>
                  {teams.map(team => (
                    <option key={team.id} value={team.id}>{team.name} ({team.members_count} members)</option>
                  ))}
                </select>
                <p className="text-xs text-zinc-500 mt-1">Assign this project to a team for shared access.</p>
              </div>
            </div>

            {/* Project Members */}
            <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold text-white flex items-center gap-2">
                  <Users className="w-4 h-4 text-zinc-400" /> Members
                </h3>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={loadMembers}
                  className="text-zinc-400 hover:text-white"
                >
                  <RefreshCcw className="w-3.5 h-3.5 mr-1" /> Refresh
                </Button>
              </div>

              {membersLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
                </div>
              ) : members.length > 0 ? (
                <div className="space-y-2">
                  {members.map(member => (
                    <div
                      key={member.id}
                      className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-indigo-500/10 flex items-center justify-center text-sm font-bold text-indigo-400">
                          {member.username.charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <div className="text-sm font-medium text-white">{member.username}</div>
                          <div className="text-xs text-zinc-500">{member.email}</div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <select
                          value={member.role}
                          onChange={e => handleChangeRole(member.id, e.target.value)}
                          className="rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        >
                          <option value="ADMIN">Admin</option>
                          <option value="MEMBER">Member</option>
                          <option value="VIEWER">Viewer</option>
                        </select>
                        <button
                          onClick={() => handleRemoveMember(member.id, member.username)}
                          className="p-1.5 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-zinc-500 text-center py-4">No members loaded. Click refresh to load project members.</p>
              )}

              {/* Invite */}
              <div className="border-t border-zinc-800 pt-4">
                <label className="block text-sm font-medium text-zinc-400 mb-2">Invite Member</label>
                <div className="flex gap-2">
                  <input
                    value={inviteEmail}
                    onChange={e => setInviteEmail(e.target.value)}
                    placeholder="user@example.com"
                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                  <select
                    value={inviteRole}
                    onChange={e => setInviteRole(e.target.value)}
                    className="rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-2 text-sm text-zinc-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  >
                    <option value="MEMBER">Member</option>
                    <option value="ADMIN">Admin</option>
                    <option value="VIEWER">Viewer</option>
                  </select>
                  <Button
                    onClick={handleInviteMember}
                    disabled={!inviteEmail.trim() || inviting}
                    size="sm"
                    className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold"
                  >
                    {inviting ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
                  </Button>
                </div>
              </div>
            </div>

            {/* Save & Delete */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Button
                  onClick={handleSaveSettings}
                  disabled={saving || !editName.trim()}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold"
                >
                  <Save className="w-4 h-4 mr-1" />
                  {saving ? 'Saving...' : 'Save Changes'}
                </Button>
              </div>
              <Button
                variant="destructive"
                onClick={async () => {
                  if (!await confirm({ title: 'Delete project?', message: `Delete "${project.name}"? All services in this project will be deleted.`, variant: 'destructive', confirmText: 'Delete' })) return;
                  try {
                    await projectsApi.delete(projectId);
                    router.push('/projects');
                  } catch {
                    toast({ title: 'Error', description: 'Failed to delete project', variant: 'destructive' });
                  }
                }}
              >
                <Trash2 className="w-4 h-4 mr-1" /> Delete Project
              </Button>
            </div>
          </motion.div>
        )}

        {/* Add Service Modal */}
        <AnimatePresence>
          {showAddService && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
              onClick={() => setShowAddService(false)}
            >
              <motion.div
                initial={{ scale: 0.9, y: 20 }}
                animate={{ scale: 1, y: 0 }}
                exit={{ scale: 0.9, y: 20 }}
                onClick={e => e.stopPropagation()}
                className="w-full max-w-md rounded-2xl border border-zinc-700/50 bg-zinc-900 p-6 shadow-2xl max-h-[70vh] flex flex-col"
              >
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-bold text-white">
                    Add Service to {project.icon_emoji} {project.name}
                  </h2>
                  <button onClick={() => setShowAddService(false)} className="text-zinc-400 hover:text-white">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <div className="overflow-y-auto flex-1 space-y-2">
                  {allServices.length === 0 ? (
                    <div className="text-center py-10 text-zinc-500 text-sm">
                      No ungrouped services available.
                    </div>
                  ) : (
                    allServices.map(svc => {
                      const statusKey = svc.latest_deployment?.status || 'UNKNOWN';
                      const statusColor = STATUS_COLORS[statusKey] || '#6366f1';
                      return (
                        <button
                          key={svc.id}
                          onClick={() => handleMoveService(svc.id)}
                          className="w-full flex items-center gap-3 rounded-xl border border-zinc-700/50 bg-zinc-800/50 p-3 text-left hover:bg-zinc-700/50 hover:border-zinc-600 transition-all"
                        >
                          <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: statusColor }} />
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-sm text-white truncate">{svc.name}</div>
                            <div className="text-xs text-zinc-500 truncate">
                              {svc.repository_url?.replace(/https?:\/\//, '').replace('.git', '') || svc.deploy_type}
                              {svc.project_name && <span className="ml-2 text-zinc-600">in {svc.project_emoji} {svc.project_name}</span>}
                            </div>
                          </div>
                          <Plus className="w-4 h-4 text-zinc-400" />
                        </button>
                      );
                    })
                  )}
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </DashboardShell>
  );
}

export default function ProjectDetailPage() {
  return (
    <Suspense fallback={
      <DashboardShell>
        <div className="container max-w-6xl py-10 relative z-10">
          <div className="h-8 w-48 bg-zinc-800 rounded-lg animate-pulse mb-6" />
        </div>
      </DashboardShell>
    }>
      <ProjectDetailContent />
    </Suspense>
  );
}
