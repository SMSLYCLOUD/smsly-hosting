"use client"

import * as React from "react"
import { Database, Archive, Play, Trash2, StopCircle, RefreshCw, Loader2, ServerIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useToast } from "@/components/ui/use-toast"
import { cn } from "@/lib/utils"
import { DashboardShell } from "@/components/layout/DashboardShell"

// Types
interface Service {
    id: string
    name: string
}

interface Addon {
    id: string
    name: string
    addon_type: 'POSTGRES' | 'REDIS' | 'MYSQL' | 'MONGODB'
    status: 'PROVISIONING' | 'ACTIVE' | 'FAILED' | 'DELETED'
    connection_url?: string
    service: string // service id
}

interface Backup {
    id: string
    status: 'PENDING' | 'COMPLETED' | 'FAILED'
    size_bytes: number
    created_at: string
}

type AddonType = Addon["addon_type"]

const ADDON_TYPES = {
    POSTGRES: { name: "PostgreSQL", icon: "PG", desc: "Relational Database" },
    REDIS: { name: "Redis", icon: "RD", desc: "In-memory Store" },
    MYSQL: { name: "MySQL", icon: "MY", desc: "Relational Database" },
    MONGODB: { name: "MongoDB", icon: "MG", desc: "NoSQL Database" },
} as const satisfies Record<AddonType, { name: string; icon: string; desc: string }>

const ADDON_CATALOG = [
    // Databases
    { id: "postgres-16", addon_type: "POSTGRES" as AddonType, name: "PostgreSQL 16", desc: "Latest stable Postgres with JSONB, full-text search, and pgvector support.", icon: "PG" },
    { id: "postgres-timescale", addon_type: "POSTGRES" as AddonType, name: "TimescaleDB", desc: "Time-series extension for PostgreSQL. Ideal for IoT and analytics.", icon: "TS" },
    { id: "pgbouncer", addon_type: "POSTGRES" as AddonType, name: "PgBouncer", desc: "Lightweight connection pooler for PostgreSQL. Reduces connection overhead.", icon: "PB" },
    { id: "mysql-8", addon_type: "MYSQL" as AddonType, name: "MySQL 8.0", desc: "Reliable relational database with InnoDB and window functions.", icon: "MY" },
    { id: "mariadb-11", addon_type: "MYSQL" as AddonType, name: "MariaDB 11", desc: "MySQL-compatible with columnar storage and enhanced performance.", icon: "MA" },
    { id: "mongodb-7", addon_type: "MONGODB" as AddonType, name: "MongoDB 7", desc: "Document database with aggregation pipelines and change streams.", icon: "MG" },
    // Caching & Queues
    { id: "redis-7", addon_type: "REDIS" as AddonType, name: "Redis 7", desc: "In-memory data store for caching, sessions, and pub/sub.", icon: "RD" },
    { id: "redis-stack", addon_type: "REDIS" as AddonType, name: "Redis Stack", desc: "Redis with Search, JSON, Graph, and TimeSeries modules built-in.", icon: "RS" },
    { id: "memcached", addon_type: "REDIS" as AddonType, name: "Memcached", desc: "High-performance distributed memory cache. Simple key-value.", icon: "MC" },
    { id: "rabbitmq", addon_type: "REDIS" as AddonType, name: "RabbitMQ", desc: "Robust message broker with AMQP. Queues, routing, and dead-letter.", icon: "RQ" },
    { id: "nats", addon_type: "REDIS" as AddonType, name: "NATS", desc: "Lightweight, high-performance messaging for microservices.", icon: "NT" },
    // Search & Analytics
    { id: "elasticsearch", addon_type: "REDIS" as AddonType, name: "Elasticsearch", desc: "Full-text search and analytics engine. Log aggregation and APM.", icon: "ES" },
    { id: "meilisearch", addon_type: "REDIS" as AddonType, name: "Meilisearch", desc: "Lightning-fast, typo-tolerant search engine. Easy to set up.", icon: "MS" },
    { id: "clickhouse", addon_type: "POSTGRES" as AddonType, name: "ClickHouse", desc: "Column-oriented OLAP database for real-time analytics at scale.", icon: "CH" },
    // Storage & Other
    { id: "minio", addon_type: "REDIS" as AddonType, name: "MinIO", desc: "S3-compatible object storage. Store blobs, backups, and assets.", icon: "MN" },
    { id: "influxdb", addon_type: "POSTGRES" as AddonType, name: "InfluxDB", desc: "Purpose-built time-series database for metrics and monitoring.", icon: "IF" },
    { id: "valkey", addon_type: "REDIS" as AddonType, name: "Valkey", desc: "Open-source Redis fork. Drop-in compatible, community-driven.", icon: "VK" },
    { id: "dragonfly", addon_type: "REDIS" as AddonType, name: "Dragonfly", desc: "Modern in-memory store. 25x faster than Redis on a single node.", icon: "DF" },
    { id: "neo4j", addon_type: "MONGODB" as AddonType, name: "Neo4j", desc: "Graph database for relationship-heavy data. Cypher query language.", icon: "N4" },
    { id: "cassandra", addon_type: "MONGODB" as AddonType, name: "Cassandra", desc: "Distributed wide-column store. Massively scalable writes.", icon: "CS" },
]

export default function MarketplacePage() {
    const { toast } = useToast()
    const [addons, setAddons] = React.useState<Addon[]>([])
    const [services, setServices] = React.useState<Service[]>([])
    const [isLoading, setIsLoading] = React.useState(true)
    
    // One-click provisioning target (default to most recent service).
    const [targetServiceId, setTargetServiceId] = React.useState<string | null>(null)
    const [isProvisioning, setIsProvisioning] = React.useState<string | null>(null) // catalog id

    // Backups Modal State
    const [isBackupsOpen, setIsBackupsOpen] = React.useState(false)
    const [activeAddon, setActiveAddon] = React.useState<Addon | null>(null)
    const [backups, setBackups] = React.useState<Backup[]>([])
    const [isLoadingBackups, setIsLoadingBackups] = React.useState(false)

    // Initial Fetch
    React.useEffect(() => {
        const loadData = async () => {
            const token = localStorage.getItem("auth_token")
            if (!token) {
                setIsLoading(false)
                return
            }

            try {
                // Fetch Addons
                const addonsRes = await fetch("/api/v1/addons/", {
                    headers: { "Authorization": `Token ${token}` }
                })
                if (addonsRes.ok) {
                    const data = await addonsRes.json()
                    // DRF pagination returns { results: [...] }. Support both shapes.
                    const list = Array.isArray(data) ? data : (data?.results || [])
                    setAddons(Array.isArray(list) ? list : [])
                }

                // Fetch Services (for provisioning)
                const servicesRes = await fetch("/api/v1/services/", {
                    headers: { "Authorization": `Token ${token}` }
                })
                if (servicesRes.ok) {
                    const data = await servicesRes.json()
                    const list = Array.isArray(data) ? data : (data?.results || [])
                    const svcList = Array.isArray(list) ? list : []
                    setServices(svcList)
                    // Auto-select the most recently created service (API is ordered by -created_at).
                    if (svcList.length > 0) {
                        setTargetServiceId((prev) => prev || svcList[0].id)
                    }
                }
            } catch (err) {
                console.error(err)
            } finally {
                setIsLoading(false)
            }
        }
        loadData()
    }, [])

    const randomToken = (len: number = 6) => {
        const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        const bytes = new Uint8Array(len)
        crypto.getRandomValues(bytes)
        let out = ""
        for (let i = 0; i < bytes.length; i += 1) out += alphabet[bytes[i] % alphabet.length]
        return out
    }

    const handleOneClickProvision = async (item: (typeof ADDON_CATALOG)[number]) => {
        const token = localStorage.getItem("auth_token")
        if (!token) {
            toast({ title: "Login required", description: "Please login to provision addons.", variant: "destructive" })
            return
        }
        if (!targetServiceId) {
            toast({ title: "No service found", description: "Create a service first, then provision addons.", variant: "destructive" })
            return
        }

        setIsProvisioning(item.id)
        try {
            const res = await fetch("/api/v1/addons/", {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "Authorization": `Token ${token}` 
                },
                body: JSON.stringify({
                    name: `${item.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${randomToken(6)}`.slice(0, 255),
                    addon_type: item.addon_type,
                    service: targetServiceId
                })
            })
            if (!res.ok) throw new Error("Provisioning failed")
            
            const newAddon = await res.json()
            // Defensive: in case addons was set to a non-array by a backend shape change.
            setAddons((prev) => Array.isArray(prev) ? [...prev, newAddon] : [newAddon])
            toast({ title: "Started", description: "Provisioning initiated." })
        } catch (err) {
            toast({ title: "Error", description: "Failed to provision addon.", variant: "destructive" })
        } finally {
            setIsProvisioning(null)
        }
    }

    const loadBackups = async (addon: Addon) => {
        setActiveAddon(addon)
        setIsBackupsOpen(true)
        setIsLoadingBackups(true)
        const token = localStorage.getItem("auth_token")
        
        try {
            const res = await fetch(`/api/v1/addons/${addon.id}/backups/`, {
                headers: { "Authorization": `Token ${token}` }
            })
            if (res.ok) {
                const data = await res.json()
                const list = Array.isArray(data) ? data : (data?.results || [])
                setBackups(Array.isArray(list) ? list : [])
            }
        } catch (err) {
            console.error(err)
        } finally {
            setIsLoadingBackups(false)
        }
    }

    const handleCreateBackup = async () => {
        if (!activeAddon) return
        const token = localStorage.getItem("auth_token")
        try {
             const res = await fetch(`/api/v1/addons/${activeAddon.id}/backup/`, {
                method: "POST",
                headers: { "Authorization": `Token ${token}` }
            })
            if (res.ok) {
                toast({ title: "Backup Started", description: "Your backup is running in background." })
                // Refresh list after a delay
                setTimeout(() => loadBackups(activeAddon), 2000)
            }
        } catch (err) {
            toast({ title: "Error", description: "Failed to start backup.", variant: "destructive" })
        }
    }

    const handleRestore = async (backupId: string) => {
        if (!activeAddon) return
        if (!confirm("This will overwrite current data. Continue?")) return
        
        const token = localStorage.getItem("auth_token")
        try {
             const res = await fetch(`/api/v1/addons/${activeAddon.id}/restore/`, {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "Authorization": `Token ${token}`
                },
                body: JSON.stringify({ backup_id: backupId })
            })
            if (res.ok) {
                toast({ title: "Restore Started", description: "Database restoration in progress." })
            }
        } catch (err) {
             toast({ title: "Error", description: "Restore failed.", variant: "destructive" })
        }
    }

    const handleDownload = async (backup: Backup) => {
        if (!activeAddon) return
        const token = localStorage.getItem("auth_token")
        
        try {
            const res = await fetch(`/api/v1/addons/${activeAddon.id}/download_backup/?backup_id=${backup.id}`, {
                headers: { "Authorization": `Token ${token}` }
            })
            if (!res.ok) throw new Error("Download failed")
            
            const blob = await res.blob()
            const url = window.URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `backup-${activeAddon.addon_type.toLowerCase()}-${backup.created_at}.dump`
            document.body.appendChild(a)
            a.click()
            window.URL.revokeObjectURL(url)
        } catch (err) {
            toast({ title: "Error", description: "Download failed.", variant: "destructive" })
        }
    }

    if (isLoading) return <div className="p-8 text-center text-muted-foreground">Loading marketplace...</div>

    return (
        <DashboardShell>
        <div className="container py-8 space-y-8">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Marketplace</h1>
                <p className="text-muted-foreground">One-click databases and services.</p>
            </div>

            {/* Catalog */}
            <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold">Addon Catalog</h2>
                    <p className="text-xs text-muted-foreground">20 production-ready addons</p>
                </div>
                <div className="w-full sm:w-[340px] space-y-1">
                    <Label>Provision to service</Label>
                    <Select value={targetServiceId || undefined} onValueChange={setTargetServiceId}>
                        <SelectTrigger>
                            <SelectValue placeholder="Select a service" />
                        </SelectTrigger>
                        <SelectContent>
                            {services.map(s => (
                                <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {ADDON_CATALOG.map((item) => (
                    <Card key={item.id} className="hover:border-primary/50 transition-colors cursor-pointer" onClick={() => {
                        handleOneClickProvision(item)
                    }}>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">{item.name}</CardTitle>
                            <span className="text-sm font-mono">{item.icon}</span>
                        </CardHeader>
                        <CardContent>
                            <div className="text-xs text-muted-foreground">{item.desc}</div>
                        </CardContent>
                        <CardFooter>
                            <Button size="sm" className="w-full" disabled={isProvisioning === item.id || !targetServiceId}>
                                {isProvisioning === item.id ? "Provisioning..." : "1-Click Provision"}
                            </Button>
                        </CardFooter>
                    </Card>
                ))}
            </div>

            <div className="border-t pt-8">
                <h2 className="text-lg font-semibold mb-4">Your Addons</h2>
                {addons.length === 0 ? (
                    <div className="text-center py-12 border rounded-lg bg-muted/10 border-dashed">
                        <Database className="mx-auto h-8 w-8 text-muted-foreground mb-2" />
                        <h3 className="text-sm font-medium">No addons yet</h3>
                        <p className="text-xs text-muted-foreground">Provision a database via the catalog above.</p>
                    </div>
                ) : (
                    <div className="grid gap-4">
                        {addons.map(addon => (
                            <Card key={addon.id} className="flex flex-col sm:flex-row items-start sm:items-center justify-between p-6">
                                <div className="flex items-center gap-4 mb-4 sm:mb-0">
                                    <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center text-xl">
                                        {ADDON_TYPES[addon.addon_type]?.icon || "?"}
                                    </div>
                                    <div>
                                        <div className="font-medium flex items-center gap-2">
                                            {addon.name}
                                            <span className={cn("text-[10px] px-2 py-0.5 rounded-full border", 
                                                addon.status === 'ACTIVE' ? "bg-green-500/10 text-green-500 border-green-500/20" : 
                                                addon.status === 'PROVISIONING' ? "bg-yellow-500/10 text-yellow-500 border-yellow-500/20" : 
                                                "bg-red-500/10 text-red-500 border-red-500/20"
                                            )}>{addon.status}</span>
                                        </div>
                                        <div className="text-xs text-muted-foreground">
                                            {ADDON_TYPES[addon.addon_type]?.name} - Attached to service {addon.service}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex gap-2 w-full sm:w-auto">
                                    <Button variant="outline" size="sm" onClick={() => loadBackups(addon)}>
                                        <Archive className="mr-2 h-3.5 w-3.5" /> Backups
                                    </Button>
                                    <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive/10">
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            </Card>
                        ))}
                    </div>
                )}
            </div>

            {/* Backups Dialog */}
            <Dialog open={isBackupsOpen} onOpenChange={setIsBackupsOpen}>
                <DialogContent className="sm:max-w-[600px]">
                    <DialogHeader>
                        <DialogTitle>Backups: {activeAddon?.name}</DialogTitle>
                        <DialogDescription>Manage snapshots and recovery points.</DialogDescription>
                    </DialogHeader>
                    
                    <div className="py-4 space-y-4">
                        <div className="flex justify-between items-center">
                            <h4 className="text-sm font-medium">History</h4>
                            <Button size="sm" onClick={handleCreateBackup} disabled={isLoadingBackups}>
                                <Play className="mr-2 h-3.5 w-3.5" /> Create Backup
                            </Button>
                        </div>
                        
                        <div className="border rounded-md divide-y max-h-[300px] overflow-y-auto">
                            {isLoadingBackups ? (
                                <div className="p-4 text-center text-xs text-muted-foreground">Loading...</div>
                            ) : backups.length === 0 ? (
                                <div className="p-4 text-center text-xs text-muted-foreground">No backups found.</div>
                            ) : (
                                backups.map(backup => (
                                    <div key={backup.id} className="p-3 flex items-center justify-between text-sm">
                                        <div>
                                            <div className="font-medium flex items-center gap-2">
                                                {new Date(backup.created_at).toLocaleString()}
                                                {backup.status === 'COMPLETED' && <span className="text-green-500 text-[10px] border border-green-500/20 px-1 rounded">OK</span>}
                                                {backup.status === 'FAILED' && <span className="text-red-500 text-[10px] border border-red-500/20 px-1 rounded">FAIL</span>}
                                                {backup.status === 'PENDING' && <span className="text-yellow-500 text-[10px] border border-yellow-500/20 px-1 rounded">RUNNING</span>}
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                Size: {(backup.size_bytes / 1024 / 1024).toFixed(2)} MB
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <Button variant="ghost" size="sm" onClick={() => handleDownload(backup)}>
                                                <Archive className="mr-2 h-3.5 w-3.5" /> Download
                                            </Button>
                                            <Button variant="ghost" size="sm" onClick={() => handleRestore(backup.id)}>
                                                <RefreshCw className="mr-2 h-3.5 w-3.5" /> Restore
                                            </Button>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </DialogContent>
            </Dialog>

        </div>
        </DashboardShell>
    )
}
