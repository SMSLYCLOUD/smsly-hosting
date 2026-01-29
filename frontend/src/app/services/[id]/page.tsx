'use client';

import { useEffect, useState, useRef } from 'react';
import { servicesApi, Service, Deployment, EnvVar } from '@/lib/api';
import { useParams } from 'next/navigation';
import { ServiceLayout } from '@/components/layout/ServiceLayout';
import { Activity, Shield, Terminal, Zap } from 'lucide-react';
import Editor from "@monaco-editor/react";

export default function ServiceDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [service, setService] = useState<Service | null>(null);
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [activeTab, setActiveTab] = useState('overview');
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const s = await servicesApi.get(id);
        setService(s);
        if (s.latest_deployment) {
            const d = await servicesApi.getDeployment(s.latest_deployment.id);
            setDeployment(d);
        }
      } catch (err) { console.error(err); }
    };
    fetchData();
  }, [id]);

  if (!service) return <div className="h-screen flex items-center justify-center bg-background text-muted-foreground">Loading...</div>;

  return (
    <ServiceLayout service={service} activeTab={activeTab} setActiveTab={setActiveTab}>
      {activeTab === 'overview' && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-4">
              {/* Stats Cards */}
              <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                  <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Uptime</h4>
                  <p className="text-3xl font-bold text-foreground">99.9%</p>
                  <p className="text-xs text-emerald-500 mt-2 font-medium">+0.1% vs last week</p>
              </div>
              <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                  <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Avg Latency</h4>
                  <p className="text-3xl font-bold text-foreground">45ms</p>
                  <p className="text-xs text-muted-foreground mt-2 font-medium">Global CDN</p>
              </div>
              <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                  <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Error Rate</h4>
                  <p className="text-3xl font-bold text-foreground">0.01%</p>
                  <p className="text-xs text-emerald-500 mt-2 font-medium">All systems normal</p>
              </div>

              <div className="col-span-1 md:col-span-2 bg-card border border-border p-8 rounded-xl shadow-sm h-fit">
                  <h3 className="font-bold mb-6 text-lg text-foreground">Configuration</h3>
                  <div className="space-y-5 text-sm">
                      <div className="flex justify-between border-b border-border pb-3">
                          <span className="text-muted-foreground font-medium">Repository</span>
                          <span className="font-mono text-foreground">{service.repository_url}</span>
                      </div>
                      <div className="flex justify-between border-b border-border pb-3">
                          <span className="text-muted-foreground font-medium">Branch</span>
                          <span className="font-mono bg-muted px-2 py-1 rounded text-foreground">{service.branch}</span>
                      </div>
                      <div className="flex justify-between border-b border-border pb-3">
                          <span className="text-muted-foreground font-medium">Resources</span>
                          <span className="text-foreground">{service.cpu_cores} vCPU / {service.memory_mb} MB</span>
                      </div>
                      <div className="flex justify-between border-b border-border pb-3">
                          <span className="text-muted-foreground font-medium">Internal DNS</span>
                          <span className="font-mono text-primary bg-primary/10 px-2 py-1 rounded">{service.name}.default.svc</span>
                      </div>
                  </div>
              </div>

              <div className="bg-card border border-border p-8 rounded-xl shadow-sm h-fit">
                  <h3 className="font-bold mb-6 text-lg text-foreground">Latest Deployment</h3>
                  {deployment ? (
                      <div className="space-y-5 text-sm">
                          <div className="flex justify-between items-center border-b border-border pb-3">
                              <span className="text-muted-foreground font-medium">Commit</span>
                              <span className="font-mono bg-muted border border-border px-2 py-1 rounded text-foreground font-bold">{deployment.commit_hash.substring(0,7)}</span>
                          </div>
                          <div className="flex justify-between items-center border-b border-border pb-3">
                              <span className="text-muted-foreground font-medium">Status</span>
                              <span className={`font-bold px-2 py-1 rounded text-xs uppercase ${
                                  deployment.status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-yellow-500/10 text-yellow-500'
                              }`}>{deployment.status}</span>
                          </div>
                          <div className="pt-2">
                              <button className="w-full border border-border hover:border-foreground/20 hover:bg-muted text-foreground font-bold py-2 rounded-lg transition-all text-sm">
                                  View Logs
                              </button>
                          </div>
                      </div>
                  ) : <p className="text-muted-foreground">No deployment found.</p>}
              </div>
          </div>
      )}

      {activeTab === 'logs' && (
          <div className="bg-[#09090b] border border-border rounded-xl overflow-hidden font-mono text-xs h-[700px] flex flex-col shadow-2xl">
              <div className="bg-white/5 p-3 border-b border-white/10 flex justify-between items-center">
                  <div className="flex gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500/80" />
                    <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                    <div className="w-3 h-3 rounded-full bg-green-500/80" />
                  </div>
                  <div className="text-zinc-500 font-sans text-xs">live tail</div>
              </div>
              <div className="flex-1 p-6 overflow-y-auto text-zinc-300 leading-relaxed">
                  {deployment?.ai_diagnosis && (
                      <div className="bg-indigo-500/10 border-l-2 border-indigo-500 p-4 mb-6 text-indigo-200 rounded-r-lg">
                          <strong className="flex items-center gap-2 mb-2 text-indigo-400 font-sans uppercase tracking-wider text-[10px]">
                              <Zap size={12} /> AI Insight
                          </strong>
                          {deployment.ai_diagnosis}
                      </div>
                  )}
                  <pre className="whitespace-pre-wrap font-mono">{deployment?.build_logs || 'Waiting for logs...'}</pre>
                  <div ref={logsEndRef} />
              </div>
          </div>
      )}

      {activeTab === 'metrics' && (
          <div className="p-8 md:p-16 text-center border-2 border-dashed border-border rounded-xl bg-muted/20">
              <Activity size={48} className="mx-auto mb-4 text-muted-foreground" />
              <h3 className="text-lg font-bold text-foreground">Metrics Visualization</h3>
              <p className="text-muted-foreground max-w-sm mx-auto mt-2">Historical CPU and Memory data will be visualized here.</p>
          </div>
      )}

      {activeTab === 'advanced' && (
        <div className="space-y-6">
            <div className="bg-card border border-border p-6 rounded-xl shadow-sm">
                <h3 className="font-bold mb-4 text-xl">Advanced Container Configuration</h3>
                <p className="text-muted-foreground text-sm mb-6">
                    Directly override the Kubernetes Pod Spec. Use with caution.
                </p>
                <div className="h-96 border border-border rounded-lg overflow-hidden">
                    <Editor
                        height="100%"
                        defaultLanguage="json"
                        defaultValue={`{
  "securityContext": {
    "runAsUser": 1000,
    "allowPrivilegeEscalation": false
  },
  "resources": {
    "limits": {
      "cpu": "${service.cpu_cores}",
      "memory": "${service.memory_mb}Mi"
    }
  }
}`}
                        theme="vs-dark"
                        options={{ minimap: { enabled: false }, fontSize: 14 }}
                    />
                </div>
                <div className="flex justify-end mt-4">
                    <button className="bg-primary text-primary-foreground px-4 py-2 rounded-md font-bold hover:opacity-90 transition-opacity">
                        Apply Overrides
                    </button>
                </div>
            </div>
        </div>
      )}
    </ServiceLayout>
  );
}
