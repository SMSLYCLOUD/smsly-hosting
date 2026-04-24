import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loader2, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";

interface RiskReport {
    risk_level: string;
    risk_score: number;
    summary: string;
    reasons: string[];
    recommendations: string[];
}

interface PreviewEnvironment {
    id: string;
    branch_name: string;
    commit_sha: string;
    status: string;
    preview_url?: string;
    migration_validation?: {
        risk_level: string;
        risk_score: number;
        summary: string;
        reasons: string[];
        recommendations: string[];
        requires_manual_approval: boolean;
    };
}

export function SafeDeployPanel({ serviceId, preview }: { serviceId: string, preview: PreviewEnvironment }) {
    const [isApproving, setIsApproving] = useState(false);

    const handleApprove = async () => {
        setIsApproving(true);
        try {
            await fetch(`/api/v1/services/${serviceId}/approvals/mock-deploy-id/approve/`, { method: 'POST' });
            alert("Deployment Approved!");
        } catch (e) {
            alert("Approval failed.");
        } finally {
            setIsApproving(false);
        }
    };

    const riskColor = (level: string) => {
        switch(level) {
            case 'LOW': return 'bg-green-100 text-green-800 border-green-300';
            case 'MEDIUM': return 'bg-yellow-100 text-yellow-800 border-yellow-300';
            case 'HIGH': return 'bg-orange-100 text-orange-800 border-orange-300';
            case 'CRITICAL': return 'bg-red-100 text-red-800 border-red-300';
            default: return 'bg-gray-100 text-gray-800';
        }
    };

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader>
                    <CardTitle>Branch Preview: {preview.branch_name}</CardTitle>
                    <CardDescription>Commit: {preview.commit_sha.substring(0,7)}</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center space-x-2 mb-4">
                        <span className="font-semibold">Status:</span>
                        <Badge variant="outline">{preview.status}</Badge>
                        {preview.preview_url && (
                            <a href={preview.preview_url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline text-sm ml-4">
                                View Preview ↗
                            </a>
                        )}
                    </div>

                    {preview.migration_validation && (
                        <div className="mt-6 border rounded-md p-4 bg-slate-50">
                            <h3 className="text-lg font-medium mb-2 flex items-center gap-2">
                                <AlertTriangle className="h-5 w-5 text-slate-600" />
                                Migration Risk Report
                            </h3>
                            <Badge className={riskColor(preview.migration_validation.risk_level)}>
                                {preview.migration_validation.risk_level} RISK (Score: {preview.migration_validation.risk_score})
                            </Badge>

                            <p className="mt-2 text-sm text-slate-700">{preview.migration_validation.summary}</p>

                            {preview.migration_validation.reasons.length > 0 && (
                                <div className="mt-4">
                                    <h4 className="text-sm font-semibold mb-1">Detected Issues:</h4>
                                    <ul className="list-disc pl-5 text-sm text-slate-600 space-y-1">
                                        {preview.migration_validation.reasons.map((r, i) => <li key={i}>{r}</li>)}
                                    </ul>
                                </div>
                            )}

                            {preview.migration_validation.recommendations.length > 0 && (
                                <div className="mt-4">
                                    <h4 className="text-sm font-semibold mb-1">Recommendations:</h4>
                                    <ul className="list-disc pl-5 text-sm text-slate-600 space-y-1">
                                        {preview.migration_validation.recommendations.map((r, i) => <li key={i}>{r}</li>)}
                                    </ul>
                                </div>
                            )}

                            {preview.migration_validation.requires_manual_approval && (
                                <div className="mt-6 flex space-x-3">
                                    <Button onClick={handleApprove} disabled={isApproving} className="bg-green-600 hover:bg-green-700 text-white">
                                        {isApproving ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> : <CheckCircle2 className="h-4 w-4 mr-2"/>}
                                        Approve Production Deploy
                                    </Button>
                                    <Button variant="destructive">
                                        <XCircle className="h-4 w-4 mr-2"/>
                                        Reject
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
