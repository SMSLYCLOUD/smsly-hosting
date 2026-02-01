"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Rocket, Clock, CheckCircle, XCircle, Loader2 } from "lucide-react";
import api from "@/lib/api";
import { motion } from "framer-motion";

interface Deployment {
  id: string;
  service_name?: string;
  status: string;
  created_at: string;
  commit_hash?: string;
}

export default function DeploymentsPage() {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchDeployments = async () => {
      try {
        const res = await api.get("/deployments/");
        const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
        setDeployments(data);
      } catch (err) {
        console.error("Failed to fetch deployments:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchDeployments();
  }, []);

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
      RUNNING: "bg-emerald-500/20 text-emerald-500",
      SUCCESS: "bg-emerald-500/20 text-emerald-500",
      FAILED: "bg-red-500/20 text-red-500",
      BUILDING: "bg-blue-500/20 text-blue-500",
      PENDING: "bg-yellow-500/20 text-yellow-500",
    };
    return colors[status?.toUpperCase()] || "bg-gray-500/20 text-gray-500";
  };

  if (loading) {
    return (
      <div className="container mx-auto py-10">
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-10">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h1 className="text-3xl font-bold flex items-center gap-3">
          <Rocket className="h-8 w-8 text-primary" />
          Deployments
        </h1>
        <p className="text-muted-foreground">Track all your deployment history.</p>
      </motion.div>

      {deployments.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Rocket className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <h3 className="font-semibold text-lg mb-2">No deployments yet</h3>
            <p className="text-muted-foreground">Deploy your first service to see activity here.</p>
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
              <Card className="hover:border-primary/30 transition-colors">
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg flex items-center gap-2">
                      {getStatusIcon(deploy.status)}
                      {deploy.service_name || `Deployment ${deploy.id.slice(0, 8)}`}
                    </CardTitle>
                    <Badge className={getStatusBadge(deploy.status)}>
                      {deploy.status}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <span>ID: {deploy.id.slice(0, 12)}...</span>
                    {deploy.commit_hash && (
                      <span>Commit: {deploy.commit_hash.slice(0, 7)}</span>
                    )}
                    <span>{new Date(deploy.created_at).toLocaleString()}</span>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  );
}
