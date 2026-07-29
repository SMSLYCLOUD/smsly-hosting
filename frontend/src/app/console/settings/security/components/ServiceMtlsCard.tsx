// ServiceMtlsCard Component
// Shows mTLS status for a single service with enable/disable toggle.

'use client';

import { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Modal } from '@/components/ui/modal';
import { Shield, ShieldOff, Clock, RefreshCw } from 'lucide-react';
import type { MtlsConfig } from '../types';

interface Props {
  config: MtlsConfig;
  onEnable: (serviceId: string) => void;
  onDisable: (serviceId: string) => void;
  isToggling: boolean;
}

export function ServiceMtlsCard({ config, onEnable, onDisable, isToggling }: Props) {
  const [showConfirm, setShowConfirm] = useState(false);

  const svidExpiry = config.svid_expiry ? new Date(config.svid_expiry) : null;
  const now = new Date();
  const ttlRemaining = svidExpiry ? Math.max(0, svidExpiry.getTime() - now.getTime()) : 0;
  const ttlHours = Math.floor(ttlRemaining / (1000 * 60 * 60));
  const ttlMinutes = Math.floor((ttlRemaining % (1000 * 60 * 60)) / (1000 * 60));
  const ttlPercent = svidExpiry ? Math.min(100, (ttlRemaining / (3600 * 1000)) * 100) : 0;

  const handleToggle = () => {
    if (config.mtls_enabled) {
      setShowConfirm(true);
    } else {
      onEnable(config.service_id);
    }
  };

  const confirmDisable = () => {
    onDisable(config.service_id);
    setShowConfirm(false);
  };

  return (
    <>
      <Card>
        <CardContent className="p-4">
          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0">
              {/* Service Name */}
              <div className="flex items-center gap-2 mb-2">
                {config.mtls_enabled ? (
                  <Shield className="h-4 w-4 text-emerald-500 flex-shrink-0" />
                ) : (
                  <ShieldOff className="h-4 w-4 flex-shrink-0" style={{ color: 'var(--text-secondary)' }} />
                )}
                <h3 className="text-sm font-semibold truncate">{config.service_name}</h3>
                <Badge variant={config.mtls_enabled ? 'default' : 'secondary'}>
                  {config.mtls_enabled ? 'Active' : 'Disabled'}
                </Badge>
              </div>

              {/* SPIFFE ID */}
              <p className="text-xs font-mono truncate mb-2" style={{ color: 'var(--text-secondary)' }}>
                {config.spiffe_id}
              </p>

              {/* SVID Expiry */}
              {config.mtls_enabled && svidExpiry && (
                <div className="mb-2">
                  <div className="flex items-center gap-1 mb-1">
                    <Clock className="h-3 w-3" style={{ color: 'var(--text-secondary)' }} />
                    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                      SVID expires in {ttlHours}h {ttlMinutes}m
                    </span>
                  </div>
                  <Progress
                    value={ttlPercent}
                    className={`h-1.5 ${config.is_svid_expired ? 'text-red-500' : 'text-emerald-500'}`}
                  />
                </div>
              )}

              {/* Last Rotation */}
              {config.last_rotation && (
                <div className="flex items-center gap-1">
                  <RefreshCw className="h-3 w-3" style={{ color: 'var(--text-secondary)' }} />
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                    Last rotated: {new Date(config.last_rotation).toLocaleString()}
                  </span>
                </div>
              )}
            </div>

            {/* Toggle Button */}
            <Button
              size="sm"
              variant={config.mtls_enabled ? 'outline' : 'default'}
              onClick={handleToggle}
              disabled={isToggling}
              className="ml-4 flex-shrink-0"
            >
              {isToggling ? 'Updating...' : config.mtls_enabled ? 'Disable' : 'Enable'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Disable Confirmation Modal */}
      <Modal
        isOpen={showConfirm}
        onClose={() => setShowConfirm(false)}
        title="Disable mTLS?"
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Disabling mTLS for <strong>{config.service_name}</strong> will remove SPIRE socket
            mounting on next deploy. The service will lose cryptographic identity and
            inter-service encryption.
          </p>
          <p className="text-sm font-mono" style={{ color: 'var(--text-secondary)' }}>
            {config.spiffe_id}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setShowConfirm(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDisable} disabled={isToggling}>
              {isToggling ? 'Disabling...' : 'Disable mTLS'}
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
