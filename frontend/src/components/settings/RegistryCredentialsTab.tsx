"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { registryCredentialsApi } from "@/lib/api";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Trash2, Plus, Loader2, Check } from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";

export function RegistryCredentialsTab() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [credentials, setCredentials] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "",
    provider: "dockerhub",
    registry_url: "",
    username: "",
    password: ""
  });

  const fetchCredentials = useCallback(async () => {
    try {
      setLoading(true);
      const data = await registryCredentialsApi.list();
      setCredentials(data);
    } catch (err) {
      toast({ title: "Error", description: "Failed to load registry credentials", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchCredentials();
  }, [fetchCredentials]);

  const handleProviderChange = (val: string) => {
    setForm(prev => {
      let url = prev.registry_url;
      if (val === "dockerhub") url = "docker.io";
      else if (val === "ghcr") url = "ghcr.io";
      else if (val === "gcr") url = "gcr.io";
      else url = "";
      return { ...prev, provider: val, registry_url: url };
    });
  };

  const handleCreate = async () => {
    if (!form.name || !form.username || !form.password) {
      toast({ title: "Missing fields", description: "Please fill out all required fields", variant: "destructive" });
      return;
    }
    try {
      setAdding(true);
      await registryCredentialsApi.create(form);
      toast({ title: "Success", description: "Registry credential added." });
      setForm({ name: "", provider: "dockerhub", registry_url: "docker.io", username: "", password: "" });
      fetchCredentials();
    } catch (err) {
      toast({ title: "Error", description: "Failed to add registry credential", variant: "destructive" });
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!(await confirm({ title: "Delete Credential?", message: "Are you sure you want to remove this registry credential?" }))) return;
    try {
      await registryCredentialsApi.delete(id);
      toast({ title: "Deleted", description: "Registry credential removed." });
      fetchCredentials();
    } catch (err) {
      toast({ title: "Error", description: "Failed to delete credential", variant: "destructive" });
    }
  };

  const handleTest = async (id: string) => {
    try {
      setTesting(id);
      const res = await registryCredentialsApi.testConnection(id);
      toast({ title: "Success", description: res.message || "Connection successful" });
    } catch (err: any) {
      toast({ title: "Test Failed", description: err.response?.data?.message || "Failed to connect", variant: "destructive" });
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Registry Credentials</CardTitle>
          <CardDescription>
            Manage private container registry credentials for deploying private images.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center p-6"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
          ) : credentials.length === 0 ? (
            <div className="text-center text-sm text-muted-foreground py-6 border rounded-md">No registry credentials configured.</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>Username</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credentials.map((cred) => (
                  <TableRow key={cred.id}>
                    <TableCell className="font-medium">{cred.name}</TableCell>
                    <TableCell><Badge variant="outline">{cred.provider}</Badge></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{cred.registry_url || "-"}</TableCell>
                    <TableCell>{cred.username}</TableCell>
                    <TableCell className="text-right flex justify-end gap-2">
                      <Button size="sm" variant="outline" onClick={() => handleTest(cred.id)} disabled={testing === cred.id}>
                        {testing === cred.id ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Check className="h-3 w-3 mr-1" />}
                        Test
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => handleDelete(cred.id)} className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Add Registry Credential</CardTitle>
          <CardDescription>Add new credentials to pull from a private registry.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Friendly Name</Label>
              <Input placeholder="e.g. My ECR" value={form.name} onChange={e => setForm({...form, name: e.target.value})} />
            </div>
            <div className="space-y-2">
              <Label>Provider</Label>
              <Select value={form.provider} onValueChange={handleProviderChange}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="dockerhub">Docker Hub</SelectItem>
                  <SelectItem value="ghcr">GitHub Container Registry</SelectItem>
                  <SelectItem value="ecr">AWS ECR</SelectItem>
                  <SelectItem value="gcr">Google Container Registry</SelectItem>
                  <SelectItem value="acr">Azure Container Registry</SelectItem>
                  <SelectItem value="custom">Custom Registry</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Registry URL</Label>
              <Input placeholder="e.g. 1234.dkr.ecr.region.amazonaws.com" value={form.registry_url} onChange={e => setForm({...form, registry_url: e.target.value})} disabled={["dockerhub", "ghcr", "gcr"].includes(form.provider)} />
            </div>
            <div className="space-y-2">
              <Label>Username</Label>
              <Input placeholder="e.g. AWS, or github username" value={form.username} onChange={e => setForm({...form, username: e.target.value})} />
            </div>
            <div className="space-y-2 col-span-2">
              <Label>Password / Token</Label>
              <Input type="password" placeholder="Password, PAT, or IAM Token" value={form.password} onChange={e => setForm({...form, password: e.target.value})} />
            </div>
          </div>
        </CardContent>
        <CardFooter>
          <Button onClick={handleCreate} disabled={adding}>
            {adding ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Plus className="h-4 w-4 mr-2" />}
            Add Credential
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
