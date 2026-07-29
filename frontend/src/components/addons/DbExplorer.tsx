"use client";

import { useState } from "react";
import { addonsApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Play, Loader2, Database } from "lucide-react";

interface DbExplorerProps {
    addonId: string;
}

export function DbExplorer({ addonId }: DbExplorerProps) {
    const [query, setQuery] = useState("SELECT * FROM information_schema.tables LIMIT 10;");
    const [results, setResults] = useState<Record<string, unknown>[]>([]);
    const [columns, setColumns] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleRunQuery = async () => {
        setLoading(true);
        setError(null);
        setResults([]);
        setColumns([]);
        try {
            const data = await addonsApi.runQuery(addonId, query);
            if (data.error) {
                setError(data.error);
            } else {
                setResults(data.results || []);
                setColumns(data.columns || []);
            }
        } catch (err: any) {
            setError(err.response?.data?.error || "Query failed to execute.");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h3 className="text-lg font-medium flex items-center gap-2">
                    <Database className="w-5 h-5 text-blue-500" />
                    SQL Explorer (Read-Only)
                </h3>
            </div>

            <div className="relative">
                <Textarea
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="SELECT * FROM ..."
                    className="font-mono text-sm min-h-[120px] bg-secondary/30"
                />
                <div className="absolute bottom-2 right-2">
                    <Button size="sm" onClick={handleRunQuery} disabled={loading || !query.trim()}>
                        {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 mr-1" />}
                        Run
                    </Button>
                </div>
            </div>

            {error && (
                <Alert variant="destructive">
                    <AlertTitle>Error</AlertTitle>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}

            {columns.length > 0 && (
                <div className="border rounded-md overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                {columns.map((col) => (
                                    <TableHead key={col} className="whitespace-nowrap">{col}</TableHead>
                                ))}
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {results.map((row, i) => (
                                <TableRow key={i}>
                                    {columns.map((col) => (
                                        <TableCell key={`${i}-${col}`} className="font-mono text-xs whitespace-nowrap">
                                            {typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col])}
                                        </TableCell>
                                    ))}
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {!loading && !error && results.length === 0 && columns.length > 0 && (
                <div className="text-center py-8 text-muted-foreground">
                    No results found.
                </div>
            )}
        </div>
    );
}
