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

const ADDON_TYPES = {
    POSTGRES: { name: "PostgreSQL", icon: "🐘", desc: "Relational Database" },
    REDIS: { name: "Redis", icon: "🔴", desc: "In-memory Store" },
    MYSQL: { name: "MySQL", icon: "🐬", desc: "Relational Database" },
    MONGODB: { name: "MongoDB", icon: "🍃", desc: "NoSQL Database" },
}

export default function MarketplacePage() {
    const { toast } = useToast()
    const [addons, setAddons] = React.useState<Addon[]>([])
    const [services, setServices] = React.useState<Service[]>([])
    const [isLoading, setIsLoading] = React.useState(true)
    
    // Provision Modal State
    const [isProvisionOpen, setIsProvisionOpen] = React.useState(false)
    const [selectedType, setSelectedType] = React.useState<string | null>(null)
    const [selectedService, setSelectedService] = React.useState<string | null>(null)
    const [addonName, setAddonName] = React.useState("")
    const [isProvisioning, setIsProvisioning] = React.useState(false)

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
                if (addonsRes.ok) setAddons(await addonsRes.json())

                // Fetch Services (for provisioning)
                const servicesRes = await fetch("/api/v1/services/", {
                    headers: { "Authorization": `Token ${token}` }
                })
                if (servicesRes.ok) {
                    const data = await servicesRes.json()
                    setServices(data.results || [])
                }
            } catch (err) {
                console.error(err)
            } finally {
                setIsLoading(false)
            }
        }
        loadData()
    }, [])

    const handleProvision = async () => {
        if (!selectedType || !selectedService || !addonName) return
        setIsProvisioning(true)
        const token = localStorage.getItem("auth_token")
        
        try {
            const res = await fetch("/api/v1/addons/", {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "Authorization": `Token ${token}` 
                },
                body: JSON.stringify({
                    name: addonName,
                    addon_type: selectedType,
                    service: selectedService
                })
            })
            if (!res.ok) throw new Error("Provisioning failed")
            
            const newAddon = await res.json()
            setAddons([...addons, newAddon])
            setIsProvisionOpen(false)
            toast({ title: "Started", description: "Provisioning initiated." })
        } catch (err) {
            toast({ title: "Error", description: "Failed to provision addon.", variant: "destructive" })
        } finally {
            setIsProvisioning(false)
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
            if (res.ok) setBackups(await res.json())
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
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {Object.entries(ADDON_TYPES).map(([type, info]) => (
                    <Card key={type} className="hover:border-primary/50 transition-colors cursor-pointer" onClick={() => {
                        setSelectedType(type)
                        setIsProvisionOpen(true)
                    }}>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">{info.name}</CardTitle>
                            <span className="text-2xl">{info.icon}</span>
                        </CardHeader>
                        <CardContent>
                            <div className="text-xs text-muted-foreground">{info.desc}</div>
                        </CardContent>
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
                                            {ADDON_TYPES[addon.addon_type]?.name} • Attached to service {addon.service}
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

            {/* Provision Dialog */}
            <Dialog open={isProvisionOpen} onOpenChange={setIsProvisionOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Provision {ADDON_TYPES[selectedType as keyof typeof ADDON_TYPES]?.name}</DialogTitle>
                        <DialogDescription>Add a managed database to your service.</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>Name</Label>
                            <Input 
                                placeholder="my-read-replica" 
                                value={addonName}
                                onChange={(e) => setAddonName(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>Attach to Service</Label>
                            <Select onValueChange={setSelectedService}>
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

                    <DialogFooter>
                        <Button onClick={handleProvision} disabled={isProvisioning}>
                            {isProvisioning && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Provision Database
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
        </DashboardShell>
    )
}
