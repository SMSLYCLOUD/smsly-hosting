'use client';

import { useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  Bot, Cpu, Cable, Plug, Copy, Check,
  ExternalLink, Terminal, Code2, Server,
  Brain, List, Loader2, Globe, BookOpen,
  ChevronRight, Power, RotateCcw, Play, RefreshCw,
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/components/ui/use-toast';
import { mcpApi, McpStatus, McpTool } from '@/lib/api';

const TOOLS = [
  { name: 'list_services', desc: 'List all deployed services and their status' },
  { name: 'get_deployment_status', desc: 'Get detailed deployment status and timings' },
  { name: 'get_service_logs', desc: 'Fetch service deployment/runtime logs' },
  { name: 'get_service_env_vars', desc: 'Get environment variables (secrets masked)' },
  { name: 'set_service_env_var', desc: 'Set/update an environment variable' },
  { name: 'delete_service_env_var', desc: 'Delete an environment variable' },
  { name: 'trigger_service_rebuild', desc: 'Trigger automated deployment rebuild' },
  { name: 'get_error_diagnostics', desc: 'Analyze deployment failure logs' },
  { name: 'list_projects', desc: 'List all projects/workspaces' },
  { name: 'get_project_services', desc: 'Get all services in a project' },
  { name: 'bulk_import_env_vars', desc: 'Bulk import environment variables' },
  { name: 'list_service_addons', desc: 'List addons attached to a service' },
  { name: 'provision_service_addon', desc: 'Provision a new addon for a service' },
  { name: 'get_exhaustive_deployment_diagnostics', desc: 'Full 9-pillar telemetry analysis' },
  { name: 'list_managed_servers', desc: 'List all cloud nodes and servers' },
  { name: 'get_server_health', desc: 'Get detailed server health' },
  { name: 'deploy_from_local_archive', desc: 'Deploy from a local source archive' },
  { name: 'search_services', desc: 'Search services by name, slug, or repo URL' },
  { name: 'get_service_details', desc: 'Full service detail: config, resources, HA, domains' },
  { name: 'list_service_deployments', desc: 'Deployment history for a service' },
  { name: 'cancel_deployment', desc: 'Cancel a queued/building deployment (admin)' },
  { name: 'retry_deployment', desc: 'Re-queue a failed/cancelled deployment (admin)' },
  { name: 'get_failed_deployments', desc: 'Recent failures across services with log excerpts' },
  { name: 'list_all_addons', desc: 'List all addons across services' },
  { name: 'get_addon_details', desc: 'Addon detail with masked connection info' },
  { name: 'get_service_domains', desc: 'Service domains with SSL/verification state' },
];

const CLAUDE_DESKTOP_CONFIG = {
  mcpServers: {
    'smsly-ecosystem': {
      command: 'python',
      args: ['manage.py', 'runmcpserver'],
      cwd: '/path/to/smsly-hosting/backend',
    },
  },
};

const CURSOR_CONFIG = {
  mcpServers: {
    'smsly-ecosystem': {
      command: 'python',
      args: ['manage.py', 'runmcpserver'],
      cwd: '/path/to/smsly-hosting/backend',
    },
  },
};

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const { toast } = useToast();

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast({ title: 'Copied to clipboard' });
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast({ title: 'Failed to copy', variant: 'destructive' });
    }
  };

  return (
    <Button variant="ghost" size="icon" onClick={handleCopy} className="shrink-0">
      {copied ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
    </Button>
  );
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  return (
    <div className="relative group">
      <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
        <CopyButton text={code} />
      </div>
      <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 font-semibold">{language}</div>
      <pre className="bg-[#0d1117] text-[13px] leading-relaxed p-4 rounded-lg overflow-x-auto border border-white/5 font-mono">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export default function MCPPage() {
  const { toast } = useToast();
  const [mcpStatus, setMcpStatus] = useState<McpStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [controlBusy, setControlBusy] = useState<string | null>(null);
  const [liveTools, setLiveTools] = useState<McpTool[] | null>(null);
  const [runningTool, setRunningTool] = useState<string | null>(null);
  const [toolArgs, setToolArgs] = useState<Record<string, string>>({});
  const [toolResult, setToolResult] = useState<Record<string, { ok: boolean; text: string }>>({});

  const refreshStatus = useCallback(async (silent = false) => {
    if (!silent) setStatusLoading(true);
    try {
      setMcpStatus(await mcpApi.status());
    } catch {
      setMcpStatus({ exists: false, running: false, error: 'Status unavailable' });
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const refreshTools = useCallback(async () => {
    try {
      const res = await mcpApi.tools();
      setLiveTools(res.tools || []);
    } catch {
      setLiveTools(null);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    refreshTools();
  }, [refreshStatus, refreshTools]);

  const controlServer = async (action: 'start' | 'stop' | 'restart') => {
    setControlBusy(action);
    try {
      const res = await mcpApi.control(action);
      setMcpStatus(res);
      toast({ title: `MCP server ${action}${action.endsWith('e') ? 'd' : 'ed'}` });
    } catch (err: any) {
      toast({
        title: `Failed to ${action} MCP server`,
        description: err?.response?.data?.error || err?.message || String(err),
        variant: 'destructive',
      });
    } finally {
      setControlBusy(null);
    }
  };

  const runTool = async (tool: McpTool) => {
    const raw = (toolArgs[tool.name] || '').trim();
    let args: Record<string, unknown> = {};
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('args must be a JSON object');
        }
        args = parsed;
      } catch (err: any) {
        setToolResult((p) => ({ ...p, [tool.name]: { ok: false, text: `Invalid JSON args: ${err?.message || err}` } }));
        return;
      }
    }
    setRunningTool(tool.name);
    try {
      const res = await mcpApi.callTool(tool.name, args);
      setToolResult((p) => ({
        ...p,
        [tool.name]: res.ok
          ? { ok: true, text: typeof res.result === 'string' ? res.result : JSON.stringify(res.result, null, 2) }
          : { ok: false, text: res.error || 'Tool failed' },
      }));
    } catch (err: any) {
      setToolResult((p) => ({
        ...p,
        [tool.name]: { ok: false, text: err?.response?.data?.error || err?.message || String(err) },
      }));
    } finally {
      setRunningTool(null);
    }
  };

  const tools: McpTool[] = liveTools ?? TOOLS.map((t) => ({ name: t.name, description: t.desc, params: [] }));
  const running = !!mcpStatus?.running;

  return (
    <DashboardShell>
      <div className="flex-1 p-4 pt-safe sm:p-8 relative z-10 w-full overflow-x-hidden">
        <motion.div
          className="max-w-5xl mx-auto space-y-6 sm:space-y-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {/* Header */}
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/25">
                <Plug className="text-white" size={22} />
              </div>
              MCP Server
            </h1>
            <p className="text-muted-foreground mt-1">
              Model Context Protocol — connect AI assistants to your SMSLY ecosystem
            </p>
          </div>

          {/* Server status + power controls */}
          <Card className={running ? "border-emerald-500/20 bg-emerald-500/5" : "border-border"}>
            <CardContent className="p-6">
              <div className="flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
                <div className="flex items-center gap-3">
                  <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${running ? "bg-emerald-500" : "bg-zinc-500"}`} />
                  <div>
                    <div className="font-bold">
                      {statusLoading ? "Checking MCP server…" : running ? "MCP Server Running" : "MCP Server Stopped"}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {mcpStatus?.error
                        ? mcpStatus.error
                        : running
                          ? `${mcpStatus?.endpoint || "http://smsly-mcp-server:8001/sse"}${mcpStatus?.container_id ? ` · ${mcpStatus.container_id}` : ""}`
                          : "Start the server to accept SSE connections from AI tools."}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => refreshStatus()} disabled={statusLoading}>
                    <RefreshCw className={`w-3.5 h-3.5 mr-1 ${statusLoading ? "animate-spin" : ""}`} /> Refresh
                  </Button>
                  {!running ? (
                    <Button size="sm" onClick={() => controlServer('start')} disabled={!!controlBusy}>
                      {controlBusy === 'start' ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Power className="w-3.5 h-3.5 mr-1" />}
                      Start
                    </Button>
                  ) : (
                    <>
                      <Button variant="outline" size="sm" onClick={() => controlServer('restart')} disabled={!!controlBusy}>
                        {controlBusy === 'restart' ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5 mr-1" />}
                        Restart
                      </Button>
                      <Button variant="destructive" size="sm" onClick={() => controlServer('stop')} disabled={!!controlBusy}>
                        {controlBusy === 'stop' ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Power className="w-3.5 h-3.5 mr-1" />}
                        Stop
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* What is MCP */}
          <Card className="border-blue-500/20 bg-blue-500/5">
            <CardContent className="p-6">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center shrink-0 mt-1">
                  <Brain className="text-blue-500" size={20} />
                </div>
                <div>
                  <h3 className="font-bold text-lg mb-2">What is MCP?</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    The <strong>Model Context Protocol (MCP)</strong> lets AI assistants like{' '}
                    <strong>Claude Desktop</strong>, <strong>Cursor</strong>, and{' '}
                    <strong>VS Code</strong> directly interact with your SMSLY platform.
                    The MCP server exposes <strong>{tools.length} tools</strong> that AI can use to
                    manage services, deployments, environment variables, addons, servers, and more.
                  </p>
                  <p className="text-sm text-muted-foreground leading-relaxed mt-2">
                    Instead of switching between browser tabs, your AI assistant can execute
                    real operations — listing services, checking logs, triggering rebuilds, and
                    provisioning addons — all through natural language.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Tabs defaultValue="connect" className="w-full">
            <TabsList className="bg-muted/20">
              <TabsTrigger value="connect" className="gap-2">
                <Plug className="w-4 h-4" /> Connect
              </TabsTrigger>
              <TabsTrigger value="tools" className="gap-2">
                <List className="w-4 h-4" /> Tools
              </TabsTrigger>
              <TabsTrigger value="server" className="gap-2">
                <Terminal className="w-4 h-4" /> Server
              </TabsTrigger>
            </TabsList>

            {/* ─── Connect Tab ───────────────────────────────────────── */}
            <TabsContent value="connect" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Bot className="text-cyan-500" /> Claude Desktop
                  </CardTitle>
                  <CardDescription>
                    Add the MCP server to Claude Desktop&apos;s configuration
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    Add this to your <code className="text-xs bg-muted px-1.5 py-0.5 rounded">claude_desktop_config.json</code>:
                  </p>
                  <CodeBlock
                    language="JSON — claude_desktop_config.json"
                    code={JSON.stringify(CLAUDE_DESKTOP_CONFIG, null, 2)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Replace <code className="bg-muted px-1 rounded">/path/to/smsly-hosting/backend</code> with the actual path on your system.
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Code2 className="text-cyan-500" /> Cursor / VS Code
                  </CardTitle>
                  <CardDescription>
                    Configure Cursor or VS Code to use the MCP server
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    Add to your Cursor MCP configuration or VS Code settings:
                  </p>
                  <CodeBlock
                    language="JSON — .cursor/mcp.json or VS Code settings"
                    code={JSON.stringify(CURSOR_CONFIG, null, 2)}
                  />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Globe className="text-cyan-500" /> SSE Mode (Remote)
                  </CardTitle>
                  <CardDescription>
                    Connect over HTTP if the server is running remotely
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    Start the server in SSE mode, then configure your AI tool to connect via URL:
                  </p>
                  <CodeBlock
                    language="Bash — Start SSE server"
                    code={'python manage.py runmcpserver --sse --host 0.0.0.0 --port 8001'}
                  />
                  <CodeBlock
                    language="JSON — Remote config"
                    code={JSON.stringify({
                      mcpServers: {
                        'smsly-ecosystem': {
                          url: 'http://your-server:8001/sse',
                        },
                      },
                    }, null, 2)}
                  />
                </CardContent>
              </Card>
            </TabsContent>

            {/* ─── Tools Tab ─────────────────────────────────────────── */}
            <TabsContent value="tools" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Cpu className="text-cyan-500" /> Available MCP Tools
                  </CardTitle>
                  <CardDescription>
                    {tools.length} tools {liveTools ? "queried live from the backend" : "(offline list — backend unreachable)"} — run any of them directly below
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-1 gap-2">
                    {tools.map((tool) => {
                      const result = toolResult[tool.name];
                      const busy = runningTool === tool.name;
                      return (
                        <div
                          key={tool.name}
                          className="p-3 rounded-lg bg-muted/20 border border-border/30 hover:bg-muted/40 transition-colors"
                        >
                          <div className="flex items-start gap-3">
                            <div className="w-7 h-7 rounded-md bg-cyan-500/10 flex items-center justify-center shrink-0 mt-0.5">
                              <Cable className="text-cyan-500" size={14} />
                            </div>
                            <div className="min-w-0 flex-1">
                            <div className="text-sm font-semibold font-mono truncate">{tool.name}</div>
                            <div className="text-xs text-muted-foreground mt-0.5">{tool.description}</div>
                              {tool.params && tool.params.length > 0 && (
                                <div className="text-[11px] text-muted-foreground mt-1 font-mono">
                                  args: {tool.params.map((p) => `${p.name}${p.required ? "*" : ""}:${p.type}`).join(", ")}
                                </div>
                              )}
                            </div>
                            <Button
                              variant="outline" size="sm" className="shrink-0"
                              disabled={busy}
                              onClick={() => runTool(tool)}
                            >
                              {busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1" />}
                              Run
                            </Button>
                          </div>
                          <div className="mt-2 flex gap-2">
                            <input
                              value={toolArgs[tool.name] || ""}
                              onChange={(e) => setToolArgs((p) => ({ ...p, [tool.name]: e.target.value }))}
                              placeholder='Optional JSON args, e.g. {"lines": 100}'
                              spellCheck={false}
                              className="flex-1 min-w-0 text-xs font-mono bg-background border border-border rounded px-2 py-1.5"
                            />
                          </div>
                          {result && (
                            <pre className={`mt-2 text-[12px] leading-relaxed p-3 rounded-lg overflow-x-auto border font-mono whitespace-pre-wrap break-words max-h-64 overflow-y-auto ${result.ok ? "bg-[#0d1117] border-white/5" : "bg-red-500/5 border-red-500/20 text-red-300"}`}>
                              {result.text}
                            </pre>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ─── Server Tab ────────────────────────────────────────── */}
            <TabsContent value="server" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Server className="text-cyan-500" /> Running the Server
                  </CardTitle>
                  <CardDescription>
                    The MCP server runs as a separate process alongside the main Django app
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <p className="text-sm text-muted-foreground">
                      The MCP server runs as a managed <code className="text-xs bg-muted px-1.5 py-0.5 rounded">smsly-mcp-server</code> container
                      alongside the main Django app. Use the status card above to start, stop, or restart it —
                      or run it manually with:
                    </p>

                  <div className="space-y-3">
                    <div className="p-4 rounded-lg bg-muted/20 border border-border/50">
                      <div className="flex items-center gap-2 text-sm font-semibold mb-2">
                        <Terminal className="text-emerald-500" size={16} />
                        STDIO mode (default — for local AI tools)
                      </div>
                      <pre className="bg-[#0d1117] text-[13px] p-3 rounded-lg font-mono border border-white/5">
                        <code>python manage.py runmcpserver</code>
                      </pre>
                    </div>

                    <div className="p-4 rounded-lg bg-muted/20 border border-border/50">
                      <div className="flex items-center gap-2 text-sm font-semibold mb-2">
                        <Globe className="text-emerald-500" size={16} />
                        SSE mode (HTTP — for remote connections)
                      </div>
                      <pre className="bg-[#0d1117] text-[13px] p-3 rounded-lg font-mono border border-white/5">
                        <code>python manage.py runmcpserver --sse --host 0.0.0.0 --port 8001</code>
                      </pre>
                    </div>
                  </div>

                  <div className="p-4 rounded-lg bg-amber-500/10 border border-amber-500/20">
                    <div className="flex items-start gap-3">
                      <BookOpen className="text-amber-500 shrink-0 mt-0.5" size={16} />
                      <div className="text-sm text-amber-400">
                        <strong>Note:</strong> Manual mode is optional now that the server is managed
                        from this page. Only use a terminal if you need custom flags — the managed
                        container runs SSE on port 8001.
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </motion.div>
      </div>
    </DashboardShell>
  );
}
