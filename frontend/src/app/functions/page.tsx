'use client';

import React, { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/components/ui/use-toast';
import { Zap, Play, Loader2, ExternalLink, Plus } from 'lucide-react';
import Editor from '@monaco-editor/react';
import { servicesApi, Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { RequiresTier } from '@/components/licensing/RequiresTier';

const DEFAULT_NODE_CODE = '// Write your function here\nexports.handler = async (req, res) => {\n  res.json({ message: "Hello from Edge!" });\n};';
const DEFAULT_PYTHON_CODE = '# Write your function here\ndef handler(req):\n    return {"message": "Hello from Edge!"}';

export default function FunctionsPage() {
  const { toast } = useToast();
  const router = useRouter();
  const [functions, setFunctions] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFunction, setSelectedFunction] = useState<Service | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  // Editor State
  const [name, setName] = useState('');
  const [runtime, setRuntime] = useState('nodejs18');
  const [code, setCode] = useState(DEFAULT_NODE_CODE);
  const [deploying, setDeploying] = useState(false);

  useEffect(() => {
    fetchFunctions();
  }, []);

  const fetchFunctions = async () => {
    try {
      const services = await servicesApi.list();
      const funcs = services.filter(s => s.deploy_type === 'FUNCTION');
      setFunctions(funcs);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleSelect = (func: Service) => {
    setSelectedFunction(func);
    setIsCreating(false);
    servicesApi.get(func.id).then((fullFunc) => {
        setName(fullFunc.name);
        setCode(fullFunc.function_code || '');
        setRuntime(fullFunc.function_runtime || 'nodejs18');
    }).catch((error) => {
        console.error(error);
        toast({
          title: "Failed to load function",
          description: "Could not load the selected function details.",
          variant: "destructive",
        });
    });
  };

  const handleCreateMode = () => {
    setSelectedFunction(null);
    setIsCreating(true);
    setName('');
    setCode(DEFAULT_NODE_CODE);
    setRuntime('nodejs18');
  };

  const handleDeploy = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      toast({ title: "Name required", variant: "destructive" });
      return;
    }
    if (!code.trim()) {
      toast({ title: "Code required", variant: "destructive" });
      return;
    }
    setDeploying(true);
    try {
      if (selectedFunction) {
        // Update existing
        await servicesApi.update(selectedFunction.id, {
            function_code: code,
            function_runtime: runtime
        });
        const deployResult = await servicesApi.deploy(selectedFunction.id);
        if (deployResult?.existing_deployment) {
          const statusLabel = deployResult?.existing_deployment?.status || 'in progress';
          toast({
            title: "Deployment already in progress",
            description: `Current deployment status: ${statusLabel}.`,
          });
          return;
        }
        toast({ title: "Function updated", description: "Deployment triggered." });
        fetchFunctions();
      } else {
        // Create new
        const newService = await servicesApi.create({
            name: trimmedName,
            deploy_type: 'FUNCTION',
            function_code: code,
            function_runtime: runtime,
            cpu_cores: 0.25,
            memory_mb: 128,
            internal_port: 8000,
            health_check_path: '/health'
        });
        // Trigger deploy
        const deployResult = await servicesApi.deploy(newService.id);
        if (deployResult?.existing_deployment) {
          const statusLabel = deployResult?.existing_deployment?.status || 'in progress';
          toast({
            title: "Deployment already in progress",
            description: `Current deployment status: ${statusLabel}.`,
          });
          return;
        }
        toast({ title: "Function created", description: "Deployment triggered." });
        setSelectedFunction(newService);
        setIsCreating(false);
        fetchFunctions();
      }
    } catch (e: any) {
      console.error(e);
      const message = e?.response?.data?.error?.message
        || e?.response?.data?.error
        || e?.response?.data?.detail
        || e?.message
        || "Unable to deploy this function.";
      toast({ title: "Deploy failed", description: String(message), variant: "destructive" });
    } finally {
      setDeploying(false);
    }
  };

  return (
    <DashboardShell>
      <RequiresTier tier="pro">
      <div className="container h-[calc(100vh-100px)] max-w-full p-4 flex gap-4">
        {/* Sidebar List */}
        <div className="w-64 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-lg flex items-center gap-2">
              <Zap className="h-5 w-5 text-yellow-500" />
              Functions
            </h2>
            <Button size="icon" variant="ghost" onClick={handleCreateMode}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-2">
            {loading ? <Loader2 className="animate-spin mx-auto mt-10" /> : functions.map(func => (
                <div
                    key={func.id}
                    onClick={() => handleSelect(func)}
                    className={`p-3 rounded-lg border cursor-pointer hover:bg-muted transition-colors ${selectedFunction?.id === func.id ? 'bg-muted border-primary' : 'bg-card'}`}
                >
                    <div className="font-medium truncate">{func.name}</div>
                    <div className="text-xs text-muted-foreground flex justify-between mt-1">
                        <span>{func.latest_deployment?.status || 'UNKNOWN'}</span>
                    </div>
                </div>
            ))}
            {functions.length === 0 && !loading && (
                <div className="text-center text-sm text-muted-foreground py-10">
                    No functions yet.
                    <br/>
                    <Button variant="link" onClick={handleCreateMode}>Create one</Button>
                </div>
            )}
          </div>
        </div>

        {/* Editor Area */}
        <div className="flex-1 flex flex-col gap-4">
          {(selectedFunction || isCreating) ? (
            <Card className="flex-1 flex flex-col border-none shadow-none bg-transparent">
                <div className="flex justify-between items-center mb-4 bg-card p-4 rounded-lg border">
                    <div className="flex items-center gap-4">
                        <div className="grid gap-1">
                            <Label htmlFor="name">Function Name</Label>
                            <Input
                                id="name"
                                value={name}
                                onChange={e => setName(e.target.value)}
                                disabled={!!selectedFunction}
                                className="h-8 w-60"
                                placeholder="my-function"
                            />
                        </div>
                        <div className="grid gap-1">
                            <Label>Runtime</Label>
                            <Select value={runtime} onValueChange={(val) => {
                                setRuntime(val);
                                if (!selectedFunction) {
                                    if (code === DEFAULT_NODE_CODE && val.includes('python')) {
                                        setCode(DEFAULT_PYTHON_CODE);
                                    } else if (code === DEFAULT_PYTHON_CODE && val.includes('node')) {
                                        setCode(DEFAULT_NODE_CODE);
                                    }
                                }
                            }}>
                                <SelectTrigger className="h-8 w-40">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="nodejs18">Node.js 18</SelectItem>
                                    <SelectItem value="python3.9">Python 3.9</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {selectedFunction?.public_domain && (
                            <Button variant="outline" size="sm" asChild>
                                <a href={`https://${selectedFunction.public_domain}`} target="_blank" rel="noreferrer">
                                    <ExternalLink className="h-4 w-4 mr-2" />
                                    Open URL
                                </a>
                            </Button>
                        )}
                        <Button onClick={handleDeploy} disabled={deploying} size="sm">
                            {deploying ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Play className="h-4 w-4 mr-2" />}
                            {selectedFunction ? 'Update & Deploy' : 'Deploy Function'}
                        </Button>
                    </div>
                </div>

                <div className="flex-1 border rounded-lg overflow-hidden bg-[#1e1e1e]">
                    <Editor
                        height="100%"
                        defaultLanguage="javascript"
                        language={runtime.includes('python') ? 'python' : 'javascript'}
                        value={code}
                        onChange={(val) => setCode(val || '')}
                        theme="vs-dark"
                        options={{
                            minimap: { enabled: false },
                            fontSize: 14,
                            padding: { top: 16 }
                        }}
                    />
                </div>
            </Card>
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
                Select a function or create a new one.
            </div>
          )}
        </div>
      </div>
      </RequiresTier>
    </DashboardShell>
  );
}
