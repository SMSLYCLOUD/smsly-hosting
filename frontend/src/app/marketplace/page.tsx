"use client";

import { useEffect, useState } from "react";
import axios from "axios";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Layers, ArrowRight, Loader2 } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function MarketplacePage() {
  const { toast } = useToast();
  const [blueprints, setBlueprints] = useState<any[]>([]);
  const [providers, setProviders] = useState<any[]>([]);
  const [selectedProvider, setSelectedProvider] = useState("");
  const [deploying, setDeploying] = useState(false);

  useEffect(() => {
    const fetchData = async () => {
        const [bpRes, provRes] = await Promise.all([
            axios.get("/api/v1/blueprints/"),
            axios.get("/api/v1/cloud/providers/")
        ]);
        setBlueprints(bpRes.data);
        setProviders(provRes.data.results || provRes.data);
    };
    fetchData();
  }, []);

  const handleDeploy = async (blueprintId: string) => {
    if (!selectedProvider) {
        toast({ title: "Select Provider", description: "Please choose where to deploy.", variant: "destructive" });
        return;
    }
    setDeploying(true);
    try {
        await axios.post("/api/v1/blueprints/deploy/", {
            blueprint_id: blueprintId,
            provider_id: selectedProvider
        });
        toast({ title: "Deployment Started", description: "Your ecosystem is being provisioned." });
    } catch (e) {
        toast({ title: "Error", description: "Failed to start deployment.", variant: "destructive" });
    } finally {
        setDeploying(false);
    }
  };

  return (
    <div className="container mx-auto py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Marketplace</h1>
        <p className="text-muted-foreground">One-click templates for complex architectures.</p>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {blueprints.map((bp) => (
            <Card key={bp.id} className="flex flex-col">
                <CardHeader>
                    <div className="flex items-center space-x-2">
                        <div className="p-2 bg-indigo-100 dark:bg-indigo-900 rounded-lg">
                            <Layers className="h-6 w-6 text-indigo-600 dark:text-indigo-400" />
                        </div>
                        <CardTitle>{bp.name}</CardTitle>
                    </div>
                    <CardDescription>{bp.description}</CardDescription>
                </CardHeader>
                <CardContent className="flex-1">
                    <div className="text-sm text-muted-foreground">
                        Includes: Backend, Identity, SMS, Voice, Video, Gateway + DBs.
                    </div>
                </CardContent>
                <CardFooter>
                    <Dialog>
                        <DialogTrigger asChild>
                            <Button className="w-full">
                                Deploy <ArrowRight className="ml-2 h-4 w-4" />
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>Deploy {bp.name}</DialogTitle>
                                <DialogDescription>
                                    Choose a target provider for this stack.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="py-4">
                                <Select onValueChange={setSelectedProvider}>
                                    <SelectTrigger>
                                        <SelectValue placeholder="Select Provider" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {providers.map((p) => (
                                            <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <DialogFooter>
                                <Button onClick={() => handleDeploy(bp.id)} disabled={deploying}>
                                    {deploying && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                    Confirm Deployment
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                </CardFooter>
            </Card>
        ))}
      </div>
    </div>
  );
}
