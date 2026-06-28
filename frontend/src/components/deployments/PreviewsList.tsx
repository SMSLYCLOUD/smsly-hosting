import React, { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { SafeDeployPanel } from "./SafeDeployPanel";
import { servicesApi, PreviewEnvironment } from "@/lib/api";
import { toast } from "@/components/ui/use-toast";
import { Loader2 } from "lucide-react";

export function PreviewsList({ serviceId }: { serviceId: string }) {
    const [previews, setPreviews] = useState<PreviewEnvironment[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedPreview, setSelectedPreview] = useState<PreviewEnvironment | null>(null);

    useEffect(() => {
        const fetchPreviews = async () => {
            try {
                const data = await servicesApi.getPreviews(serviceId);
                setPreviews(data);
            } catch (err) {
                console.error("Failed to load previews:", err);
                toast({
                    title: "Error",
                    description: "Failed to load preview environments",
                    variant: "destructive",
                });
            } finally {
                setLoading(false);
            }
        };
        fetchPreviews();
    }, [serviceId]);

    if (loading) return <div className="flex justify-center p-8"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;

    if (selectedPreview) {
        return (
            <div>
                <button onClick={() => setSelectedPreview(null)} className="mb-4 text-sm text-slate-500 hover:text-slate-900">
                    ← Back to Previews List
                </button>
                <SafeDeployPanel serviceId={serviceId} preview={selectedPreview} />
            </div>
        );
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Branch Previews</CardTitle>
            </CardHeader>
            <CardContent>
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Branch</TableHead>
                            <TableHead>Commit</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead>Risk Level</TableHead>
                            <TableHead>Actions</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {previews.map(p => (
                            <TableRow key={p.id}>
                                <TableCell className="font-medium">{p.branch_name}</TableCell>
                                <TableCell>{p.commit_sha}</TableCell>
                                <TableCell><Badge variant="secondary">{p.status}</Badge></TableCell>
                                <TableCell>
                                    {p.migration_validation && (
                                        <Badge variant="outline">{p.migration_validation.risk_level}</Badge>
                                    )}
                                </TableCell>
                                <TableCell>
                                    <button
                                        className="text-blue-600 hover:underline text-sm"
                                        onClick={() => setSelectedPreview(p)}
                                    >
                                        View Details
                                    </button>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </CardContent>
        </Card>
    );
}
