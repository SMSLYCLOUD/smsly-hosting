"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Rocket, Clock, CheckCircle, XCircle, Loader2, RefreshCw, ExternalLink } from "lucide-react";
import api from "@/lib/api";
import { motion } from "framer-motion";
import { DashboardShell } from "@/components/layout/DashboardShell";

interface Deployment {
  id: string;
  service_name?: string;
  service?: string;  // FK UUID from backend
  status: string;
  created_at: string;
  commit_hash?: string;
  logs_url?: string;
}

export default function DeploymentsPage() {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchDeployments = async () => {
    try {
      const res = await api.get("/deployments/");
      const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
      setDeployments(data);
    } catch (err) {
      console.error("Failed to fetch deployments:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchDeployments();
    const interval = setInterval(fetchDeployments, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchDeployments();
  };

  const getStatusIcon = (status: string) => {
    switch (status?.toUpperCase()) {
      case "RUNNING":
      case "SUCCESS":
        return <CheckCircle className="h-4 w-4 text-emerald-500" />;
      case "FAILED":
        return <XCircle className="h-4 w-4 text-red-500" />;
      case "BUILDING":
      case "PENDING":
        return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />;
      default:
        return <Clock className="h-4 w-4 text-gray-500" />;
    }
  };

  const getStatusBadge = (status: string) => {
    const colors: Record<string, string> = {
      RUNNING: "bg-emerald-500/20 text-emerald-500 border-emerald-500/30",
      SUCCESS: "bg-emerald-500/20 text-emerald-500 border-emerald-500/30",
      FAILED: "bg-red-500/20 text-red-500 border-red-500/30",
      BUILDING: "bg-blue-500/20 text-blue-500 border-blue-500/30",
      PENDING: "bg-yellow-500/20 text-yellow-500 border-yellow-500/30",
    };
    return colors[status?.toUpperCase()] || "bg-gray-500/20 text-gray-500 border-gray-500/30";
  };

  if (loading) {
    return (
      <DashboardShell>
        <div className="container mx-auto py-10 relative z-10">
          <div className="flex items-center justify-center h-64">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
    <div className="container mx-auto py-10 relative z-10">
      <PageHeader
        title="Deployments"
        description="Track all your deployment history and status."
        icon={<Rocket className="h-8 w-8 text-primary" />}
        breadcrumbs={[{ label: "Deployments" }]}
        backHref="/dashboard"
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
              <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Link href="/new">
              <Button size="sm">
                <Rocket className="h-4 w-4 mr-2" />
                New Deployment
              </Button>
            </Link>
          </div>
        }
      />

      {deployments.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Rocket className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <h3 className="font-semibold text-lg mb-2">No deployments yet</h3>
            <p className="text-muted-foreground mb-4">Deploy your first service to see activity here.</p>
            <Link href="/new">
              <Button>
                <Rocket className="h-4 w-4 mr-2" />
                Create First Deployment
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {deployments.map((deploy, i) => (
            <motion.div
              key={deploy.id}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
            >
              <Link href={`/deployments/${deploy.id}`} className="block">
                <Card className="hover:border-primary/30 transition-colors cursor-pointer hover:shadow-lg hover:shadow-primary/5">
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-lg flex items-center gap-2">
                        {getStatusIcon(deploy.status)}
                        {deploy.service_name || `Deployment ${deploy.id.slice(0, 8)}`}
                      </CardTitle>
                      <div className="flex items-center gap-2">
                        <Badge className={getStatusBadge(deploy.status)}>
                          {deploy.status}
                        </Badge>
                        <ExternalLink className="h-4 w-4 text-muted-foreground" />
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <span>ID: {deploy.id.slice(0, 12)}...</span>
                      {deploy.commit_hash && (
                        <span className="font-mono bg-muted px-2 py-0.5 rounded">
                          {deploy.commit_hash.slice(0, 7)}
                        </span>
                      )}
                      <span>{new Date(deploy.created_at).toLocaleString()}</span>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            </motion.div>
          ))}
        </div>
      )}
    </div>
    </DashboardShell>
  );
}
