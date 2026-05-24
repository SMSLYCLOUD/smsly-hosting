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
import { useConfirm } from '@/components/ui/confirm-dialog'
import { RequiresTier } from "@/components/licensing/RequiresTier"
import { EcosystemSuggestion } from "@/components/dashboard/EcosystemSuggestion"

import { ADDON_REGISTRY, getAddonMetadata, AddonRegistryItem } from "@/lib/addonRegistry"

type AddonType = any

export default function MarketplacePage() {
    const { toast } = useToast()
    const confirm = useConfirm()
    const [addons, setAddons] = React.useState<any[]>([])
    const [services, setServices] = React.useState<any[]>([])
    const [isLoading, setIsLoading] = React.useState(true)
    
    // One-click provisioning target (default to most recent service).
    const [targetServiceId, setTargetServiceId] = React.useState<string | null>(null)
    const [isProvisioning, setIsProvisioning] = React.useState<string | null>(null) // catalog id

    // Backups Modal State
    const [isBackupsOpen, setIsBackupsOpen] = React.useState(false)
    const [activeAddon, setActiveAddon] = React.useState<any | null>(null)
    const [backups, setBackups] = React.useState<any[]>([])
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

    const handleOneClickProvision = async (item: AddonRegistryItem) => {
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

    const loadBackups = async (addon: any) => {
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
        if (!await confirm({ title: 'Restore backup?', message: 'This will overwrite current data. Continue?', variant: 'destructive', confirmText: 'Restore' })) return
        
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

    const handleDownload = async (backup: any) => {
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
         <RequiresTier tier="pro">
         <div className="container py-8 space-y-8">
             <div>
                 <h1 className="text-3xl font-bold tracking-tight">Marketplace</h1>
                 <p className="text-muted-foreground">One-click databases and services.</p>
             </div>
             
             {/* SMSLY Ecosystem Cross-Sell */}
             <div className="mb-6">
                 <EcosystemSuggestion context="marketplace" dismissible={true} />
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
                {ADDON_REGISTRY.map((item) => (
                    <Card key={item.id} className="hover:border-primary/50 transition-colors cursor-pointer" onClick={() => {
                        handleOneClickProvision(item)
                    }}>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">{item.name}</CardTitle>
                            {item.logo ? (
                                <img src={item.logo} alt={item.name} className="h-6 w-6 object-contain" />
                            ) : (
                                <span className="text-sm font-mono text-muted-foreground">??</span>
                            )}
                        </CardHeader>
                        <CardContent>
                            <div className="text-xs text-muted-foreground">{item.description}</div>
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
                        {addons.map(addon => {
                            const meta = getAddonMetadata(addon.addon_type);
                            return (
                                <Card key={addon.id} className="flex flex-col sm:flex-row items-start sm:items-center justify-between p-6">
                                    <div className="flex items-center gap-4 mb-4 sm:mb-0">
                                        <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center text-xl overflow-hidden p-2">
                                            {meta?.logo ? (
                                                <img src={meta.logo} alt={meta.name} className="w-full h-full object-contain" />
                                            ) : (
                                                <span className="text-sm font-mono text-muted-foreground">?</span>
                                            )}
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
                                                {meta?.name || addon.addon_type} - Attached to service {addon.service}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex gap-2 w-full sm:w-auto">
                                        {/* Backups Button */}
                                        <Button variant="outline" size="sm" onClick={() => loadBackups(addon)}>
                                            <Archive className="h-4 w-4 mr-2" />
                                            Backups
                                        </Button>

                                        {/* Delete Button */}
                                        <Button
                                            variant="destructive"
                                            size="sm"
                                            onClick={async () => {
                                                if (await confirm({ title: "Delete Addon", message: "Are you sure? All data will be lost." })) {
                                                    try {
                                                        const token = localStorage.getItem("auth_token")
                                                        const res = await fetch(`/api/v1/addons/${addon.id}/`, {
                                                            method: 'DELETE',
                                                            headers: { 'Authorization': `Token ${token}` }
                                                        })
                                                        if (res.ok) {
                                                            setAddons(prev => prev.filter(a => a.id !== addon.id))
                                                            toast({ title: "Deleted", description: "Addon has been deleted." })
                                                        }
                                                    } catch (err) {
                                                        toast({ title: "Error", description: "Could not delete addon.", variant: "destructive" })
                                                    }
                                                }
                                            }}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </Card>
                            );
                        })}
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
        </RequiresTier>
        </DashboardShell>
    )
}
