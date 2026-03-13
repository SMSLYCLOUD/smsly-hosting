'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BrainCircuit, RefreshCw, Router, Save, Shuffle } from 'lucide-react';

import { AiRouterConfig, servicesApi } from '@/lib/api';
import { toast } from '@/components/ui/use-toast';

interface AiRouterTabProps {
  serviceId: string;
  serviceDomain: string;
}

export function AiRouterTab({ serviceId, serviceDomain }: AiRouterTabProps) {
  const [config, setConfig] = useState<AiRouterConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deploying, setDeploying] = useState(false);

  const [apiBase, setApiBase] = useState('/api/v1');
  const [uiBase, setUiBase] = useState('/');
  const [braidAlias, setBraidAlias] = useState('braid-llm');
  const [braidEnabled, setBraidEnabled] = useState(true);
  const [selectedServiceIds, setSelectedServiceIds] = useState<string[]>([]);

  const selectedModels = useMemo(
    () => (config?.detected_models || []).filter((model) => selectedServiceIds.includes(model.service_id)),
    [config, selectedServiceIds],
  );

  const chatModels = selectedModels.filter((model) => model.mode === 'chat');
  const embeddingModels = selectedModels.filter((model) => model.mode === 'embedding');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const nextConfig = await servicesApi.getAiRouterConfig(serviceId);
      setConfig(nextConfig);
      setApiBase(nextConfig.api_base || '/api/v1');
      setUiBase(nextConfig.ui_base || '/');
      setBraidAlias(nextConfig.braid_alias || 'braid-llm');
      setBraidEnabled(Boolean(nextConfig.braid_enabled));
      setSelectedServiceIds(nextConfig.selected_service_ids || []);
    } catch (error) {
      console.error(error);
      toast({
        title: 'Failed to load AI Router config',
        description: 'The router settings could not be fetched.',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleSelection = (serviceItemId: string) => {
    setSelectedServiceIds((current) => (
      current.includes(serviceItemId)
        ? current.filter((item) => item !== serviceItemId)
        : [...current, serviceItemId]
    ));
  };

  const handleSave = async (redeployAfterSave: boolean) => {
    setSaving(true);
    try {
      const nextConfig = await servicesApi.saveAiRouterConfig(serviceId, {
        api_base: apiBase,
        ui_base: uiBase,
        braid_alias: braidAlias,
        braid_enabled: braidEnabled,
        selected_service_ids: selectedServiceIds,
      });
      setConfig(nextConfig);
      setApiBase(nextConfig.api_base || '/api/v1');
      setUiBase(nextConfig.ui_base || '/');
      setBraidAlias(nextConfig.braid_alias || 'braid-llm');
      setBraidEnabled(Boolean(nextConfig.braid_enabled));
      setSelectedServiceIds(nextConfig.selected_service_ids || []);
      toast({
        title: 'AI Router config saved',
        description: redeployAfterSave
          ? 'Config saved. Redeploying the router now.'
          : 'Config saved. Redeploy to apply the new routing catalog.',
      });

      if (redeployAfterSave) {
        setDeploying(true);
        await servicesApi.deploy(serviceId);
        toast({
          title: 'Redeploy queued',
          description: 'The router will refresh its models and route labels on this deployment.',
        });
      }
    } catch (error) {
      console.error(error);
      toast({
        title: 'Failed to save AI Router config',
        description: 'The router settings could not be updated.',
        variant: 'destructive',
      });
    } finally {
      setSaving(false);
      setDeploying(false);
    }
  };

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading AI Router configuration...</div>;
  }

  if (!config) {
    return <div className="text-sm text-destructive">AI Router configuration is unavailable.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div className="mb-5 flex items-start justify-between gap-4">
            <div>
              <h3 className="flex items-center gap-2 text-lg font-semibold text-foreground">
                <Router className="h-5 w-5 text-primary" />
                AI Router Catalog
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Auto-detect deployed Ollama services, choose which ones are exposed, and publish the senate alias as <code>{braidAlias || 'braid-llm'}</code>.
              </p>
            </div>
            <button
              type="button"
              onClick={load}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted"
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </button>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <label className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">API Base</span>
              <input
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">UI Base</span>
              <input
                value={uiBase}
                onChange={(e) => setUiBase(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Senate Alias</span>
              <input
                value={braidAlias}
                onChange={(e) => setBraidAlias(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>

          <label className="mt-4 flex items-center justify-between rounded-xl border border-border bg-background/50 px-4 py-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium">
                <Shuffle className="h-4 w-4 text-primary" />
                Enable <code>{braidAlias || 'braid-llm'}</code>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                Exposes one aggregate model alias across all selected chat models.
              </p>
            </div>
            <input
              type="checkbox"
              checked={braidEnabled}
              onChange={(e) => setBraidEnabled(e.target.checked)}
            />
          </label>

          <div className="mt-6 space-y-3">
            {(config.detected_models || []).map((model) => {
              const checked = selectedServiceIds.includes(model.service_id);
              return (
                <label
                  key={model.service_id}
                  className={`flex items-start gap-3 rounded-xl border px-4 py-3 transition ${
                    checked ? 'border-primary/40 bg-primary/5' : 'border-border hover:bg-muted/60'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSelection(model.service_id)}
                    className="mt-1"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-foreground">{model.alias}</span>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                        {model.mode}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Service: <code>{model.service_name}</code> | Upstream: <code>{model.api_base}</code>
                    </p>
                  </div>
                </label>
              );
            })}
            {config.detected_models.length === 0 && (
              <div className="rounded-xl border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
                No active Ollama services were detected for this router.
              </div>
            )}
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => handleSave(false)}
              disabled={saving || deploying}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              <Save className="h-4 w-4" />
              Save
            </button>
            <button
              type="button"
              onClick={() => handleSave(true)}
              disabled={saving || deploying}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-semibold hover:bg-muted disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${deploying ? 'animate-spin' : ''}`} />
              Save + Redeploy
            </button>
          </div>
        </section>

        <section className="rounded-2xl border border-border bg-card p-6 shadow-sm">
          <h3 className="flex items-center gap-2 text-lg font-semibold text-foreground">
            <BrainCircuit className="h-5 w-5 text-primary" />
            Effective Endpoints
          </h3>
          <div className="mt-4 space-y-4 text-sm">
            <div className="rounded-xl bg-muted/60 p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Chat Models</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {chatModels.map((model) => (
                  <span key={model.service_id} className="rounded-full bg-background px-2 py-1 font-mono text-xs text-foreground">
                    {model.alias}
                  </span>
                ))}
                {chatModels.length === 0 && <span className="text-muted-foreground">No chat models selected.</span>}
              </div>
            </div>

            <div className="rounded-xl bg-muted/60 p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Embedding Models</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {embeddingModels.map((model) => (
                  <span key={model.service_id} className="rounded-full bg-background px-2 py-1 font-mono text-xs text-foreground">
                    {model.alias}
                  </span>
                ))}
                {embeddingModels.length === 0 && <span className="text-muted-foreground">No embedding models selected.</span>}
              </div>
            </div>

            <div className="rounded-xl bg-muted/60 p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Recommended SMSLY Marketer Env</p>
              <pre className="mt-2 overflow-x-auto rounded-lg bg-background p-3 text-xs text-foreground">
{`AI_PROVIDER=openai_compatible
AI_BASE_URL=https://${serviceDomain}${apiBase}
AI_API_KEY=<router key>
AI_MODEL=${braidEnabled ? (braidAlias || 'braid-llm') : (chatModels[0]?.alias || 'ollama/phi3')}
AI_EMBEDDING_MODEL=${embeddingModels[0]?.alias || 'ollama/nomic-embed-text'}`}
              </pre>
            </div>

            <div className="rounded-xl border border-border bg-background p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Generated LiteLLM Config Preview</p>
              <pre className="mt-3 max-h-[420px] overflow-auto rounded-lg bg-zinc-950 p-3 text-xs text-zinc-100">
                {config.config_preview}
              </pre>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
