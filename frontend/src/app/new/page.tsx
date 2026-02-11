"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Github, Box, Layers, ArrowRight, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Navbar } from "@/components/layout/Navbar"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { EnvVarEditor, type EnvVar } from "@/components/dashboard/env-var-editor"
import { useToast } from "@/components/ui/use-toast"

// Mock templates for now (replace with API call later)
const TEMPLATES = [
  { id: "node", name: "Node.js Express", icon: "🟢", repo: "https://github.com/smsly/template-node" },
  { id: "python", name: "Python Django", icon: "🐍", repo: "https://github.com/smsly/template-django" },
  { id: "go", name: "Go Gin", icon: "🐹", repo: "https://github.com/smsly/template-go" },
  { id: "rust", name: "Rust Axum", icon: "🦀", repo: "https://github.com/smsly/template-rust" },
]

export default function NewServicePage() {
  const router = useRouter()
  const { toast } = useToast()
  
  const [step, setStep] = React.useState(1)
  const [sourceType, setSourceType] = React.useState<"git" | "template" | "docker">("git")
  const [repoUrl, setRepoUrl] = React.useState("")
  const [selectedTemplate, setSelectedTemplate] = React.useState<string | null>(null)
  
  // Config state
  const [name, setName] = React.useState("")
  const [region, setRegion] = React.useState("us-east-1")
  const [envVars, setEnvVars] = React.useState<EnvVar[]>([])
  const [isDeploying, setIsDeploying] = React.useState(false)

  const handleNext = () => {
    if (step === 1) {
      if (sourceType === "git" && !repoUrl) return
      if (sourceType === "template" && !selectedTemplate) return
      
      // Auto-generate name from repo/template if empty
      if (!name) {
          if (sourceType === "git") {
              const parts = repoUrl.split("/")
              setName(parts[parts.length - 1]?.replace(".git", "") || "my-service")
          } else if (sourceType === "template") {
              setName(`my-${selectedTemplate}-app`)
          }
      }
    }
    setStep(step + 1)
  }

  const handleDeploy = async () => {
    setIsDeploying(true)
    try {
        const token = localStorage.getItem("auth_token")
        if (!token) throw new Error("Not authenticated")

        // 1. Create Service
        const finalRepo = sourceType === "template" 
            ? TEMPLATES.find(t => t.id === selectedTemplate)?.repo 
            : repoUrl

        const createRes = await fetch("/api/v1/services/", {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "Authorization": `Token ${token}`
            },
            body: JSON.stringify({
                name,
                deploy_type: "GIT", // Templates are just git repos
                repository_url: finalRepo,
                branch: "main",
                cpu_cores: 0.5,
                memory_mb: 512,
                regions: [] // Default region logic handles this
            })
        })
        
        if (!createRes.ok) throw new Error("Failed to create service")
        const service = await createRes.json()

        // 2. Set Env Vars
        if (envVars.length > 0) {
            for (const v of envVars) {
                await fetch(`/api/v1/services/${service.id}/env_vars/`, {
                    method: "POST",
                    headers: { 
                        "Content-Type": "application/json", 
                        "Authorization": `Token ${token}`
                    },
                    body: JSON.stringify({ key: v.key, value: v.value, is_secret: v.isSecret })
                })
            }
        }

        // 3. Trigger Deployment
        const deployRes = await fetch(`/api/v1/services/${service.id}/deploy/`, {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "Authorization": `Token ${token}`
            },
            body: JSON.stringify({ ref: "main" })
        })

        if (!deployRes.ok) throw new Error("Failed to trigger deployment")
        
        toast({ title: "Success", description: "Service created and deployment started." })
        router.push(`/services/${service.id}`)

    } catch (err) {
        console.error(err)
        // toast({ title: "Error", description: "Deployment failed. Check console.", variant: "destructive" })
    } finally {
        setIsDeploying(false)
    }
  }

  return (
    <main className="min-h-screen flex flex-col premium-bg">
      <Navbar />
      {/* Floating Gradient Orbs */}
      <div className="floating-orb w-[450px] h-[450px] bg-emerald-500/8 -top-24 -left-24" style={{ animationDelay: '0s' }} />
      <div className="floating-orb w-[350px] h-[350px] bg-violet-500/6 bottom-10 -right-16" style={{ animationDelay: '5s' }} />
      <div className="absolute inset-0 dot-grid opacity-30 pointer-events-none" />
    <div className="container max-w-4xl py-10 relative z-10">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Create New Service</h1>
        <p className="text-muted-foreground">Deploy your application in seconds.</p>
      </div>

      <div className="grid gap-8 md:grid-cols-[250px_1fr]">
        <nav className="flex flex-col gap-2 text-sm text-muted-foreground">
            <div className={cn("flex items-center gap-2 p-2 rounded-lg", step === 1 && "bg-muted text-foreground font-medium")}>
                <div className="flex h-6 w-6 items-center justify-center rounded-full border bg-background text-xs">1</div>
                Select Source
            </div>
            <div className={cn("flex items-center gap-2 p-2 rounded-lg", step === 2 && "bg-muted text-foreground font-medium")}>
                <div className="flex h-6 w-6 items-center justify-center rounded-full border bg-background text-xs">2</div>
                Configure
            </div>
            <div className={cn("flex items-center gap-2 p-2 rounded-lg", step === 3 && "bg-muted text-foreground font-medium")}>
                <div className="flex h-6 w-6 items-center justify-center rounded-full border bg-background text-xs">3</div>
                Deploy
            </div>
        </nav>

        <div className="space-y-8">
            {step === 1 && (
                <div className="space-y-6 animate-in slide-in-from-right-4">
                    <div className="grid grid-cols-3 gap-4">
                        <Card 
                            className={cn("cursor-pointer hover:border-primary transition-all", sourceType === "git" && "border-primary bg-primary/5")}
                            onClick={() => setSourceType("git")}
                        >
                            <CardHeader className="p-6 text-center">
                                <Github className="h-8 w-8 mx-auto mb-2" />
                                <CardTitle className="text-base">Git Repository</CardTitle>
                                <CardDescription>Public or private repo</CardDescription>
                            </CardHeader>
                        </Card>
                        <Card 
                            className={cn("cursor-pointer hover:border-primary transition-all", sourceType === "template" && "border-primary bg-primary/5")}
                            onClick={() => setSourceType("template")}
                        >
                            <CardHeader className="p-6 text-center">
                                <Layers className="h-8 w-8 mx-auto mb-2" />
                                <CardTitle className="text-base">Template</CardTitle>
                                <CardDescription>Start from scratch</CardDescription>
                            </CardHeader>
                        </Card>
                        <Card 
                            className={cn("cursor-pointer hover:border-primary transition-all", sourceType === "docker" && "border-primary bg-primary/5")}
                            onClick={() => setSourceType("docker")}
                        >
                            <CardHeader className="p-6 text-center">
                                <Box className="h-8 w-8 mx-auto mb-2" />
                                <CardTitle className="text-base">Docker Image</CardTitle>
                                <CardDescription>Registry image</CardDescription>
                            </CardHeader>
                        </Card>
                    </div>

                    {sourceType === "git" && (
                        <div className="space-y-2">
                            <Label>Repository URL</Label>
                            <Input 
                                placeholder="https://github.com/username/repo" 
                                value={repoUrl} 
                                onChange={(e) => setRepoUrl(e.target.value)} 
                            />
                        </div>
                    )}

                    {sourceType === "template" && (
                        <div className="grid grid-cols-2 gap-4">
                            {TEMPLATES.map(t => (
                                <div 
                                    key={t.id}
                                    className={cn(
                                        "flex items-center gap-3 p-4 rounded-lg border cursor-pointer hover:bg-muted",
                                        selectedTemplate === t.id && "border-primary bg-primary/5"
                                    )}
                                    onClick={() => setSelectedTemplate(t.id)}
                                >
                                    <span className="text-2xl">{t.icon}</span>
                                    <div className="font-medium">{t.name}</div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {step === 2 && (
                <div className="space-y-6 animate-in slide-in-from-right-4">
                    <div className="space-y-4">
                        <div className="grid gap-2">
                            <Label>Service Name</Label>
                            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-awesome-service" />
                        </div>
                        <div className="grid gap-2">
                            <Label>Region</Label>
                            <Select value={region} onValueChange={setRegion}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="us-east-1">🇺🇸 US East (N. Virginia)</SelectItem>
                                    <SelectItem value="eu-west-1">🇪🇺 EU West (Ireland)</SelectItem>
                                    <SelectItem value="ap-southeast-1">🇸🇬 Asia Pacific (Singapore)</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="pt-4">
                            <EnvVarEditor 
                                initialVars={envVars} 
                                onSave={async (v) => setEnvVars(v)} 
                            />
                        </div>
                    </div>
                </div>
            )}

            {step === 3 && (
                <div className="space-y-6 animate-in slide-in-from-right-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Review & Deploy</CardTitle>
                            <CardDescription>Ready to launch your service?</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="grid grid-cols-2 gap-4 text-sm">
                                <div className="text-muted-foreground">Source</div>
                                <div className="font-medium truncate">{sourceType === "template" ? `Template: ${selectedTemplate}` : repoUrl}</div>
                                
                                <div className="text-muted-foreground">Name</div>
                                <div className="font-medium">{name}</div>
                                
                                <div className="text-muted-foreground">Region</div>
                                <div className="font-medium">{region}</div>
                                
                                <div className="text-muted-foreground">Env Vars</div>
                                <div className="font-medium">{envVars.length} variables defined</div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            <div className="flex justify-end gap-4 pt-4">
                {step > 1 && (
                    <Button variant="outline" onClick={() => setStep(step - 1)} disabled={isDeploying}>
                        Back
                    </Button>
                )}
                {step < 3 ? (
                    <Button onClick={handleNext}>
                        Next <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                ) : (
                    <Button onClick={handleDeploy} disabled={isDeploying}>
                        {isDeploying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        {isDeploying ? "Deploying..." : "Deploy Service"}
                    </Button>
                )}
            </div>
        </div>
      </div>
    </div>
    </main>
  )
}

function cn(...classes: (string | undefined | null | boolean)[]) {
  return classes.filter(Boolean).join(" ")
}
