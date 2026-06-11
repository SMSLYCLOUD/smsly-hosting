'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  CheckCircle2,
  XCircle,
  RefreshCcw,
  Loader2,
  User,
  Clock,
  FileX2,
  ShieldCheck,
  ThumbsUp,
  ThumbsDown,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/use-toast';
import { deploymentApprovalApi, DeploymentApproval } from '@/lib/api';

interface DeploymentApprovalsPanelProps {
  serviceId: string;
}

export const DeploymentApprovalsPanel: React.FC<DeploymentApprovalsPanelProps> = ({ serviceId }) => {
  const [approvals, setApprovals] = useState<DeploymentApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectApprovalId, setRejectApprovalId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const fetchApprovals = useCallback(async () => {
    try {
      const data = await deploymentApprovalApi.list(serviceId);
      setApprovals(data);
    } catch {
      setApprovals([]);
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals]);

  const handleApprove = async (approvalId: string) => {
    setActionLoading(approvalId);
    try {
      await deploymentApprovalApi.approve(serviceId, approvalId);
      toast({ title: 'Approved', description: 'Deployment approval has been granted.' });
      fetchApprovals();
    } catch {
      toast({ title: 'Action failed', description: 'Could not approve deployment.', variant: 'destructive' });
    } finally {
      setActionLoading(null);
    }
  };

  const openRejectDialog = (approvalId: string) => {
    setRejectApprovalId(approvalId);
    setRejectReason('');
    setRejectDialogOpen(true);
  };

  const handleReject = async () => {
    if (!rejectApprovalId) return;
    setActionLoading(rejectApprovalId);
    try {
      await deploymentApprovalApi.reject(serviceId, rejectApprovalId, rejectReason || undefined);
      toast({ title: 'Rejected', description: 'Deployment approval has been rejected.' });
      setRejectDialogOpen(false);
      setRejectApprovalId(null);
      setRejectReason('');
      fetchApprovals();
    } catch {
      toast({ title: 'Action failed', description: 'Could not reject deployment.', variant: 'destructive' });
    } finally {
      setActionLoading(null);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'pending':
        return <Badge variant="warning">Pending</Badge>;
      case 'approved':
        return <Badge variant="success">Approved</Badge>;
      case 'rejected':
        return <Badge variant="destructive">Rejected</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-primary" />
            Deployment Approvals
          </h3>
          <p className="text-sm text-muted-foreground mt-1">
            Review and approve or reject pending deployment requests.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchApprovals} disabled={loading} className="gap-2">
          <RefreshCcw className="w-4 h-4" />
          Refresh
        </Button>
      </div>

      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="p-5">
                <div className="flex items-center gap-4">
                  <Skeleton className="h-10 w-10 rounded-full" />
                  <div className="space-y-2 flex-1">
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                  <Skeleton className="h-8 w-20 rounded-full" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : approvals.length === 0 ? (
        <div className="text-center py-16 bg-muted/30 border border-dashed border-border rounded-xl">
          <FileX2 className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
          <p className="text-muted-foreground text-sm font-medium">No pending approvals.</p>
          <p className="text-muted-foreground/60 text-xs mt-1">
            All deployment requests have been reviewed.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {approvals.map((approval) => (
            <Card key={approval.id} className="overflow-hidden">
              <CardContent className="p-5">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="flex items-start gap-4">
                    <div className="mt-1 p-2 bg-primary/10 rounded-lg">
                      <User className="w-5 h-5 text-primary" />
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-foreground">{approval.requester}</span>
                        {getStatusBadge(approval.status)}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5" />
                          {new Date(approval.requested_at).toLocaleString()}
                        </span>
                        {approval.environment && (
                          <span className="flex items-center gap-1">
                            <ShieldCheck className="w-3.5 h-3.5" />
                            {approval.environment}
                          </span>
                        )}
                      </div>
                      {approval.reason && (
                        <p className="text-xs text-muted-foreground/70 mt-1 italic">
                          &ldquo;{approval.reason}&rdquo;
                        </p>
                      )}
                      {approval.status === 'approved' && approval.approved_by && (
                        <p className="text-xs text-emerald-500/70 mt-1">
                          Approved by {approval.approved_by}
                          {approval.approved_at ? ` — ${new Date(approval.approved_at).toLocaleString()}` : ''}
                        </p>
                      )}
                      {approval.status === 'rejected' && approval.rejected_by && (
                        <p className="text-xs text-red-500/70 mt-1">
                          Rejected by {approval.rejected_by}
                          {approval.rejected_at ? ` — ${new Date(approval.rejected_at).toLocaleString()}` : ''}
                        </p>
                      )}
                    </div>
                  </div>

                  {approval.status === 'pending' && (
                    <div className="flex items-center gap-2 shrink-0">
                      <Button
                        variant="default"
                        size="sm"
                        className="gap-2 bg-emerald-600 hover:bg-emerald-700 text-white"
                        onClick={() => handleApprove(approval.id)}
                        disabled={actionLoading === approval.id}
                      >
                        {actionLoading === approval.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <ThumbsUp className="w-4 h-4" />
                        )}
                        Approve
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        className="gap-2"
                        onClick={() => openRejectDialog(approval.id)}
                        disabled={actionLoading === approval.id}
                      >
                        <ThumbsDown className="w-4 h-4" />
                        Reject
                      </Button>
                    </div>
                  )}

                  {approval.status === 'approved' && (
                    <CheckCircle2 className="w-6 h-6 text-emerald-500 shrink-0" />
                  )}
                  {approval.status === 'rejected' && (
                    <XCircle className="w-6 h-6 text-red-500 shrink-0" />
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Reject Dialog */}
      <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject Deployment</DialogTitle>
            <DialogDescription>
              Provide a reason for rejecting this deployment request.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Reason for rejection (optional)..."
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            rows={3}
          />
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setRejectDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleReject}
              disabled={actionLoading === rejectApprovalId}
              className="gap-2"
            >
              {actionLoading === rejectApprovalId ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ThumbsDown className="w-4 h-4" />
              )}
              Reject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};
