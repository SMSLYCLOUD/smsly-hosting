'use client';

import { useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/components/ui/use-toast';
import { Loader2, Rocket, Code2, Zap } from 'lucide-react';
import api from '@/lib/api';

export default function ServerlessPage() {
  const { toast } = useToast();
  const [functionName, setFunctionName] = useState('');
  const [runtime, setRuntime] = useState('python3.9');
  const [code, setCode] = useState('');
  const [deploying, setDeploying] = useState(false);

  const handleDeploy = async () => {
    if (!functionName.trim() || !code.trim()) {
      toast({ title: 'Missing fields', description: 'Function name and handler code are required.', variant: 'destructive' });
      return;
    }
    setDeploying(true);
    try {
      const response = await api.post('/deployments/upload/', {
        name: functionName,
        runtime,
        code,
        type: 'function'
      });
      toast({
        title: 'Deployed successfully',
        description: `Function "${functionName}" deployed. ID: ${response.data.deployment_id || response.data.id || 'created'}`,
      });
      setFunctionName('');
      setCode('');
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.response?.data?.error || 'Failed to deploy function.';
      toast({ title: 'Deploy failed', description: msg, variant: 'destructive' });
    } finally {
      setDeploying(false);
    }
  };

  return (
    <DashboardShell>
      <div className="flex-1 p-6 md:p-12 max-w-3xl mx-auto w-full space-y-6">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            <Zap className="h-8 w-8 text-primary" />
            Serverless Functions
          </h1>
          <p className="text-muted-foreground mt-1">Deploy lightweight functions that run on demand.</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Rocket className="h-5 w-5" />
              New Function
            </CardTitle>
            <CardDescription>Define your function and deploy it instantly.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="fn-name">Function Name</Label>
              <Input
                id="fn-name"
                placeholder="my-api-handler"
                value={functionName}
                onChange={(e) => setFunctionName(e.target.value)}
                disabled={deploying}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="fn-runtime">Runtime</Label>
              <select
                id="fn-runtime"
                className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                value={runtime}
                onChange={(e) => setRuntime(e.target.value)}
                disabled={deploying}
              >
                <option value="python3.9">Python 3.9</option>
                <option value="python3.11">Python 3.11</option>
                <option value="nodejs18">Node.js 18</option>
                <option value="nodejs20">Node.js 20</option>
                <option value="go1.21">Go 1.21</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="fn-code" className="flex items-center gap-2">
                <Code2 className="h-4 w-4" />
                Handler Code
              </Label>
              <textarea
                id="fn-code"
                className="w-full px-4 py-3 rounded-lg bg-background border border-border font-mono text-sm h-48 focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder={`def handler(event):\n    return {"statusCode": 200, "body": "Hello World"}`}
                disabled={deploying}
              />
            </div>

            <Button
              onClick={handleDeploy}
              disabled={deploying}
              className="w-full"
            >
              {deploying ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deploying...
                </>
              ) : (
                <>
                  <Rocket className="mr-2 h-4 w-4" />
                  Deploy Function
                </>
              )}
            </Button>
          </CardContent>
        </Card>
      </div>
    </DashboardShell>
  );
}
