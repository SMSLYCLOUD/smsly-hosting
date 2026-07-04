'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  Bot, Cpu, Cable, Plug, Copy, Check,
  ExternalLink, Terminal, Code2, Server,
  Brain, List, Loader2, Globe, BookOpen,
  ChevronRight
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/components/ui/use-toast';

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
                    The MCP server exposes <strong>20 tools</strong> that AI can use to
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
                    {TOOLS.length} tools registered on the MCP server — your AI assistant can call any of them
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {TOOLS.map((tool) => (
                      <div
                        key={tool.name}
                        className="flex items-start gap-3 p-3 rounded-lg bg-muted/20 border border-border/30 hover:bg-muted/40 transition-colors"
                      >
                        <div className="w-7 h-7 rounded-md bg-cyan-500/10 flex items-center justify-center shrink-0 mt-0.5">
                          <Cable className="text-cyan-500" size={14} />
                        </div>
                        <div className="min-w-0">
                          <div className="text-sm font-semibold font-mono truncate">{tool.name}</div>
                          <div className="text-xs text-muted-foreground mt-0.5">{tool.desc}</div>
                        </div>
                      </div>
                    ))}
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
                    The MCP server is not part of the Django REST API — it runs as a standalone
                    process using the <strong>Model Context Protocol</strong>. Start it with:
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
                        <strong>Note:</strong> The MCP server does not run automatically.
                        You need to start it separately in a terminal or set up a
                        process manager (systemd, supervisor, etc.) to keep it running.
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
