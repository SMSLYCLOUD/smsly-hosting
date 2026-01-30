"use client";

import { useState, useEffect } from "react";
import axios from "axios";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Github, Box, Loader2 } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";

interface CloudProvider {
  id: string;
  name: string;
  provider_type: string;
  region: string;
}

export default function NewServicePage() {
  const router = useRouter();
  const { toast } = useToast();

  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [providers, setProviders] = useState<CloudProvider[]>([]);

  // Form State
  const [deployType, setDeployType] = useState<"GIT" | "DOCKER">("GIT");
  const [repoUrl, setRepoUrl] = useState("");
  const [dockerImage, setDockerImage] = useState("");
  const [serviceName, setServiceName] = useState("");
  const [providerId, setProviderId] = useState("");

  // Fetch Providers on Mount
  useEffect(() => {
    const fetchProviders = async () => {
        try {
            const res = await axios.get("/api/v1/cloud/providers/");
            setProviders(res.data.results || res.data); // Handle pagination or list
        } catch (error) {
            console.error("Failed to fetch providers", error);
            toast({ title: "Error", description: "Could not load cloud providers.", variant: "destructive" });
        }
    };
    fetchProviders();
  }, [toast]);

  const handleDeploy = async () => {
    if (!serviceName || !providerId) {
        toast({ title: "Error", description: "Please fill all required fields.", variant: "destructive" });
        return;
    }

    setLoading(true);
    try {
        // 1. Create Service Record
        const serviceRes = await axios.post("/api/v1/services/", {
            name: serviceName,
            deploy_type: deployType,
            repository_url: deployType === 'GIT' ? repoUrl : null,
            docker_image: deployType === 'DOCKER' ? dockerImage : null,
            provider: providerId // Updated field name to match model
        });

        const serviceId = serviceRes.data.id;

        // 2. Trigger Deployment
        await axios.post("/api/v1/deployments/trigger/", {
            service_id: serviceId,
            provider_id: providerId
        });

        toast({ title: "Success", description: "Deployment triggered successfully!" });
        router.push(`/services/${serviceId}`);

    } catch (error: any) {
        console.error(error);
        toast({
            title: "Deployment Failed",
            description: error.response?.data?.message || "Something went wrong.",
            variant: "destructive"
        });
    } finally {
        setLoading(false);
    }
  };

  return (
    <div className="container mx-auto max-w-4xl py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Deploy New Service</h1>
        <p className="text-muted-foreground">Get your code running in minutes.</p>
      </div>

      <Tabs defaultValue="git" className="w-full">
        <TabsList className="grid w-full grid-cols-2 mb-8">
          <TabsTrigger value="git" onClick={() => setDeployType("GIT")}>
            <Github className="mr-2 h-4 w-4" /> Git Repository
          </TabsTrigger>
          <TabsTrigger value="docker" onClick={() => setDeployType("DOCKER")}>
            <Box className="mr-2 h-4 w-4" /> Docker Image
          </TabsTrigger>
        </TabsList>

        <Card>
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>Configure your service settings and target provider.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">

            {deployType === "GIT" ? (
                <div className="space-y-2">
                    <Label htmlFor="repo">Repository URL</Label>
                    <Input
                        id="repo"
                        placeholder="https://github.com/username/repo"
                        value={repoUrl}
                        onChange={(e) => setRepoUrl(e.target.value)}
                    />
                </div>
            ) : (
                <div className="space-y-2">
                    <Label htmlFor="image">Docker Image</Label>
                    <Input
                        id="image"
                        placeholder="nginx:latest"
                        value={dockerImage}
                        onChange={(e) => setDockerImage(e.target.value)}
                    />
                </div>
            )}

            <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                    <Label>Service Name</Label>
                    <Input
                        placeholder="my-awesome-app"
                        value={serviceName}
                        onChange={(e) => setServiceName(e.target.value)}
                    />
                </div>
                <div className="space-y-2">
                    <Label>Target Provider</Label>
                    <Select onValueChange={setProviderId}>
                        <SelectTrigger>
                            <SelectValue placeholder="Select Provider" />
                        </SelectTrigger>
                        <SelectContent>
                            {providers.length === 0 ? (
                                <SelectItem value="none" disabled>No providers available</SelectItem>
                            ) : (
                                providers.map((provider) => (
                                    <SelectItem key={provider.id} value={provider.id}>
                                        {provider.name} ({provider.provider_type}) - {provider.region}
                                    </SelectItem>
                                ))
                            )}
                        </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground mt-1">
                        Don't see your provider? <a href="/settings" className="underline">Add one in Settings</a>.
                    </p>
                </div>
            </div>

          </CardContent>
          <CardFooter className="flex justify-between">
            <Button variant="outline" disabled={loading}>Cancel</Button>
            <Button onClick={handleDeploy} disabled={loading}>
                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {loading ? "Deploying..." : "Deploy Service"}
            </Button>
          </CardFooter>
        </Card>
      </Tabs>
    </div>
  );
}
