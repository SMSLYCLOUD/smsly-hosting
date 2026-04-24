import React, { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { SafeDeployPanel } from "./SafeDeployPanel";

export function PreviewsList({ serviceId }: { serviceId: string }) {
    const [previews, setPreviews] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedPreview, setSelectedPreview] = useState<any | null>(null);

    useEffect(() => {
        setTimeout(() => {
            setPreviews([
                {
                    id: '123',
                    branch_name: 'feature/new-billing',
                    commit_sha: 'a1b2c3d',
                    status: 'READY',
                    preview_url: 'https://feature-new-billing--myapp.preview.domain.com',
                    migration_validation: {
                        risk_level: 'HIGH',
                        risk_score: 85,
                        summary: 'Migration contains destructive operations.',
                        reasons: ['Contains RemoveField for users.legacy_email', 'Contains RunPython'],
                        recommendations: ['Separate destructive changes into a contract deployment.', 'Requires manual approval before production merge.'],
                        requires_manual_approval: true
                    }
                },
                {
                    id: '124',
                    branch_name: 'bugfix/header-typo',
                    commit_sha: 'e4f5g6h',
                    status: 'READY',
                    preview_url: 'https://bugfix-header-typo--myapp.preview.domain.com',
                    migration_validation: {
                        risk_level: 'LOW',
                        risk_score: 0,
                        summary: 'No database schema changes detected.',
                        reasons: [],
                        recommendations: [],
                        requires_manual_approval: false
                    }
                }
            ]);
            setLoading(false);
        }, 500);
    }, [serviceId]);

    if (loading) return <div>Loading previews...</div>;

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
