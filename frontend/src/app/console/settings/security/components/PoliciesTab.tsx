// PoliciesTab Component
// Manages L7 authorization policies for mTLS service-to-service access control.

'use client';

import { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Modal } from '@/components/ui/modal';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Plus, Trash2, Shield, ShieldOff, GripVertical } from 'lucide-react';
import type { MtlsAuthorizationPolicy, MtlsConfig } from '../types';

interface Props {
  policies: MtlsAuthorizationPolicy[] | undefined;
  configs: MtlsConfig[] | undefined;
  isPoliciesLoading: boolean;
  onCreatePolicy: (data: {
    name: string;
    source_spiffe_id: string;
    target_service_id: string;
    paths: string[];
    methods: string[];
    action: 'allow' | 'deny';
    priority: number;
  }) => void;
  onUpdatePolicy: (data: { id: number; enabled: boolean }) => void;
  onDeletePolicy: (id: number) => void;
  isMutating: boolean;
}

export function PoliciesTab({
  policies,
  configs,
  isPoliciesLoading,
  onCreatePolicy,
  onUpdatePolicy,
  onDeletePolicy,
  isMutating,
}: Props) {
  const [showCreate, setShowCreate] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    source_spiffe_id: '',
    target_service_id: '',
    paths: '',
    methods: '',
    action: 'allow' as 'allow' | 'deny',
    priority: 0,
  });

  const handleCreate = () => {
    const paths = formData.paths
      .split(',')
      .map((p) => p.trim())
      .filter(Boolean);
    const methods = formData.methods
      .split(',')
      .map((m) => m.trim().toUpperCase())
      .filter(Boolean);

    onCreatePolicy({
      name: formData.name,
      source_spiffe_id: formData.source_spiffe_id,
      target_service_id: formData.target_service_id,
      paths,
      methods,
      action: formData.action,
      priority: formData.priority,
    });
    setShowCreate(false);
    setFormData({
      name: '',
      source_spiffe_id: '',
      target_service_id: '',
      paths: '',
      methods: '',
      action: 'allow',
      priority: 0,
    });
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Authorization Policies</h3>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Control which services can call which endpoints. Policies are evaluated by priority (highest first).
          </p>
        </div>
        <Button onClick={() => setShowCreate(true)} size="sm">
          <Plus className="h-4 w-4 mr-1" />
          Add Policy
        </Button>
      </div>

      {/* Policy List */}
      {isPoliciesLoading ? (
        <div className="space-y-3">
          {[1, 2].map((i) => (
            <div key={i} className="h-20 rounded-lg animate-pulse" style={{ backgroundColor: 'var(--bg-secondary)' }} />
          ))}
        </div>
      ) : policies && policies.length > 0 ? (
        <div className="space-y-2">
          {policies.map((policy) => (
            <Card key={policy.id}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      {policy.action === 'allow' ? (
                        <Shield className="h-4 w-4 text-emerald-500" />
                      ) : (
                        <ShieldOff className="h-4 w-4 text-red-500" />
                      )}
                      <span className="text-sm font-semibold">{policy.name}</span>
                      <Badge variant={policy.action === 'allow' ? 'default' : 'destructive'}>
                        {policy.action.toUpperCase()}
                      </Badge>
                      {!policy.enabled && <Badge variant="secondary">Disabled</Badge>}
                    </div>
                    <div className="text-xs font-mono mb-1" style={{ color: 'var(--text-secondary)' }}>
                      {policy.source_spiffe_id} → {policy.target_service_name}
                    </div>
                    {policy.paths.length > 0 && (
                      <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                        Paths: {policy.paths.join(', ')}
                      </div>
                    )}
                    {policy.methods.length > 0 && (
                      <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                        Methods: {policy.methods.join(', ')}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                      P{policy.priority}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onUpdatePolicy({ id: policy.id, enabled: !policy.enabled })}
                      disabled={isMutating}
                    >
                      {policy.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onDeletePolicy(policy.id)}
                      disabled={isMutating}
                    >
                      <Trash2 className="h-4 w-4 text-red-500" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div
          className="text-center py-12 rounded-lg border-2 border-dashed"
          style={{ borderColor: 'var(--border)' }}
        >
          <Shield className="h-12 w-12 mx-auto mb-3" style={{ color: 'var(--text-secondary)' }} />
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            No authorization policies yet. Add a policy to control service-to-service access.
          </p>
        </div>
      )}

      {/* Create Policy Modal */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="Create Authorization Policy" size="md">
        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Policy Name</label>
            <Input
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="e.g., Allow frontend to call API"
            />
          </div>

          <div>
            <label className="text-sm font-medium">Source SPIFFE ID</label>
            <Input
              value={formData.source_spiffe_id}
              onChange={(e) => setFormData({ ...formData, source_spiffe_id: e.target.value })}
              placeholder="spiffe://ecosystem.local/service/frontend or *"
            />
            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              Use * to match any source service
            </p>
          </div>

          <div>
            <label className="text-sm font-medium">Target Service</label>
            <select
              className="w-full rounded-md border px-3 py-2 text-sm"
              style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
              value={formData.target_service_id}
              onChange={(e) => setFormData({ ...formData, target_service_id: e.target.value })}
            >
              <option value="">Select a service</option>
              {configs?.map((c) => (
                <option key={c.service_id} value={c.service_id}>
                  {c.service_name}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Path Prefixes</label>
              <Input
                value={formData.paths}
                onChange={(e) => setFormData({ ...formData, paths: e.target.value })}
                placeholder="/api/, /internal/"
              />
              <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                Comma-separated, empty = all paths
              </p>
            </div>
            <div>
              <label className="text-sm font-medium">HTTP Methods</label>
              <Input
                value={formData.methods}
                onChange={(e) => setFormData({ ...formData, methods: e.target.value })}
                placeholder="GET, POST"
              />
              <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                Comma-separated, empty = all methods
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Action</label>
              <select
                className="w-full rounded-md border px-3 py-2 text-sm"
                style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
                value={formData.action}
                onChange={(e) => setFormData({ ...formData, action: e.target.value as 'allow' | 'deny' })}
              >
                <option value="allow">Allow</option>
                <option value="deny">Deny</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Priority</label>
              <Input
                type="number"
                value={formData.priority}
                onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 0 })}
              />
              <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                Higher = evaluated first
              </p>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!formData.name || !formData.source_spiffe_id || !formData.target_service_id || isMutating}
            >
              {isMutating ? 'Creating...' : 'Create Policy'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
