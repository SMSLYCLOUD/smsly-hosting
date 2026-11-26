'use client';

/**
 * Projects page — Railway-style grid of project cards.
 * Each card shows emoji icon, name, service count, and latest deploy status.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { projectsApi, Project } from '@/lib/api';
import { PROJECT_EMOJI_OPTIONS, PROJECT_COLOR_OPTIONS } from '@/lib/project-constants';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { motion } from 'framer-motion';
import { Plus, FolderOpen, Settings2, Trash2, Palette } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#10b981',
  BUILDING: '#3b82f6',
  DEPLOYING: '#818cf8',
  QUEUED: '#fbbf24',
  FAILED: '#ef4444',
};

export default function ProjectsPage() {
  const router = useRouter();
  const confirm = useConfirm();
  const { toast } = useToast();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newEmoji, setNewEmoji] = useState('📦');
  const [newColor, setNewColor] = useState('#6366f1');
  const [creating, setCreating] = useState(false);

  const loadProjects = useCallback(async () => {
    try {
      const data = await projectsApi.list();
      setProjects(data);
    } catch (err) {
      console.error('Failed to load projects:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const project = await projectsApi.create({
        name: newName.trim(),
        description: newDesc.trim(),
        icon_emoji: newEmoji,
        color: newColor,
      });
      setProjects(prev => [project, ...prev]);
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      setNewEmoji('📦');
      setNewColor('#6366f1');
      toast({ title: 'Project created', description: `${project.icon_emoji} ${project.name}` });
    } catch (err: any) {
      toast({ title: 'Error', description: err?.response?.data?.name?.[0] || 'Failed to create project', variant: 'destructive' });
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    if (!await confirm({
      title: 'Delete project?',
      message: `Delete project "${project.name}"? All services in this project will be deleted.`,
      variant: 'destructive',
      confirmText: 'Delete',
      requireMatchText: project.name,
    })) return;
    try {
      await projectsApi.delete(project.id);
      setProjects(prev => prev.filter(p => p.id !== project.id));
      toast({ title: 'Project deleted' });
    } catch {
      toast({ title: 'Error', description: 'Failed to delete project', variant: 'destructive' });
    }
  };

  const formatTimeAgo = (dateStr?: string | null) => {
    if (!dateStr) return null;
    const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };

  return (
    <DashboardShell>
      <div className="container max-w-6xl py-10 relative z-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Projects</h1>
            <p className="text-muted-foreground">
              Organize your services into projects for a cleaner workflow.
            </p>
          </div>
          <Button
            onClick={() => setShowCreate(true)}
            className="bg-gradient-to-r from-emerald-500 to-green-600 hover:from-emerald-600 hover:to-green-700 text-white font-bold shadow-lg shadow-emerald-500/20 transition-all hover:scale-105"
          >
            <Plus className="w-4 h-4 mr-2" />
            New Project
          </Button>
        </div>

        {/* Create Modal */}
        <Dialog open={showCreate} onOpenChange={setShowCreate}>
          <DialogContent className="sm:max-w-md rounded-2xl border-border bg-card text-card-foreground p-6 shadow-2xl">
            <DialogHeader className="mb-2">
              <DialogTitle className="text-lg font-bold text-foreground">Create Project</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground">
                Configure a name, icon, and accent color for your new project.
              </DialogDescription>
            </DialogHeader>

            {/* Name */}
            <div>
              <Label htmlFor="create-project-name" className="block text-sm font-medium text-muted-foreground mb-1.5">
                Name
              </Label>
              <input
                id="create-project-name"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="My Awesome Project"
                autoFocus
                className="w-full rounded-lg border border-input bg-background px-3 py-2.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>

            {/* Description */}
            <div>
              <Label htmlFor="create-project-desc" className="block text-sm font-medium text-muted-foreground mb-1.5">
                Description <span className="text-muted-foreground/70">(optional)</span>
              </Label>
              <input
                id="create-project-desc"
                value={newDesc}
                onChange={e => setNewDesc(e.target.value)}
                placeholder="What is this project about?"
                className="w-full rounded-lg border border-input bg-background px-3 py-2.5 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>

            {/* Emoji picker */}
            <div>
              <span className="block text-sm font-medium text-muted-foreground mb-1.5">Icon</span>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Project icon">
                {PROJECT_EMOJI_OPTIONS.map(emoji => (
                  <button
                    key={emoji}
                    type="button"
                    aria-label={`Select icon ${emoji}`}
                    aria-pressed={newEmoji === emoji}
                    onClick={() => setNewEmoji(emoji)}
                    className={`text-xl w-10 h-10 rounded-lg flex items-center justify-center transition-all ${
                      newEmoji === emoji
                        ? 'bg-primary/20 ring-2 ring-primary scale-110'
                        : 'bg-muted hover:bg-muted/80'
                    }`}
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            </div>

            {/* Color picker */}
            <div>
              <span className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground mb-1.5">
                <Palette className="w-3.5 h-3.5" aria-hidden="true" /> Accent Color
              </span>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Project accent color">
                {PROJECT_COLOR_OPTIONS.map(color => (
                  <button
                    key={color}
                    type="button"
                    aria-label={`Select color ${color}`}
                    aria-pressed={newColor === color}
                    onClick={() => setNewColor(color)}
                    className={`w-8 h-8 rounded-full transition-all ${
                      newColor === color ? 'ring-2 ring-foreground scale-110' : 'hover:scale-105'
                    }`}
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </div>

            {/* Preview */}
            <div className="rounded-xl border border-border bg-muted/50 p-4 flex items-center gap-3">
              <span className="text-2xl">{newEmoji}</span>
              <div className="min-w-0">
                <div className="font-semibold text-foreground truncate">{newName || 'Project Name'}</div>
                <div className="text-xs text-muted-foreground truncate">{newDesc || 'No description'}</div>
              </div>
              <div className="ml-auto w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: newColor }} />
            </div>

            <div className="flex gap-3 pt-1">
              <Button variant="ghost" onClick={() => setShowCreate(false)} className="flex-1">Cancel</Button>
              <Button
                onClick={handleCreate}
                disabled={!newName.trim() || creating}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-bold"
              >
                {creating ? 'Creating...' : 'Create Project'}
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        {/* Loading state */}
        {loading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="h-40 rounded-2xl bg-muted/40 animate-pulse border border-border" />
            ))}
          </div>
        )}

        {/* Empty state */}
        {!loading && projects.length === 0 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center justify-center py-24 text-center"
          >
            <div className="text-6xl mb-4">📂</div>
            <h2 className="text-xl font-bold text-foreground mb-2">No Projects Yet</h2>
            <p className="text-muted-foreground mb-6 max-w-md">
              Projects help you organize related services together. Create your first project to get started.
            </p>
            <Button
              onClick={() => setShowCreate(true)}
              className="bg-gradient-to-r from-emerald-500 to-green-600 text-white font-bold"
            >
              <Plus className="w-4 h-4 mr-2" />
              Create First Project
            </Button>
          </motion.div>
        )}

        {/* Project cards */}
        {!loading && projects.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {projects.map((project, i) => {
              const deployColor = STATUS_COLORS[project.latest_deploy_status || ''] || '#6366f1';
              return (
                <motion.div
                  key={project.id}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.05 }}
                  onClick={() => router.push(`/projects/${project.id}`)}
                  className="group relative rounded-2xl border border-border bg-card/80 backdrop-blur-sm p-5 cursor-pointer transition-all duration-200 hover:border-primary/40 hover:bg-card hover:shadow-xl hover:shadow-black/10 hover:-translate-y-0.5"
                >
                  {/* Color accent bar */}
                  <div
                    className="absolute top-0 left-0 w-full h-1 rounded-t-2xl opacity-60 group-hover:opacity-100 transition-opacity"
                    style={{ background: `linear-gradient(90deg, ${project.color}, ${project.color}00)` }}
                  />

                  {/* Header */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div
                        className="w-11 h-11 rounded-xl flex items-center justify-center text-xl"
                        style={{ backgroundColor: project.color + '20' }}
                      >
                        {project.icon_emoji}
                      </div>
                      <div>
                        <h3 className="font-semibold text-foreground group-hover:text-primary transition-colors">
                          {project.name}
                        </h3>
                        <div className="text-xs text-muted-foreground">{project.slug}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                      <button
                        type="button"
                        aria-label={`Project settings for ${project.name}`}
                        onClick={e => { e.stopPropagation(); router.push(`/projects/${project.id}?tab=settings`); }}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
                      >
                        <Settings2 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete project ${project.name}`}
                        onClick={e => handleDelete(e, project)}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-red-500/10"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Description */}
                  {project.description && (
                    <p className="text-xs text-muted-foreground mb-3 line-clamp-2">{project.description}</p>
                  )}

                  {/* Stats row */}
                  <div className="flex items-center justify-between border-t border-border pt-3 mt-auto">
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <FolderOpen className="w-3.5 h-3.5" />
                        {project.services_count} service{project.services_count !== 1 ? 's' : ''}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {project.latest_deploy_status && (
                        <>
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: deployColor }} />
                          <span>{project.latest_deploy_status.toLowerCase()}</span>
                        </>
                      )}
                      {project.latest_deploy_at && (
                        <span>· {formatTimeAgo(project.latest_deploy_at)}</span>
                      )}
                    </div>
                  </div>

                  {/* Default badge */}
                  {project.is_default && (
                    <div className="absolute top-3 right-3 bg-indigo-500/20 text-indigo-400 text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full">
                      default
                    </div>
                  )}
                </motion.div>
              );
            })}
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
