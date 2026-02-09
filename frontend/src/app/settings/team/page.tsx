"use client"

import * as React from "react"
import { Users, Mail, Shield, Trash2, Plus, Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { useToast } from "@/components/ui/use-toast"
import { cn } from "@/lib/utils"

interface TeamMember {
    id: string
    user: {
        username: string
        email: string
    }
    role: 'ADMIN' | 'MEMBER' | 'VIEWER'
}

export default function TeamSettingsPage() {
    const { toast } = useToast()
    const [members, setMembers] = React.useState<TeamMember[]>([])
    const [isLoading, setIsLoading] = React.useState(true)
    
    // Invite State
    const [isInviteOpen, setIsInviteOpen] = React.useState(false)
    const [inviteEmail, setInviteEmail] = React.useState("")
    const [inviteRole, setInviteRole] = React.useState("MEMBER")
    const [isInviting, setIsInviting] = React.useState(false)

    // Simulating fetching members
    React.useEffect(() => {
        const fetchMembers = async () => {
            // MOCK DATA FOR MVP - connection to backend pending in next step
            setTimeout(() => {
                setMembers([
                    { id: "1", user: { username: "owner", email: "owner@smsly.com" }, role: "ADMIN" },
                    { id: "2", user: { username: "jules", email: "jules@smsly.com" }, role: "MEMBER" },
                ])
                setIsLoading(false)
            }, 800)
        }
        fetchMembers()
    }, [])

    const handleInvite = async () => {
        if (!inviteEmail) return
        setIsInviting(true)
        
        // Simulate API call
        setTimeout(() => {
            const newMember: TeamMember = {
                id: Math.random().toString(),
                user: { username: inviteEmail.split('@')[0], email: inviteEmail },
                role: inviteRole as any
            }
            setMembers([...members, newMember])
            setIsInviteOpen(false)
            setIsInviting(false)
            setInviteEmail("")
            toast({ title: "Invitation Sent", description: `Invited ${inviteEmail} as ${inviteRole}` })
        }, 1000)
    }

    const handleRemove = (id: string) => {
        if (confirm("Are you sure? This user will lose access immediately.")) {
            setMembers(members.filter(m => m.id !== id))
            toast({ title: "Member Removed", description: "Access revoked." })
        }
    }

    if (isLoading) return <div className="p-8 text-center text-muted-foreground">Loading team...</div>

    return (
        <div className="container py-8 max-w-4xl space-y-8">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Team Settings</h1>
                <p className="text-muted-foreground">Manage access and roles for your organization.</p>
            </div>

            <Card className="bio-card">
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                    <div>
                        <CardTitle>Members</CardTitle>
                        <CardDescription>People with access to this workspace.</CardDescription>
                    </div>
                    <Button onClick={() => setIsInviteOpen(true)}>
                        <Plus className="mr-2 h-4 w-4" /> Invite Member
                    </Button>
                </CardHeader>
                <CardContent>
                    <div className="space-y-6">
                        {members.map(member => (
                            <div key={member.id} className="flex items-center justify-between group">
                                <div className="flex items-center gap-4">
                                    <Avatar>
                                        <AvatarImage src={`https://avatar.vercel.sh/${member.user.email}`} />
                                        <AvatarFallback>{member.user.username[0].toUpperCase()}</AvatarFallback>
                                    </Avatar>
                                    <div>
                                        <div className="font-medium">{member.user.username}</div>
                                        <div className="text-sm text-muted-foreground">{member.user.email}</div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-4">
                                    <div className="flex items-center gap-2 text-xs font-mono bg-muted px-2 py-1 rounded">
                                        <Shield className="h-3 w-3" />
                                        {member.role}
                                    </div>
                                    {member.role !== 'ADMIN' && (
                                        <Button 
                                            variant="ghost" 
                                            size="icon" 
                                            className="text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                                            onClick={() => handleRemove(member.id)}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>

            <Dialog open={isInviteOpen} onOpenChange={setIsInviteOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Invite Member</DialogTitle>
                        <DialogDescription>Send an invitation to join your team.</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>Email Address</Label>
                            <Input 
                                placeholder="colleague@company.com" 
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>Role</Label>
                            <Select value={inviteRole} onValueChange={setInviteRole}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="ADMIN">Admin (Full Access)</SelectItem>
                                    <SelectItem value="MEMBER">Member (Deploy & Manage)</SelectItem>
                                    <SelectItem value="VIEWER">Viewer (Read Only)</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button onClick={handleInvite} disabled={isInviting || !inviteEmail}>
                            {isInviting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Send Invitation
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
