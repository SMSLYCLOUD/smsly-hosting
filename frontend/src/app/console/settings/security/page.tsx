// mTLS Security Settings Page
// Main page for managing SPIFFE mTLS, authorization policies, and Envoy sidecar.

'use client';

import { useState } from 'react';
import { Shield, Settings, FileText } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { MtlsHealthCard } from './components/MtlsHealthCard';
import { ServiceMtlsCard } from './components/ServiceMtlsCard';
import { PoliciesTab } from './components/PoliciesTab';
import { useMtls } from './hooks/useMtls';

type Tab = 'services' | 'policies';

export default function MtlsSettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('services');
  const {
    health,
    configs,
    enableMtls,
    disableMtls,
    policies,
    createPolicy,
    updatePolicy,
    deletePolicy,
  } = useMtls();

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Shield className="h-6 w-6 text-emerald-500" />
          Security & mTLS
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          Manage SPIFFE mTLS for inter-service authentication, authorization policies,
          and Envoy sidecar proxy.
        </p>
      </div>

      {/* Platform Health */}
      <MtlsHealthCard health={health.data} isLoading={health.isLoading} />

      {/* Tab Navigation */}
      <div className="flex gap-1 p-1 rounded-lg" style={{ backgroundColor: 'var(--bg-secondary)' }}>
        <button
          onClick={() => setActiveTab('services')}
          className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            activeTab === 'services' ? 'bg-white shadow-sm' : 'hover:bg-white/50'
          }`}
        >
          <Settings className="h-4 w-4" />
          Services
        </button>
        <button
          onClick={() => setActiveTab('policies')}
          className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            activeTab === 'policies' ? 'bg-white shadow-sm' : 'hover:bg-white/50'
          }`}
        >
          <FileText className="h-4 w-4" />
          Policies
        </button>
      </div>

      {/* Tab Content */}
      {activeTab === 'services' && (
        <div>
          <h2 className="text-lg font-semibold mb-3">Services</h2>

          {configs.isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : configs.data && configs.data.length > 0 ? (
            <div className="space-y-3">
              {configs.data.map((config) => (
                <ServiceMtlsCard
                  key={config.service_id}
                  config={config}
                  onEnable={(id) => enableMtls.mutate(id)}
                  onDisable={(id) => disableMtls.mutate(id)}
                  isToggling={enableMtls.isPending || disableMtls.isPending}
                />
              ))}
            </div>
          ) : (
            <div
              className="text-center py-12 rounded-lg border-2 border-dashed"
              style={{ borderColor: 'var(--border)' }}
            >
              <Shield className="h-12 w-12 mx-auto mb-3" style={{ color: 'var(--text-secondary)' }} />
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                No services deployed yet. Deploy a service to enable mTLS.
              </p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'policies' && (
        <PoliciesTab
          policies={policies.data}
          configs={configs.data}
          isPoliciesLoading={policies.isLoading}
          onCreatePolicy={(data) => createPolicy.mutate(data)}
          onUpdatePolicy={(data) => updatePolicy.mutate(data)}
          onDeletePolicy={(id) => deletePolicy.mutate(id)}
          isMutating={createPolicy.isPending || updatePolicy.isPending || deletePolicy.isPending}
        />
      )}

      {/* Info Section */}
      <div
        className="rounded-lg p-4 text-sm"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <h3 className="font-semibold mb-2">How mTLS Works</h3>
        <ul className="space-y-1" style={{ color: 'var(--text-secondary)' }}>
          <li>• Each service gets a SPIFFE ID: <code className="text-xs">spiffe://&lt;trust-domain&gt;/service/&lt;name&gt;</code></li>
          <li>• Certificates auto-rotate every hour via SPIRE agent</li>
          <li>• No shared secrets — identity is cryptographic (X.509)</li>
          <li>• Works with any language: Python, Node.js, Go, Rust, etc.</li>
          <li>• File-based SVIDs at <code className="text-xs">/opt/spire/svids/</code></li>
          <li>• Optional Envoy sidecar for transparent mTLS (no app changes needed)</li>
          <li>• Authorization policies control service-to-service access</li>
        </ul>
      </div>
    </div>
  );
}
