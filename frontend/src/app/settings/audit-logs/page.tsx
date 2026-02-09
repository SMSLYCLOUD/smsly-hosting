"use client"

import * as React from "react"
import { Activity, Search, ShieldAlert, Info, Database, Server } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { formatDistanceToNow } from "date-fns"

interface AuditLog {
    id: string
    actor: string
    action: string
    target: string
    timestamp: string
    metadata: any
    hash: string
}

export default function AuditLogsPage() {
    const [logs, setLogs] = React.useState<AuditLog[]>([])
    const [isLoading, setIsLoading] = React.useState(true)
    const [searchTerm, setSearchTerm] = React.useState("")

    React.useEffect(() => {
        // Mock data for now until API integration
        setTimeout(() => {
            setLogs([
                {
                    id: "1",
                    actor: "osaretin",
                    action: "DEPLOY_TRIGGER",
                    target: "Service: smsly-frontend",
                    timestamp: new Date().toISOString(),
                    metadata: { commit: "a1b2c3d" },
                    hash: "0000abc..."
                },
                {
                    id: "2",
                    actor: "system",
                    action: "SCALE_UP",
                    target: "Service: smsly-backend",
                    timestamp: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
                    metadata: { reason: "CPU > 80%" },
                    hash: "0000def..."
                },
                 {
                    id: "3",
                    actor: "jules",
                    action: "ENV_VAR_UPDATE",
                    target: "Service: smsly-hosting",
                    timestamp: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
                    metadata: { key: "DATABASE_URL" },
                    hash: "0000123..."
                }
            ])
            setIsLoading(false)
        }, 800)
    }, [])

    const filteredLogs = logs.filter(log => 
        log.action.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.actor.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.target.toLowerCase().includes(searchTerm.toLowerCase())
    )

    const getIcon = (action: string) => {
        if (action.includes("DEPLOY")) return <Server className="h-4 w-4" />
        if (action.includes("ENV")) return <ShieldAlert className="h-4 w-4" />
        if (action.includes("SCALE")) return <Activity className="h-4 w-4" />
        return <Info className="h-4 w-4" />
    }

    if (isLoading) return <div className="p-8 text-center text-muted-foreground">Loading audit trail...</div>

    return (
        <div className="container py-8 max-w-5xl space-y-8">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Audit Logs</h1>
                <p className="text-muted-foreground">Immutable record of all system activities.</p>
            </div>

            <div className="relative">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input 
                    placeholder="Search by actor, action, or target..." 
                    className="pl-8"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                />
            </div>

            <Card className="bio-card">
                <CardHeader>
                    <CardTitle>Activity Stream</CardTitle>
                    <CardDescription>
                        All actions are cryptographically hashed and linked.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <ScrollArea className="h-[600px] pr-4">
                        <div className="space-y-4">
                            {filteredLogs.map((log) => (
                                <div key={log.id} className="flex items-start gap-4 p-4 rounded-lg bg-muted/50 border border-border/50 hover:bg-muted transition-colors">
                                    <div className="mt-1 h-8 w-8 rounded bg-primary/10 text-primary flex items-center justify-center">
                                        {getIcon(log.action)}
                                    </div>
                                    <div className="flex-1 space-y-1">
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-medium leading-none">
                                                <span className="font-bold text-primary">{log.actor}</span> performed <span className="font-mono">{log.action}</span>
                                            </p>
                                            <span className="text-xs text-muted-foreground tabular-nums">
                                                {formatDistanceToNow(new Date(log.timestamp), { addSuffix: true })}
                                            </span>
                                        </div>
                                        <p className="text-xs text-muted-foreground">Target: {log.target}</p>
                                        <div className="flex items-center gap-2 mt-2">
                                            <Badge variant="outline" className="font-mono text-[10px] opacity-70">
                                                HASH: {log.hash.substring(0, 12)}...
                                            </Badge>
                                            {Object.entries(log.metadata).map(([k, v]) => (
                                                <Badge key={k} variant="secondary" className="text-[10px]">
                                                    {k}: {String(v)}
                                                </Badge>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </ScrollArea>
                </CardContent>
            </Card>
        </div>
    )
}
