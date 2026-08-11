"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Trash2, Copy } from "lucide-react";
import { coreApi } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function ApiKeysTab() {
  const { toast } = useToast();
  const [apiKeys, setApiKeys] = useState<any[]>([]);
  const [newKeyName, setNewKeyName] = useState("");
  const [generatedKey, setGeneratedKey] = useState<string | null>(null);

  const fetchApiKeys = useCallback(async () => {
    try {
      const keys = await coreApi.getApiKeys();
      setApiKeys(keys);
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    fetchApiKeys();
  }, [fetchApiKeys]);

  const handleCreateApiKey = async () => {
    try {
      const res = await coreApi.createApiKey(newKeyName || "CLI Token");
      setGeneratedKey(res.key);
      setNewKeyName("");
      fetchApiKeys();
      toast({ title: "API Key Created", description: "Copy it now, you won't see it again." });
    } catch {
      toast({ title: "Error", description: "Failed to create API key", variant: "destructive" });
    }
  };

  const handleRevokeKey = async (id: number) => {
    try {
      await coreApi.revokeApiKey(id);
      fetchApiKeys();
      toast({ title: "API Key Revoked" });
    } catch {
      toast({ title: "Error", description: "Failed to revoke key", variant: "destructive" });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>API Keys</CardTitle>
        <CardDescription>Manage API keys for CI/CD and external access.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {generatedKey && (
          <div className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg">
            <p className="text-sm font-semibold text-green-600 mb-2">New API Key Generated</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 p-2 bg-background border rounded font-mono text-sm">{generatedKey}</code>
              <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(generatedKey)}>
                <Copy className="w-4 h-4" />
              </Button>
            </div>
            <p className="text-xs text-muted-foreground mt-2">Save this key now. It won&apos;t be shown again.</p>
          </div>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="space-y-2 flex-1">
            <Label>New Key Name</Label>
            <Input value={newKeyName} onChange={(e) => setNewKeyName(e.target.value)} placeholder="e.g. GitHub Actions" />
          </div>
          <Button onClick={handleCreateApiKey}>Create Key</Button>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Prefix</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Last Used</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {apiKeys.map((key) => (
              <TableRow key={key.id}>
                <TableCell className="font-medium">{key.name}</TableCell>
                <TableCell className="font-mono text-xs">{key.prefix}...</TableCell>
                <TableCell>{new Date(key.created_at).toLocaleDateString()}</TableCell>
                <TableCell>{key.last_used ? new Date(key.last_used).toLocaleDateString() : "Never"}</TableCell>
                <TableCell>
                  <Button variant="ghost" size="sm" onClick={() => handleRevokeKey(key.id)} className="text-red-500 hover:text-red-600">
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {apiKeys.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-6 text-muted-foreground">No API keys found.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
