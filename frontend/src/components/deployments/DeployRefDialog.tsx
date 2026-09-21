'use client';

import React, { useState, useEffect } from 'react';
import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Loader2, Rocket } from 'lucide-react';
import { listBranches, listCommits, GitRefCommit } from '@/lib/gitRefs';

interface DeployRefDialogProps {
  isOpen: boolean;
  onClose: () => void;
  repositoryUrl: string;
  defaultBranch: string;
  deploying: boolean;
  onDeploy: (ref: string) => void;
}

/** Deploy a service from a chosen branch and optional pinned commit. */
export function DeployRefDialog({
  isOpen, onClose, repositoryUrl, defaultBranch, deploying, onDeploy,
}: DeployRefDialogProps) {
  const [branch, setBranch] = useState(defaultBranch || 'main');
  const [branches, setBranches] = useState<{ name: string; sha?: string }[]>([]);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [commits, setCommits] = useState<GitRefCommit[]>([]);
  const [loadingCommits, setLoadingCommits] = useState(false);
  const [commitSha, setCommitSha] = useState('');

  // Reset + load branches whenever the dialog opens.
  useEffect(() => {
    if (!isOpen) return;
    setBranch(defaultBranch || 'main');
    setCommitSha('');
    setCommits([]);
    if (!repositoryUrl) return;
    setLoadingBranches(true);
    listBranches(repositoryUrl)
      .then((b) => {
        setBranches(b);
        if (b.length > 0 && !b.some((x) => x.name === (defaultBranch || 'main'))) {
          setBranch(b[0].name);
        }
      })
      .finally(() => setLoadingBranches(false));
  }, [isOpen, repositoryUrl, defaultBranch]);

  // Load commits whenever the branch changes.
  useEffect(() => {
    if (!isOpen || !repositoryUrl || !branch) { setCommits([]); return; }
    setLoadingCommits(true);
    setCommitSha('');
    listCommits(repositoryUrl, branch)
      .then((c) => setCommits(c.slice(0, 30)))
      .finally(() => setLoadingCommits(false));
  }, [isOpen, repositoryUrl, branch]);

  const selectedCommit = commits.find((c) => c.sha === commitSha);
  const effectiveRef = commitSha || branch || 'HEAD';

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Deploy service" size="md">
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Branch</Label>
          {branches.length > 0 ? (
            <select
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {branches.map((b) => (
                <option key={b.name} value={b.name}>{b.name}</option>
              ))}
            </select>
          ) : (
            <input
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              placeholder={loadingBranches ? 'Loading branches...' : 'main'}
              disabled={loadingBranches}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          )}
        </div>
        <div className="space-y-2">
          <Label>Commit (optional — defaults to branch HEAD)</Label>
          <select
            value={commitSha}
            onChange={(e) => setCommitSha(e.target.value)}
            disabled={loadingCommits || commits.length === 0}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:opacity-50"
          >
            <option value="">Latest ({branch || 'HEAD'})</option>
            {commits.map((c) => (
              <option key={c.sha} value={c.sha}>
                {c.sha.slice(0, 8)} — {c.message.slice(0, 60)}{c.author ? ` (${c.author})` : ''}
              </option>
            ))}
          </select>
          {loadingCommits && <p className="text-xs text-muted-foreground">Loading commits…</p>}
          {selectedCommit?.date && (
            <p className="text-xs text-muted-foreground">{new Date(selectedCommit.date).toLocaleString()}</p>
          )}
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={deploying}>Cancel</Button>
          <Button onClick={() => onDeploy(effectiveRef)} disabled={deploying || !effectiveRef}>
            {deploying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Rocket className="mr-2 h-4 w-4" />}
            {deploying ? 'Deploying…' : `Deploy ${commitSha ? commitSha.slice(0, 8) : branch}`}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
