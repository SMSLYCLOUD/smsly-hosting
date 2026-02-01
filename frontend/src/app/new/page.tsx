"use client";

import { useState, useEffect, Suspense } from "react";
import api from "@/lib/api";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Github, Box, Loader2, CheckCircle } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";

interface CloudProvider {
  id: string;
  name: string;
  provider_type: string;
  region: string;
}

// Docker image mappings for marketplace templates
const TEMPLATE_CONFIGS: Record<string, { image: string; port: number; name: string }> = {
  // Databases
  'postgres': { image: 'postgres:16-alpine', port: 5432, name: 'PostgreSQL' },
  'redis': { image: 'redis:7-alpine', port: 6379, name: 'Redis' },
  'mongodb': { image: 'mongo:7', port: 27017, name: 'MongoDB' },
  'mysql': { image: 'mysql:8.0', port: 3306, name: 'MySQL' },
  'mariadb': { image: 'mariadb:11', port: 3306, name: 'MariaDB' },
  'clickhouse': { image: 'clickhouse/clickhouse-server:latest', port: 8123, name: 'ClickHouse' },
  'influxdb': { image: 'influxdb:2-alpine', port: 8086, name: 'InfluxDB' },
  'elasticsearch': { image: 'docker.elastic.co/elasticsearch/elasticsearch:8.12.0', port: 9200, name: 'Elasticsearch' },
  'meilisearch': { image: 'getmeili/meilisearch:v1.6', port: 7700, name: 'Meilisearch' },
  'neo4j': { image: 'neo4j:5-community', port: 7474, name: 'Neo4j' },
  'cassandra': { image: 'cassandra:4', port: 9042, name: 'Cassandra' },
  'supabase': { image: 'supabase/postgres:15.1.0.147', port: 5432, name: 'Supabase' },
  // CMS
  'wordpress': { image: 'wordpress:6-apache', port: 80, name: 'WordPress' },
  'ghost': { image: 'ghost:5-alpine', port: 2368, name: 'Ghost' },
  'strapi': { image: 'strapi/strapi:4', port: 1337, name: 'Strapi' },
  'directus': { image: 'directus/directus:10', port: 8055, name: 'Directus' },
  'payload': { image: 'payloadcms/payload:latest', port: 3000, name: 'Payload CMS' },
  // Dev Tools
  'n8n': { image: 'n8nio/n8n:latest', port: 5678, name: 'n8n' },
  'gitea': { image: 'gitea/gitea:latest', port: 3000, name: 'Gitea' },
  'gitlab': { image: 'gitlab/gitlab-ce:latest', port: 80, name: 'GitLab' },
  'jenkins': { image: 'jenkins/jenkins:lts-jdk17', port: 8080, name: 'Jenkins' },
  'drone': { image: 'drone/drone:2', port: 80, name: 'Drone CI' },
  'sonarqube': { image: 'sonarqube:community', port: 9000, name: 'SonarQube' },
  'harbor': { image: 'goharbor/harbor-core:v2.10.0', port: 8080, name: 'Harbor' },
  'vault': { image: 'hashicorp/vault:1.15', port: 8200, name: 'Vault' },
  'minio': { image: 'minio/minio:latest', port: 9000, name: 'MinIO' },
  'registry': { image: 'registry:2', port: 5000, name: 'Docker Registry' },
  'portainer': { image: 'portainer/portainer-ce:latest', port: 9443, name: 'Portainer' },
  // Analytics
  'metabase': { image: 'metabase/metabase:latest', port: 3000, name: 'Metabase' },
  'grafana': { image: 'grafana/grafana-oss:latest', port: 3000, name: 'Grafana' },
  'prometheus': { image: 'prom/prometheus:latest', port: 9090, name: 'Prometheus' },
  'superset': { image: 'apache/superset:latest', port: 8088, name: 'Apache Superset' },
  'redash': { image: 'redash/redash:latest', port: 5000, name: 'Redash' },
  'umami': { image: 'ghcr.io/umami-software/umami:postgresql-latest', port: 3000, name: 'Umami' },
  'plausible': { image: 'plausible/analytics:latest', port: 8000, name: 'Plausible' },
  'matomo': { image: 'matomo:fpm-alpine', port: 80, name: 'Matomo' },
  'uptime-kuma': { image: 'louislam/uptime-kuma:1', port: 3001, name: 'Uptime Kuma' },
  // Communication
  'mattermost': { image: 'mattermost/mattermost-team-edition:latest', port: 8065, name: 'Mattermost' },
  'rocketchat': { image: 'rocket.chat:latest', port: 3000, name: 'Rocket.Chat' },
  'jitsi': { image: 'jitsi/web:stable', port: 443, name: 'Jitsi Meet' },
  'element': { image: 'vectorim/element-web:latest', port: 80, name: 'Element' },
  // Project Management
  'nextcloud': { image: 'nextcloud:28-apache', port: 80, name: 'Nextcloud' },
  'outline': { image: 'outlinewiki/outline:latest', port: 3000, name: 'Outline' },
  'bookstack': { image: 'lscr.io/linuxserver/bookstack:latest', port: 80, name: 'BookStack' },
  'focalboard': { image: 'mattermost/focalboard:latest', port: 8000, name: 'Focalboard' },
  'plane': { image: 'makeplane/plane-frontend:latest', port: 3000, name: 'Plane' },
  // Backend
  'appwrite': { image: 'appwrite/appwrite:1.5', port: 80, name: 'Appwrite' },
  'pocketbase': { image: 'ghcr.io/muchobien/pocketbase:latest', port: 8090, name: 'PocketBase' },
  'nocodb': { image: 'nocodb/nocodb:latest', port: 8080, name: 'NocoDB' },
};

function NewServiceContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { toast } = useToast();
  const templateId = searchParams.get('template');
  const templateConfig = templateId ? TEMPLATE_CONFIGS[templateId] : null;

  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [providers, setProviders] = useState<CloudProvider[]>([]);

  // Form State - auto-populate from template
  const [deployType, setDeployType] = useState<"GIT" | "DOCKER">(templateConfig ? "DOCKER" : "GIT");
  const [repoUrl, setRepoUrl] = useState("");
  const [dockerImage, setDockerImage] = useState(templateConfig?.image || "");
  const [serviceName, setServiceName] = useState(templateConfig ? templateId || "" : "");
  const [providerId, setProviderId] = useState("");

  // Fetch Providers on Mount
  useEffect(() => {
    const fetchProviders = async () => {
      try {
        const res = await api.get("/cloud/providers/");
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
      const serviceRes = await api.post("/services/", {
        name: serviceName,
        deploy_type: deployType,
        repository_url: deployType === 'GIT' ? repoUrl : null,
        docker_image: deployType === 'DOCKER' ? dockerImage : null,
        provider: providerId // Updated field name to match model
      });

      const serviceId = serviceRes.data.id;

      // 2. Trigger Deployment
      await api.post("/deployments/trigger/", {
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

      <Tabs defaultValue={templateConfig ? "docker" : "git"} className="w-full">
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
                  Don&apos;t see your provider? <a href="/settings" className="underline">Add one in Settings</a>.
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

// Wrap in Suspense for useSearchParams (Next.js 15 requirement)
export default function NewServicePage() {
  return (
    <Suspense fallback={<div className="container mx-auto max-w-4xl py-10"><p>Loading...</p></div>}>
      <NewServiceContent />
    </Suspense>
  );
}
