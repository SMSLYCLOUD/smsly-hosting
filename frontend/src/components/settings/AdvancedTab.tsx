'use client';

import React, { useState } from 'react';
import Editor from "@monaco-editor/react";
import { Service } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Save, AlertTriangle } from 'lucide-react';

export function AdvancedTab({ service }: { service: Service }) {
    const [config, setConfig] = useState({
        image: 'postgres:16', // Mock default
        restart: 'always',
        cmd: service.start_command || '',
    });

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4">

            {/* Raw JSON Config */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h3 className="font-bold text-lg">Raw Pod Configuration</h3>
                        <p className="text-sm text-muted-foreground">Directly override Kubernetes specifications.</p>
                    </div>
                    <Button variant="outline" className="gap-2">
                        <Save size={16} /> Apply
                    </Button>
                </div>
                <div className="h-96 border border-border rounded-lg overflow-hidden">
                    <Editor
                        height="100%"
                        defaultLanguage="json"
                        defaultValue={`{
  "spec": {
    "containers": [
      {
        "name": "${service.name}",
        "image": "registry.smsly.cloud/${service.name}:latest",
        "resources": {
          "limits": {
            "cpu": "${service.cpu_cores}",
            "memory": "${service.memory_mb}Mi"
          }
        },
        "securityContext": {
          "allowPrivilegeEscalation": false
        }
      }
    ]
  }
}`}
                        theme="vs-dark"
                        options={{ minimap: { enabled: false }, fontSize: 13 }}
                    />
                </div>
            </Card>

            {/* Container Settings Form */}
            <Card className="p-6 border-border shadow-md">
                <h3 className="font-bold text-lg mb-6">Container Runtime</h3>
                <div className="grid grid-cols-2 gap-6">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Docker Image</label>
                        <Input defaultValue={`registry.smsly.cloud/${service.name}`} />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Image Tag</label>
                        <Input defaultValue="latest" />
                    </div>
                    <div className="col-span-2 space-y-2">
                        <label className="text-sm font-medium">Command Override</label>
                        <Input placeholder="/bin/sh -c '...'" defaultValue={config.cmd} />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Restart Policy</label>
                        <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
                            <option value="always">Always</option>
                            <option value="on-failure">On Failure</option>
                            <option value="never">Never</option>
                        </select>
                    </div>
                </div>
                <div className="mt-6 flex justify-end">
                    <Button>Save Configuration</Button>
                </div>
            </Card>

            {/* Danger Zone */}
            <Card className="p-6 border-red-200/50 bg-red-50/10 dark:bg-red-900/10">
                <h3 className="font-bold text-lg text-destructive mb-2 flex items-center gap-2">
                    <AlertTriangle size={20} /> Danger Zone
                </h3>
                <p className="text-sm text-muted-foreground mb-4">
                    Irreversible actions that affect your service availability.
                </p>
                <div className="flex gap-4">
                    <Button variant="destructive">Force Redeploy</Button>
                    <Button variant="destructive" className="bg-red-600 hover:bg-red-700">Delete Service</Button>
                </div>
            </Card>
        </div>
    );
}
