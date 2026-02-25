'use client';

import { useState, useEffect } from 'react';
import { TopologyNode } from '@/types/topology';
import { servicesApi, EnvVar } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Loader2, X, Eye, EyeOff, Server, Database, Activity, Copy, ExternalLink, Globe } from 'lucide-react';

interface ServiceSidePanelProps {
  node: TopologyNode;
  onClose: () => void;
}

export function ServiceSidePanel({ node, onClose }: ServiceSidePanelProps) {
  const [envVars, setEnvVars] = useState<EnvVar[]>([]);
  const [loadingEnv, setLoadingEnv] = useState(false);
  const [revealedKeys, setRevealedKeys] = useState<Record<string, string>>({}); // key -> value

  // Fetch env vars when node changes (only for SERVICE type)
  useEffect(() => {
    if (node.type?.toUpperCase() === 'SERVICE') {
      setLoadingEnv(true);
      servicesApi.getEnvVars(node.id)
        .then(setEnvVars)
        .catch(err => console.error('Failed to load env vars', err))
        .finally(() => setLoadingEnv(false));
    } else {
      setEnvVars([]);
    }
    setRevealedKeys({});
  }, [node]);

  const handleReveal = async (key: string) => {
    if (revealedKeys[key]) {
      // Toggle off
      const next = { ...revealedKeys };
      delete next[key];
      setRevealedKeys(next);
    } else {
      // Fetch and reveal
      try {
        const res = await servicesApi.revealEnvVar(node.id, key);
        setRevealedKeys(prev => ({ ...prev, [key]: res.value }));
      } catch (err) {
        console.error('Failed to reveal secret', err);
      }
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <div className="absolute top-0 right-0 h-full w-80 bg-zinc-950/95 border-l border-zinc-800 backdrop-blur-md shadow-2xl flex flex-col z-50 animate-in slide-in-from-right-10 duration-200">

      {/* Header */}
      <div className="p-4 border-b border-zinc-800 flex justify-between items-start bg-zinc-900/50">
        <div className="flex items-center gap-3">
           <div className={`p-2 rounded-lg border border-zinc-700/50 ${node.type?.toUpperCase() === 'SERVICE' ? 'bg-blue-500/10 text-blue-400' : 'bg-purple-500/10 text-purple-400'}`}>
              {node.type?.toUpperCase() === 'SERVICE' ? <Server className="w-5 h-5" /> : <Database className="w-5 h-5" />}
           </div>
           <div>
              <h2 className="font-semibold text-zinc-100 truncate max-w-[180px]" title={node.data.name}>
                {node.data.name}
              </h2>
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                 <span className="uppercase">{node.data.kind}</span>
                 <span>•</span>
                 <span className={`capitalize ${node.data.status === 'ACTIVE' ? 'text-emerald-400' : 'text-zinc-400'}`}>
                    {node.data.status?.toLowerCase()}
                 </span>
              </div>
           </div>
        </div>
        <Button variant="ghost" size="icon" className="h-8 w-8 text-zinc-500 hover:text-white" onClick={onClose}>
          <X className="w-4 h-4" />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-6">

          {/* Quick Actions */}
          {node.type?.toUpperCase() === 'SERVICE' && (
            <div className="grid grid-cols-2 gap-2">
               <Button variant="outline" size="sm" className="w-full text-xs h-8 border-zinc-700 hover:bg-zinc-800 hover:text-white" onClick={() => window.open(`/services/${node.id}`, '_blank')}>
                 <Activity className="w-3.5 h-3.5 mr-2" />
                 Dashboard
               </Button>
               {node.data.url && (
                 <Button variant="outline" size="sm" className="w-full text-xs h-8 border-zinc-700 hover:bg-zinc-800 hover:text-white" onClick={() => window.open(`https://${node.data.url}`, '_blank')}>
                   <ExternalLink className="w-3.5 h-3.5 mr-2" />
                   Open App
                 </Button>
               )}
            </div>
          )}

          {/* Metadata */}
          <div className="space-y-3">
             <h4 className="text-[10px] font-bold text-zinc-500 uppercase tracking-wider">Details</h4>
             <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="bg-zinc-900/50 p-2 rounded border border-zinc-800/50">
                   <div className="text-zinc-500 mb-1">Type</div>
                   <div className="text-zinc-300 font-mono">{node.data.subtype}</div>
                </div>
                <div className="bg-zinc-900/50 p-2 rounded border border-zinc-800/50">
                   <div className="text-zinc-500 mb-1">Region</div>
                   <div className="text-zinc-300">{node.data.region}</div>
                </div>
                {node.data.metadata?.replicas !== undefined && (
                    <div className="bg-zinc-900/50 p-2 rounded border border-zinc-800/50">
                        <div className="text-zinc-500 mb-1">Replicas</div>
                        <div className="text-zinc-300">{node.data.metadata.replicas}</div>
                    </div>
                )}
                {node.data.metadata?.language && (
                    <div className="bg-zinc-900/50 p-2 rounded border border-zinc-800/50">
                        <div className="text-zinc-500 mb-1">Buildpack</div>
                        <div className="text-zinc-300">{node.data.metadata.language}</div>
                    </div>
                )}
             </div>
          </div>

          {/* Environment Variables */}
          {node.type?.toUpperCase() === 'SERVICE' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                 <h4 className="text-[10px] font-bold text-zinc-500 uppercase tracking-wider">Environment Variables</h4>
                 <span className="text-[10px] text-zinc-600">{envVars.length} vars</span>
              </div>

              {loadingEnv ? (
                <div className="flex justify-center py-4"><Loader2 className="w-5 h-5 animate-spin text-zinc-600" /></div>
              ) : envVars.length === 0 ? (
                <div className="text-xs text-zinc-600 italic text-center py-2">No environment variables set.</div>
              ) : (
                <div className="space-y-2">
                  {envVars.map((env) => {
                    const isRevealed = !!revealedKeys[env.key];
                    const displayValue = env.is_secret && !isRevealed ? '••••••••••••' : (revealedKeys[env.key] || env.value);

                    return (
                      <div key={env.id} className="group bg-zinc-900/30 rounded-lg border border-zinc-800/50 hover:border-zinc-700 transition-colors overflow-hidden">
                        <div className="px-3 py-2 border-b border-zinc-800/30 bg-zinc-900/50 flex justify-between items-center">
                           <code className="text-[10px] text-blue-300 font-mono break-all">{env.key}</code>
                           {env.is_secret && (
                             <button onClick={() => handleReveal(env.key)} className="text-zinc-500 hover:text-white transition-colors">
                               {isRevealed ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                             </button>
                           )}
                        </div>
                        <div className="px-3 py-2 flex justify-between items-center gap-2">
                           <code className="text-[10px] text-zinc-400 font-mono truncate flex-1 block min-w-0" title={displayValue}>
                             {displayValue}
                           </code>
                           <button
                             onClick={() => copyToClipboard(isRevealed || !env.is_secret ? (revealedKeys[env.key] || env.value) : '')}
                             className="text-zinc-600 hover:text-zinc-300 opacity-0 group-hover:opacity-100 transition-opacity"
                             disabled={env.is_secret && !isRevealed}
                           >
                             <Copy className="w-3 h-3" />
                           </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

        </div>
      </ScrollArea>
    </div>
  );
}
