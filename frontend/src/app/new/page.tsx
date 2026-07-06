"use client"

import * as React from "react"
import Image from 'next/image';
import { useRouter } from "next/navigation"
import { Github, Box, Layers, ArrowRight, Loader2, Search, Sparkles, Zap, Settings2, Rocket, CheckCircle2, Code2, Database, Globe, GitBranch, Key, SkipForward, Server, Monitor, Wifi, WifiOff, Filter, Tag, LayoutGrid, ListFilter, UploadCloud } from "lucide-react"
import { Button } from "@/components/ui/button"
import { DashboardShell } from "@/components/layout/DashboardShell"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { EnvVarEditor, type EnvVar } from "@/components/dashboard/env-var-editor"
import { useToast } from "@/components/ui/use-toast"
import { BuildpackSelector, BuildpackType } from "@/components/deployments/BuildpackSelector"
import api, { serversApi, servicesApi, deployApi, projectsApi, registryCredentialsApi, githubApi, gitlabApi, bitbucketApi, ManagedServer, Project } from "@/lib/api"
import { templatesApi } from "@/lib/api"
import { slugify } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"

const STACK_ICONS: Record<string, string> = {
  node: "🟢", python: "🐍", go: "🐹", rust: "🦀", ruby: "💎",
  java: "☕", php: "🐘", dotnet: "🔷", static: "📄", unknown: "📦",
}

// Enriched env var from AI analysis
interface AnalysisEnvVar {
  key: string
  hint?: string
  required?: boolean
  is_secret?: boolean
  user_required?: boolean
  default?: string
}

interface Analysis {
  repo: string
  name: string
  stack: string
  languages?: string[]
  port: number
  build: string
  addons: string[]
  env_vars: AnalysisEnvVar[] | Record<string, string>
}

// ── Steps ──────────────────────────────────────────────────────────────────
const STEPS = [
  { id: 1, label: "Select Source", icon: Github },
  { id: 2, label: "AI Analysis", icon: Sparkles },
  { id: 3, label: "Configure", icon: Settings2 },
  { id: 4, label: "Review", icon: CheckCircle2 },
  { id: 5, label: "Target Servers", icon: Server },
]

export default function NewServicePage() {
  const router = useRouter()
  const { toast } = useToast()

  const [step, setStep] = React.useState(1)
  const [sourceType, setSourceType] = React.useState<"git" | "template" | "docker" | "upload">("git")
  const [repoUrl, setRepoUrl] = React.useState("")
  const [selectedTemplate, setSelectedTemplate] = React.useState<string | null>(null)
  const [dockerImage, setDockerImage] = React.useState("")
  const [registryCredentials, setRegistryCredentials] = React.useState<any[]>([])
  const [registryCredentialId, setRegistryCredentialId] = React.useState<string>("none")

  // Upload state
  const [uploadFiles, setUploadFiles] = React.useState<FileList | null>(null)
  const [uploading, setUploading] = React.useState(false)
  const [uploadProgress, setUploadProgress] = React.useState<string>("")
  const fileInputRef = React.useRef<HTMLInputElement>(null)

  // Config state
  const [name, setName] = React.useState("")
  const [branch, setBranch] = React.useState("main")
  const [buildpack, setBuildpack] = React.useState<BuildpackType>("DOCKER")
  const [region, setRegion] = React.useState("us-east-1")
  const [cpuCores, setCpuCores] = React.useState<number>(0.5)
  const [memoryMb, setMemoryMb] = React.useState<number>(512)
  const [envVars, setEnvVars] = React.useState<EnvVar[]>([])
  const [isDeploying, setIsDeploying] = React.useState(false)

  // AI analysis state
  const [analyzing, setAnalyzing] = React.useState(false)
  const [analysis, setAnalysis] = React.useState<Analysis | null>(null)
  const [deployMode, setDeployMode] = React.useState<"auto" | "manual" | null>(null)
  const [showEnvPrompt, setShowEnvPrompt] = React.useState(false)
  const [userRequiredVars, setUserRequiredVars] = React.useState<Record<string, string>>({})

  // Git Provider state
  const [gitProvider, setGitProvider] = React.useState<"github" | "gitlab" | "bitbucket">("github")
  const [gitRepos, setGitRepos] = React.useState<any[]>([])
  const [gitLoading, setGitLoading] = React.useState(false)
  const [gitSearch, setGitSearch] = React.useState("")
  const [gitConnected, setGitConnected] = React.useState(false)
  const [gitCategories, setGitCategories] = React.useState<Record<string, any[]>>({})
  const [gitClusters, setGitClusters] = React.useState<any[]>([])
  const [selectedCategory, setSelectedCategory] = React.useState<string>("All")

  // Branch fetching state
  const [branches, setBranches] = React.useState<any[]>([])
  const [loadingBranches, setLoadingBranches] = React.useState(false)

  // Templates from API
  const [templates, setTemplates] = React.useState<any[]>([])

  // Server selection state
  const [servers, setServers] = React.useState<ManagedServer[]>([])
  const [serversLoading, setServersLoading] = React.useState(false)
  const [selectedServers, setSelectedServers] = React.useState<string[]>([])
  const [includeLocal, setIncludeLocal] = React.useState(true)
  const [deployResults, setDeployResults] = React.useState<any>(null)

  // Project selection state
  const [projectsList, setProjectsList] = React.useState<Project[]>([])
  const [selectedProject, setSelectedProject] = React.useState<string>("")

  // Registry config state (custom push registry for this deployment)
  const [showRegistryConfig, setShowRegistryConfig] = React.useState(false)
  const [registryUrl, setRegistryUrl] = React.useState("")
  const [registryUsername, setRegistryUsername] = React.useState("")
  const [registryPassword, setRegistryPassword] = React.useState("")

  React.useEffect(() => {
    setGitLoading(true)
    api.get(`/integrations/${gitProvider}/repos/`)
      .then(res => {
        setGitRepos(res.data?.repos || [])
        setGitCategories(res.data?.categories || {})
        setGitClusters(res.data?.clusters || [])
        setGitConnected(true)
      })
      .catch(() => {
        setGitConnected(false)
        setGitRepos([])
        setGitCategories({})
        setGitClusters([])
      })
      .finally(() => setGitLoading(false))
  }, [gitProvider])

  React.useEffect(() => {
    templatesApi.list().then(setTemplates).catch(() => {})
    // Load projects for the project selector
    projectsApi.list().then(setProjectsList).catch(() => {})
    // Load registry credentials
    registryCredentialsApi.list().then(setRegistryCredentials).catch(() => {})
  }, [])

  // Fetch branches from Git provider when a repo URL is entered
  React.useEffect(() => {
    if (!repoUrl || sourceType !== "git") {
      setBranches([])
      return
    }
    // Extract repo slug from URL (e.g. https://github.com/user/repo -> user/repo)
    const match = repoUrl.match(/github\.com\/([^\/]+\/[^\/]+)/)
      || repoUrl.match(/gitlab\.com\/([^\/]+\/[^\/]+)/)
      || repoUrl.match(/bitbucket\.org\/([^\/]+\/[^\/]+)/)
    if (!match) {
      setBranches([])
      return
    }
    let repo = match[1]
    if (repo.endsWith('.git')) repo = repo.slice(0, -4)

    setLoadingBranches(true)
    const api = match[0].includes('github.com') ? githubApi
      : match[0].includes('gitlab.com') ? gitlabApi
      : bitbucketApi
    api.branches(repo)
      .then((data: any) => {
        if (Array.isArray(data)) {
          setBranches(data)
          // Auto-select the first branch if current branch is empty or "main"
          if (data.length > 0 && (!branch || branch === "main")) {
            setBranch(data[0].name || data[0])
          }
        }
      })
      .catch(() => setBranches([]))
      .finally(() => setLoadingBranches(false))
  }, [repoUrl, sourceType, branch])

  const filteredRepos = gitRepos.filter(r => {
    const matchesSearch = !gitSearch || r.full_name?.toLowerCase().includes(gitSearch.toLowerCase()) || r.name?.toLowerCase().includes(gitSearch.toLowerCase())
    const matchesCategory = selectedCategory === "All" || r.category === selectedCategory
    return matchesSearch && matchesCategory
  })

  const categoryList = ["All", ...Object.keys(gitCategories).sort()]

  // ── AI Analysis ────────────────────────────────────────────────────────
  const runAnalysis = async (url: string) => {
    setAnalyzing(true)
    setAnalysis(null)
    try {
      const res = await api.post('/cloud/intelligence/analyze_repo/', { repo_url: url })
      const data = res.data as Analysis
      setAnalysis(data)
      // Pre-fill config from analysis
      if (data.name) setName(data.name)

      // Handle enriched env vars (list of objects) or legacy format (Record)
      if (Array.isArray(data.env_vars) && data.env_vars.length > 0) {
        setEnvVars(
          data.env_vars.map((ev: AnalysisEnvVar) => ({
            key: ev.key,
            value: ev.default || '',
            isSecret: ev.is_secret || false,
          }))
        )
        // Pre-fill userRequiredVars for the prompt
        const reqVars: Record<string, string> = {}
        data.env_vars.forEach((ev: AnalysisEnvVar) => {
          if (ev.user_required) reqVars[ev.key] = ''
        })
        setUserRequiredVars(reqVars)
      } else if (data.env_vars && typeof data.env_vars === 'object' && !Array.isArray(data.env_vars)) {
        // Legacy Record<string, string> format
        setEnvVars(
          Object.entries(data.env_vars).map(([key, value]) => ({
            key, value: value as string, isSecret: key.toLowerCase().includes("secret") || key.toLowerCase().includes("key"),
          }))
        )
      }
    } catch (err: any) {
      toast({
        title: "Analysis Failed",
        description: err?.response?.data?.error || "Could not analyze repository.",
        variant: "destructive",
      })
    } finally {
      setAnalyzing(false)
    }
  }

  // ── Navigation ─────────────────────────────────────────────────────────
  const handleNext = () => {
    if (step === 1) {
      if (sourceType === "git" && !repoUrl) return
      if (sourceType === "template" && !selectedTemplate) return
      if (sourceType === "docker" && !dockerImage) return
      if (sourceType === "upload" && (!uploadFiles || uploadFiles.length === 0)) return

      // Auto-generate name
      if (!name) {
        const randomStr = Math.random().toString(36).substring(2, 7)
        if (sourceType === "git") {
          const parts = repoUrl.split("/")
          setName(`${parts[parts.length - 1]?.replace(".git", "") || "my-service"}-${randomStr}`)
        } else if (sourceType === "template") {
          setName(`my-${selectedTemplate}-app-${randomStr}`)
        } else if (sourceType === "docker") {
          const imageName = dockerImage.split("/").pop()?.split(":")[0]
          setName(`${(imageName || "docker-service").replace(/[^a-zA-Z0-9-]/g, "-")}-${randomStr}`)
        } else if (sourceType === "upload" && uploadFiles && uploadFiles.length > 0) {
          const baseName = uploadFiles[0].name.replace(/\.(zip|tar\.gz|tgz)$/, "")
          setName(`${baseName.replace(/[^a-zA-Z0-9-]/g, "-").toLowerCase()}-${randomStr}`)
        }
      }

      // For git repos, go to AI analysis step
      if (sourceType === "git") {
        setStep(2)
        runAnalysis(repoUrl)
        return
      }
      // For templates/docker, skip analysis and go straight to config
      setStep(3)
      return
    }

    if (step === 2) {
      if (deployMode === "auto") {
        // Check if there are user-required vars (API keys, etc.)
        const hasUserRequired = Object.keys(userRequiredVars).length > 0
        if (hasUserRequired) {
          setShowEnvPrompt(true)
          return
        }
        // No user-required vars, go straight to deploy
        setStep(4)
      } else {
        // Manual — go to config (pre-filled)
        setStep(3)
      }
      return
    }

    setStep(step + 1)
    // Fetch servers when entering Step 5
    if (step + 1 === 5) fetchServers()
  }

  const handleBack = () => {
    if (step === 5) {
      setStep(4) // Go back to Review from Target Servers
    } else if (step === 4 && deployMode === "auto") {
      setStep(2) // Skip config going back
    } else if (step === 3 && sourceType !== "git") {
      setStep(1) // Skip analysis for non-git
    } else {
      setStep(step - 1)
    }
  }

  // ── Fetch servers when reaching step 5 ────────────────────────────
  const fetchServers = React.useCallback(async () => {
    setServersLoading(true)
    try {
      const data = await serversApi.list()
      setServers(data)
    } catch (err) {
      console.error('Failed to load servers:', err)
    } finally {
      setServersLoading(false)
    }
  }, [])

  const toggleServer = (id: string) => {
    const server = servers.find(s => s.id === id)
    if (!server || server.is_primary || server.allow_user_workloads === false || server.status !== 'ONLINE') return
    setSelectedServers(prev => {
      const isSelected = prev.includes(id)
      if (isSelected) {
        return []
      } else {
        setIncludeLocal(false)
        return [id]
      }
    })
  }

  // ── Deploy ─────────────────────────────────────────────────────────────
  const handleDeploy = async () => {
    setIsDeploying(true)
    try {
      // The HttpOnly auth cookie is attached automatically by the
      // browser via the api instance's ``withCredentials: true``. If
      // the user is not authenticated, the call returns 401 and the
      // global error interceptor handles the redirect.
      const localOnlyRequest = { _skipRemoteProxy: true } as any

      // Auto-derive name from repo URL if still empty
      let finalName = slugify(name.trim())
      if (!finalName && repoUrl) {
        finalName = slugify((repoUrl.split("/").pop()?.replace(".git", "")?.replace(/[^a-zA-Z0-9-]/g, "-") || "my-service") + "-" + Math.random().toString(36).substring(2, 7))
        setName(finalName)
      }
      if (!finalName) throw new Error("Service name is required")

      // Validate upload file
      if (sourceType === "upload") {
        if (!uploadFiles || uploadFiles.length === 0) {
          throw new Error("Please select an archive file to upload")
        }
        const file = uploadFiles[0]
        if (!file.name.endsWith('.zip') && !file.name.endsWith('.tar.gz') && !file.name.endsWith('.tgz')) {
          throw new Error("Invalid file format. Please upload a .zip or .tar.gz archive.")
        }
        if (file.size > 100 * 1024 * 1024) {
          throw new Error("File too large. Maximum size is 100MB.")
        }
      }

      const finalRepo = sourceType === "template"
        ? templates.find(t => t.id === selectedTemplate || t.slug === selectedTemplate)?.repository_url || ''
        : repoUrl

      const deployType = sourceType === "docker" || sourceType === "upload" ? "DOCKER" : "GIT"

      const service = await servicesApi.create({
          name: finalName,
          deploy_type: sourceType === "upload" ? "UPLOAD" : deployType,
          buildpack: sourceType === "docker" ? "DOCKER" : sourceType === "upload" ? "DOCKER" : buildpack,
          repository_url: (sourceType === "docker" || sourceType === "upload") ? null : finalRepo,
          docker_image: sourceType === "docker" ? dockerImage : null,
          branch: branch || "main",
          cpu_cores: cpuCores,
          memory_mb: memoryMb,
          regions: [],
          ...(selectedProject && selectedProject !== "none" ? { project: selectedProject } : {}),
          ...(sourceType === "docker" && registryCredentialId !== "none" ? { registry_credential: registryCredentialId } : {})
      }, localOnlyRequest)

      // Set Env Vars
      if (envVars.length > 0) {
        for (const v of envVars) {
          await api.post(
            `/services/${service.id}/env_vars/`,
            { key: v.key, value: v.value, is_secret: v.isSecret },
            localOnlyRequest,
          )
        }
      }

      // Handle upload deploy
      if (sourceType === "upload" && uploadFiles && uploadFiles.length > 0) {
        setUploading(true)
        setUploadProgress("Uploading archive...")
        const file = uploadFiles[0]
        const formData = new FormData()
        formData.append('file', file)

        await api.post(`/services/${service.id}/upload-deploy/`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          _skipRemoteProxy: true,
          onUploadProgress: (progressEvent: any) => {
            const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            setUploadProgress(`Uploading... ${percent}%`)
          },
        } as any)

        toast({
          title: "🚀 Deploying!",
          description: `Service created — deploying from ${file.name}.`,
        })
        setTimeout(() => router.push(`/services/${service.id}`), 2000)
        return
      }

      // Trigger Multi-Deploy (local + selected remote servers)
      const workloadServerIds = selectedServers.filter(id => {
        const server = servers.find(s => s.id === id)
        return server && !server.is_primary && server.allow_user_workloads !== false
      })

      const results = await deployApi.multiDeploy(
        service.id,
        branch || 'main',
        workloadServerIds,
        includeLocal,
        localOnlyRequest,
        registryUrl ? { url: registryUrl, username: registryUsername, password: registryPassword } : undefined,
      )
      setDeployResults(results)

      const remoteCount = workloadServerIds.length
      toast({
        title: "🚀 Deploying!",
        description: remoteCount > 0
          ? `Service created — deploying to ${includeLocal ? 'local + ' : ''}${remoteCount} remote server${remoteCount > 1 ? 's' : ''}.`
          : "Service created — AI is handling the rest.",
      })
      // Brief delay to let progress show before navigating
      setTimeout(() => router.push(`/services/${service.id}`), 2000)
    } catch (err) {
      console.error(err)
      toast({ title: "Error", description: "Deployment failed. Check console.", variant: "destructive" })
    } finally {
      setIsDeploying(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <DashboardShell>
    <div className="container max-w-5xl py-10 relative z-10">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Create New Service</h1>
        <p className="text-muted-foreground">AI-powered deployment — select a repo and let SMSLY handle the rest.</p>
      </div>

      <div className="grid gap-8 md:grid-cols-[250px_1fr]">
        {/* ── Step Sidebar ───────────────────────────────────── */}
        <nav className="flex flex-col gap-2 text-sm text-muted-foreground">
          {STEPS.map((s) => {
            // Hide "Configure" step in auto mode
            if (s.id === 3 && deployMode === "auto" && step !== 3) return null
            // Hide "AI Analysis" for non-git sources
            if (s.id === 2 && sourceType !== "git" && step !== 2) return null

            const Icon = s.icon
            const isActive = step === s.id
            const isDone = step > s.id

            return (
              <div
                key={s.id}
                className={cn(
                  "flex items-center gap-2 p-2 rounded-lg transition-colors",
                  isActive && "bg-primary/10 text-primary font-medium",
                  isDone && "text-emerald-500",
                )}
              >
                <div className={cn(
                  "flex h-6 w-6 items-center justify-center rounded-full border text-xs transition-colors",
                  isActive && "border-primary bg-primary text-primary-foreground",
                  isDone && "border-emerald-500 bg-emerald-500 text-white",
                )}>
                  {isDone ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
                </div>
                {s.label}
              </div>
            )
          })}
        </nav>

        {/* ── Step Content ──────────────────────────────────── */}
        <div className="space-y-8 min-w-0 overflow-hidden">
          <AnimatePresence mode="wait">
            {/* ── STEP 1: SELECT SOURCE ── */}
            {step === 1 && (
              <motion.div
                key="step1"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="space-y-6"
              >
                <div className="grid grid-cols-4 gap-4">
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
                  <Card
                    className={cn("cursor-pointer hover:border-primary transition-all", sourceType === "upload" && "border-primary bg-primary/5")}
                    onClick={() => setSourceType("upload")}
                  >
                    <CardHeader className="p-6 text-center">
                      <UploadCloud className="h-8 w-8 mx-auto mb-2" />
                      <CardTitle className="text-base">Upload</CardTitle>
                      <CardDescription>Deploy from local dir</CardDescription>
                    </CardHeader>
                  </Card>
                </div>

                {sourceType === "git" && (
                  <div className="space-y-4">
                    <div className="flex items-center gap-4 border-b pb-4">
                      <Label className="text-muted-foreground whitespace-nowrap">Git Provider</Label>
                      <Select value={gitProvider} onValueChange={(val: any) => setGitProvider(val)}>
                        <SelectTrigger className="w-[180px]">
                          <SelectValue placeholder="Select Provider" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="github">GitHub</SelectItem>
                          <SelectItem value="gitlab">GitLab</SelectItem>
                          <SelectItem value="bitbucket">Bitbucket</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    {gitConnected && (
                      <div className="space-y-2">
                        <Label>Your {gitProvider === 'github' ? 'GitHub' : gitProvider === 'gitlab' ? 'GitLab' : 'Bitbucket'} Repositories</Label>
                        <div className="flex gap-4">
                          <div className="relative flex-1">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                            <Input
                              placeholder="Search repositories..."
                              className="pl-10"
                              value={gitSearch}
                              onChange={(e) => setGitSearch(e.target.value)}
                            />
                          </div>
                          <Select value={selectedCategory} onValueChange={setSelectedCategory}>
                            <SelectTrigger className="w-[180px]">
                              <ListFilter className="h-4 w-4 mr-2" />
                              <SelectValue placeholder="Category" />
                            </SelectTrigger>
                            <SelectContent>
                              {categoryList.map(cat => (
                                <SelectItem key={cat} value={cat}>{cat}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>

                        {gitClusters.length > 0 && !gitSearch && selectedCategory === "All" && (
                          <div className="space-y-2">
                            <p className="text-[10px] uppercase tracking-wider font-bold text-muted-foreground">Detected Clusters</p>
                            <div className="flex flex-wrap gap-2">
                              {gitClusters.slice(0, 5).map(cluster => (
                                <Button
                                  key={cluster.name}
                                  variant="outline"
                                  size="sm"
                                  className="h-7 text-xs border-dashed border-primary/40 hover:bg-primary/5"
                                  onClick={() => setGitSearch(cluster.name.toLowerCase() + "-")}
                                >
                                  <LayoutGrid className="h-3 w-3 mr-1" />
                                  {cluster.name} ({cluster.count})
                                </Button>
                              ))}
                            </div>
                          </div>
                        )}
                        {gitLoading ? (
                          <div className="flex items-center justify-center py-6">
                            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                          </div>
                        ) : (
                          <div className="max-h-48 overflow-y-auto border rounded-lg divide-y">
                            {filteredRepos.map((repo: any) => (
                              <button
                                key={repo.id}
                                type="button"
                                className={cn(
                                  "w-full flex items-center gap-3 px-3 py-2 text-sm text-left hover:bg-muted/50 transition-colors",
                                  repoUrl === repo.clone_url && "bg-primary/5 border-l-2 border-primary"
                                )}
                                onClick={() => setRepoUrl(repo.clone_url)}
                              >
                                <div className="flex h-8 w-8 items-center justify-center rounded bg-muted/50 border border-border/50 flex-shrink-0">
                                  <span className="text-sm">{STACK_ICONS[repo.language?.toLowerCase()] || "📦"}</span>
                                </div>
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2">
                                    <p className="font-medium truncate">{repo.name}</p>
                                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary font-medium">{repo.category || 'App'}</span>
                                  </div>
                                  <p className="text-xs text-muted-foreground truncate">{repo.description || 'No description'}</p>
                                </div>
                                {repo.private && (
                                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-600">Private</span>
                                )}
                              </button>
                            ))}
                            {filteredRepos.length === 0 && (
                              <p className="text-sm text-muted-foreground text-center py-4">No repositories found</p>
                    )}
                  </div>
                )}
                        <div className="flex items-center gap-2 py-1">
                          <div className="flex-1 h-px bg-border" />
                          <span className="text-xs text-muted-foreground">or enter URL manually</span>
                          <div className="flex-1 h-px bg-border" />
                        </div>
                      </div>
                    )}
                    <div className="space-y-2">
                      <Label>Repository URL</Label>
                      <Input
                        placeholder={`https://${gitProvider}.com/username/repo`}
                        value={repoUrl}
                        onChange={(e) => setRepoUrl(e.target.value)}
                      />
                      {!gitConnected && !gitLoading && (
                        <p className="text-xs text-muted-foreground">
                          Connect your {gitProvider === 'github' ? 'GitHub' : gitProvider === 'gitlab' ? 'GitLab' : 'Bitbucket'} account in{" "}
                          <a className="underline hover:text-foreground" href="/settings">Settings</a>{" "}
                          to browse your repositories.
                        </p>
                      )}
                      {gitLoading && !gitConnected && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                          <Loader2 className="h-3 w-3 animate-spin" /> Fetching {gitProvider} repositories...
                        </div>
                      )}
                      {gitConnected && (
                        <p className="text-xs text-muted-foreground text-emerald-500/80">
                          ✓ Push and Pull Request Webhooks will be configured automatically.
                        </p>
                      )}
                    </div>
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" /> Branch</Label>
                      {branches.length > 0 ? (
                        <select
                          value={branch}
                          onChange={(e) => setBranch(e.target.value)}
                          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        >
                          {branches.map((b: any) => (
                            <option key={b.name || b} value={b.name || b}>{b.name || b}</option>
                          ))}
                        </select>
                      ) : (
                        <Input
                          placeholder={loadingBranches ? "Loading branches..." : "main"}
                          value={branch}
                          onChange={(e) => setBranch(e.target.value)}
                          disabled={loadingBranches}
                        />
                      )}
                      <p className="text-xs text-muted-foreground">Branch, tag, or commit to deploy from. Defaults to <code>main</code>.</p>
                    </div>
                   </div>
                )}

                {sourceType === "upload" && (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5"><UploadCloud className="h-3.5 w-3.5" /> Upload Archive</Label>
                      <p className="text-sm text-muted-foreground">
                        Upload a <code>.zip</code> or <code>.tar.gz</code> of your project to deploy.
                      </p>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept=".zip,.tar.gz,.tgz"
                        className="hidden"
                        onChange={(e) => {
                          const file = e.target.files?.[0]
                          if (file) {
                            setUploadFiles(e.target.files)
                            if (!name) {
                              const baseName = file.name.replace(/\.(zip|tar\.gz|tgz)$/, "")
                              setName(baseName.replace(/[^a-zA-Z0-9-]/g, "-").toLowerCase())
                            }
                          }
                        }}
                      />
                      <Button
                        type="button"
                        variant="outline"
                        className="w-full h-24 border-dashed"
                        onClick={() => fileInputRef.current?.click()}
                      >
                        {uploadFiles && uploadFiles.length > 0 ? (
                          <div className="flex flex-col items-center gap-1">
                            <CheckCircle2 className="h-6 w-6 text-emerald-500" />
                            <span className="text-sm font-medium">{uploadFiles[0].name}</span>
                            <span className="text-xs text-muted-foreground">
                              {(uploadFiles[0].size / 1024 / 1024).toFixed(1)} MB — Click to change
                            </span>
                          </div>
                        ) : (
                          <div className="flex flex-col items-center gap-1">
                            <UploadCloud className="h-6 w-6 text-muted-foreground" />
                            <span className="text-sm font-medium">Click to select archive</span>
                            <span className="text-xs text-muted-foreground">.zip or .tar.gz up to 100MB</span>
                          </div>
                        )}
                      </Button>
                    </div>
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" /> Branch</Label>
                      <Input
                        placeholder="main"
                        value={branch}
                        onChange={(e) => setBranch(e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground">Branch name for this upload. Defaults to <code>main</code>.</p>
                    </div>
                  </div>
                )}

                {sourceType === "template" && (
                  <div className="grid grid-cols-2 gap-4">
                    {templates.length > 0 ? templates.map((t: any) => (
                      <div
                        key={t.id || t.slug}
                        className={cn(
                          "flex items-center gap-3 p-4 rounded-lg border cursor-pointer hover:bg-muted",
                          selectedTemplate === (t.slug || t.id) && "border-primary bg-primary/5"
                        )}
                        onClick={() => setSelectedTemplate(t.slug || t.id)}
                      >
                        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-zinc-900/50 border border-zinc-800 overflow-hidden p-1">
                          {(t.icon || t.logo_url)?.startsWith('http') || (t.icon || t.logo_url)?.startsWith('/') ? (
                            <Image src={t.icon || t.logo_url} alt={t.name} className="h-7 w-7 object-contain" unoptimized />
                          ) : (
                            <span className="text-xl">{t.icon || t.logo_url || '📦'}</span>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="font-medium truncate">{t.name}</p>
                          <p className="text-xs text-muted-foreground truncate">{t.description || t.framework || 'Template'}</p>
                        </div>
                      </div>
                    )) : (
                      <p className="col-span-2 text-center text-sm text-muted-foreground py-8">
                        No templates available. Use a Git repository or Docker image instead.
                      </p>
                    )}
                  </div>
                )}

                {sourceType === "docker" && (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label>Docker Image</Label>
                      <Input
                        placeholder="ghcr.io/org/app:latest"
                        value={dockerImage}
                        onChange={(e) => setDockerImage(e.target.value)}
                      />
                    </div>
                    {registryCredentials.length > 0 && (
                      <div className="space-y-2">
                        <Label>Registry Credential (Optional)</Label>
                        <Select value={registryCredentialId} onValueChange={setRegistryCredentialId}>
                          <SelectTrigger>
                            <SelectValue placeholder="Select credential..." />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none">No credential (public image)</SelectItem>
                            {registryCredentials.map(cred => (
                              <SelectItem key={cred.id} value={cred.id}>
                                {cred.name} ({cred.provider})
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
            )}

            {/* ── STEP 2: AI ANALYSIS ── */}
            {step === 2 && (
              <motion.div
                key="step2"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="space-y-6"
              >
                {analyzing ? (
                  <Card className="border-primary/20">
                    <CardContent className="flex flex-col items-center justify-center py-16 gap-4">
                      <div className="relative">
                        <Sparkles className="h-12 w-12 text-primary animate-pulse" />
                        <div className="absolute inset-0 h-12 w-12 rounded-full border-2 border-primary/30 animate-ping" />
                      </div>
                      <div className="text-center space-y-2">
                        <h3 className="text-lg font-semibold">AI is analyzing your repository...</h3>
                        <p className="text-sm text-muted-foreground">
                          Detecting stack, frameworks, ports, dependencies, and optimal config.
                        </p>
                      </div>
                      <div className="flex gap-2 mt-4">
                        {["Cloning", "Scanning files", "Detecting stack", "Building config"].map((label, i) => (
                          <span key={label} className="text-[10px] px-2 py-1 rounded-full bg-primary/10 text-primary animate-pulse" style={{ animationDelay: `${i * 0.3}s` }}>
                            {label}
                          </span>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                ) : analysis ? (
                  <>
                    {/* Analysis Results */}
                    <Card className="border-emerald-500/30 bg-emerald-500/5">
                      <CardHeader className="pb-3">
                        <div className="flex items-center gap-2">
                          <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                          <CardTitle className="text-lg">Analysis Complete</CardTitle>
                        </div>
                        <CardDescription>SMSLY AI has analyzed your repository and detected the following:</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                          <div className="p-3 rounded-lg bg-background border space-y-1">
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                              <Code2 className="h-3 w-3" /> Stack
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-lg">{STACK_ICONS[analysis.stack] || "📦"}</span>
                              <span className="font-semibold capitalize">{analysis.stack}</span>
                            </div>
                            {analysis.languages && analysis.languages.length > 1 && (
                              <div className="flex gap-1 flex-wrap mt-1">
                                {analysis.languages.map(l => (
                                  <span key={l} className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary capitalize">{l}</span>
                                ))}
                              </div>
                            )}
                          </div>
                          <div className="p-3 rounded-lg bg-background border space-y-1">
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                              <Globe className="h-3 w-3" /> Port
                            </div>
                            <p className="font-semibold text-lg">{analysis.port}</p>
                          </div>
                          <div className="p-3 rounded-lg bg-background border space-y-1">
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                              <Box className="h-3 w-3" /> Build
                            </div>
                            <p className="font-semibold capitalize">{analysis.build}</p>
                          </div>
                          <div className="p-3 rounded-lg bg-background border space-y-1">
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                              <Database className="h-3 w-3" /> Addons
                            </div>
                            {analysis.addons.length > 0 ? (
                              <div className="flex gap-1 flex-wrap">
                                {analysis.addons.map(a => (
                                  <span key={a} className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 font-medium">{a}</span>
                                ))}
                              </div>
                            ) : (
                              <p className="text-sm text-muted-foreground">None</p>
                            )}
                          </div>
                        </div>
                      </CardContent>
                    </Card>

                    {/* Auto vs Manual Choice */}
                    <div className="space-y-3">
                      <h3 className="font-semibold text-lg">How would you like to deploy?</h3>
                      <div className="grid grid-cols-2 gap-4">
                        <Card
                          className={cn(
                            "cursor-pointer transition-all hover:shadow-lg",
                            deployMode === "auto"
                              ? "border-primary bg-primary/5 shadow-primary/10"
                              : "hover:border-primary/50"
                          )}
                          onClick={() => setDeployMode("auto")}
                        >
                          <CardHeader className="text-center pb-2">
                            <Zap className={cn("h-10 w-10 mx-auto mb-2", deployMode === "auto" ? "text-primary" : "text-muted-foreground")} />
                            <CardTitle className="text-lg">🚀 Auto Deploy</CardTitle>
                            <CardDescription className="text-xs">
                              Zero-config — AI handles everything. One click and you&apos;re live.
                            </CardDescription>
                          </CardHeader>
                          <CardContent className="text-center">
                            <div className="space-y-1 text-xs text-muted-foreground">
                              <p>✓ Auto-detect port, build, env</p>
                              <p>✓ Provisions databases if needed</p>
                              <p>✓ Deploys in ~60 seconds</p>
                            </div>
                          </CardContent>
                        </Card>

                        <Card
                          className={cn(
                            "cursor-pointer transition-all hover:shadow-lg",
                            deployMode === "manual"
                              ? "border-primary bg-primary/5 shadow-primary/10"
                              : "hover:border-primary/50"
                          )}
                          onClick={() => setDeployMode("manual")}
                        >
                          <CardHeader className="text-center pb-2">
                            <Settings2 className={cn("h-10 w-10 mx-auto mb-2", deployMode === "manual" ? "text-primary" : "text-muted-foreground")} />
                            <CardTitle className="text-lg">⚙️ Manual Config</CardTitle>
                            <CardDescription className="text-xs">
                              Review and customize before deploying. AI pre-fills everything.
                            </CardDescription>
                          </CardHeader>
                          <CardContent className="text-center">
                            <div className="space-y-1 text-xs text-muted-foreground">
                              <p>✓ Edit name, region, env vars</p>
                              <p>✓ AI suggestions pre-filled</p>
                              <p>✓ Full control over config</p>
                            </div>
                          </CardContent>
                        </Card>
                      </div>
                    </div>
                  </>
                ) : (
                  <Card>
                    <CardContent className="flex flex-col items-center py-12 gap-3">
                      <Sparkles className="h-10 w-10 text-muted-foreground" />
                      <p className="text-muted-foreground">Analysis could not be completed. You can still configure manually.</p>
                      <Button variant="outline" onClick={() => { setDeployMode("manual"); setStep(3) }}>
                        Configure Manually
                      </Button>
                    </CardContent>
                  </Card>
                )}

                {/* ── USER-REQUIRED ENV VAR PROMPT ── */}
                {showEnvPrompt && (
                  <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="mt-6"
                  >
                    <Card className="border-amber-500/30 bg-amber-500/5">
                      <CardHeader className="pb-3">
                        <div className="flex items-center gap-2">
                          <Key className="h-5 w-5 text-amber-500" />
                          <CardTitle className="text-lg">Configure Required Variables</CardTitle>
                        </div>
                        <CardDescription>
                          AI detected variables that need your input. Fill them in or skip to use placeholders.
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {Object.entries(userRequiredVars).map(([key, val]) => {
                          const envVar = Array.isArray(analysis?.env_vars)
                            ? analysis.env_vars.find((ev: AnalysisEnvVar) => ev.key === key)
                            : null
                          return (
                            <div key={key} className="space-y-1.5">
                              <Label className="flex items-center gap-2 font-mono text-sm">
                                <Key className="h-3 w-3 text-amber-500" />
                                {key}
                                {envVar?.required && <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-500 font-medium">Required</span>}
                              </Label>
                              {envVar?.hint && (
                                <p className="text-xs text-muted-foreground pl-5">{envVar.hint}</p>
                              )}
                              <Input
                                type={envVar?.is_secret ? "password" : "text"}
                                placeholder={envVar?.hint || `Enter ${key}`}
                                value={val}
                                onChange={(e) => setUserRequiredVars(prev => ({ ...prev, [key]: e.target.value }))}
                                className="font-mono"
                              />
                            </div>
                          )
                        })}

                        <div className="flex gap-3 pt-4 border-t">
                          <Button
                            onClick={() => {
                              setEnvVars(prev => prev.map(v => {
                                if (v.key in userRequiredVars && userRequiredVars[v.key]) {
                                  return { ...v, value: userRequiredVars[v.key] }
                                }
                                return v
                              }))
                              setShowEnvPrompt(false)
                              setStep(4)
                            }}
                            className="flex-1"
                          >
                            <Rocket className="h-4 w-4 mr-2" />
                            Continue to Deploy
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => {
                              setEnvVars(prev => prev.map(v => {
                                if (v.key in userRequiredVars && !userRequiredVars[v.key] && !v.value) {
                                  return { ...v, value: `CHANGE_ME_${Math.random().toString(36).slice(2, 10)}` }
                                }
                                if (v.key in userRequiredVars && userRequiredVars[v.key]) {
                                  return { ...v, value: userRequiredVars[v.key] }
                                }
                                return v
                              }))
                              setShowEnvPrompt(false)
                              setStep(4)
                            }}
                          >
                            <SkipForward className="h-4 w-4 mr-2" />
                            Skip (use placeholders)
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                )}
              </motion.div>
            )}

            {/* ── STEP 3: CONFIGURE (Manual mode or non-git) ── */}
            {step === 3 && (
              <motion.div
                key="step3"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="space-y-6"
              >
                {analysis && (
                  <div className="flex items-center gap-2 p-3 rounded-lg bg-primary/5 border border-primary/20 text-sm">
                    <Sparkles className="h-4 w-4 text-primary flex-shrink-0" />
                    <span>AI has pre-filled the config below based on your repo analysis. Edit anything you need.</span>
                  </div>
                )}
                <div className="space-y-4">
                  <div className="grid gap-2">
                    <Label>Service Name</Label>
                    <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-awesome-service" />
                  </div>
                  {sourceType === "git" && (
                  <div className="grid gap-2">
                    <Label className="flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" /> Branch</Label>
                    {branches.length > 0 ? (
                      <select
                        value={branch}
                        onChange={(e) => setBranch(e.target.value)}
                        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      >
                        {branches.map((b: any) => (
                          <option key={b.name || b} value={b.name || b}>{b.name || b}</option>
                        ))}
                      </select>
                    ) : (
                      <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
                    )}
                  </div>
                  )}
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

                  {/* Project assignment */}
                  <div className="grid gap-2">
                    <Label className="flex items-center gap-1.5">📦 Project <span className="text-xs text-muted-foreground">(optional)</span></Label>
                    <Select value={selectedProject} onValueChange={setSelectedProject}>
                      <SelectTrigger>
                        <SelectValue placeholder="No project (ungrouped)" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">No project (ungrouped)</SelectItem>
                        {projectsList.map(p => (
                          <SelectItem key={p.id} value={p.id}>
                            {p.icon_emoji} {p.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Custom registry (push to) */}
                  <div className="border rounded-lg p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <Label className="flex items-center gap-1.5">
                        <Server className="h-3.5 w-3.5" /> Custom Push Registry
                        <span className="text-xs text-muted-foreground">(optional)</span>
                      </Label>
                      <Button variant="ghost" size="sm" onClick={() => {
                        if (showRegistryConfig) {
                          setRegistryUrl("")
                          setRegistryUsername("")
                          setRegistryPassword("")
                        }
                        setShowRegistryConfig(!showRegistryConfig)
                      }}>
                        {showRegistryConfig ? "Remove" : "Configure"}
                      </Button>
                    </div>
                    {showRegistryConfig && (
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1.5 col-span-2">
                          <Label className="text-xs">Registry URL</Label>
                          <Input
                            placeholder="registry.example.com:5000"
                            value={registryUrl}
                            onChange={e => setRegistryUrl(e.target.value)}
                            className="h-9"
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label className="text-xs">Username</Label>
                          <Input
                            placeholder="registry-user"
                            value={registryUsername}
                            onChange={e => setRegistryUsername(e.target.value)}
                            className="h-9"
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label className="text-xs">Password / Token</Label>
                          <Input
                            type="password"
                            placeholder="••••••••"
                            value={registryPassword}
                            onChange={e => setRegistryPassword(e.target.value)}
                            className="h-9"
                          />
                        </div>
                        <p className="text-xs text-muted-foreground col-span-2">
                          Sets where built images are pushed for this deployment. Overrides any project-level registry scope.
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Resource allocation */}
                  <div className="grid grid-cols-2 gap-4">
                    <div className="grid gap-2">
                      <Label>CPU Cores</Label>
                      <Input type="number" step="0.1" value={cpuCores} onChange={(e) => setCpuCores(parseFloat(e.target.value) || 0.5)} />
                    </div>
                    <div className="grid gap-2">
                      <Label>Memory (MB)</Label>
                      <Input type="number" step="1" value={memoryMb} onChange={(e) => setMemoryMb(parseInt(e.target.value) || 512)} />
                    </div>
                  </div>

                  {sourceType !== "docker" && (
                    <div className="pt-4">
                      <BuildpackSelector value={buildpack} onChange={setBuildpack} />
                    </div>
                  )}

                  <div className="pt-4">
                    <EnvVarEditor
                      initialVars={envVars}
                      onSave={async (v) => setEnvVars(v)}
                    />
                  </div>
                </div>
              </motion.div>
            )}

            {/* ── STEP 4: REVIEW & DEPLOY ── */}
            {step === 4 && (
              <motion.div
                key="step4"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="space-y-6"
              >
                <Card className="border-primary/20">
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <Rocket className="h-5 w-5 text-primary" />
                      <CardTitle>Review & Deploy</CardTitle>
                    </div>
                    <CardDescription>
                      {deployMode === "auto"
                        ? "AI has configured everything. Ready to launch!"
                        : "Review your configuration below."}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div className="text-muted-foreground">Source</div>
                      <div className="font-medium truncate">
                        {sourceType === "template"
                          ? `Template: ${selectedTemplate}`
                          : sourceType === "docker"
                            ? `Docker: ${dockerImage}`
                            : sourceType === "upload"
                              ? `Upload: ${uploadFiles?.[0]?.name || "archive"}`
                              : repoUrl}
                      </div>
                      <div className="text-muted-foreground">Name</div>
                      <div className="font-medium">{name}</div>
                      <div className="text-muted-foreground">Region</div>
                      <div className="font-medium">{region}</div>
                      {sourceType === "git" && (
                        <>
                          <div className="text-muted-foreground">Branch</div>
                          <div className="font-medium flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" />{branch || "main"}</div>
                        </>
                      )}
                      {analysis && (
                        <>
                          <div className="text-muted-foreground">Stack</div>
                          <div className="font-medium capitalize flex items-center gap-2">
                            <span>{STACK_ICONS[analysis.stack] || "📦"}</span>
                            {analysis.stack}
                            {analysis.languages && analysis.languages.length > 1 && (
                              <span className="text-xs text-muted-foreground">(+{analysis.languages.length - 1} more)</span>
                            )}
                          </div>
                          <div className="text-muted-foreground">Build</div>
                          <div className="font-medium capitalize">{analysis.build}</div>
                          <div className="text-muted-foreground">Port</div>
                          <div className="font-medium">{analysis.port}</div>
                        </>
                      )}
                      <div className="text-muted-foreground">Env Vars</div>
                      <div className="font-medium">{envVars.length} variables defined</div>
                      <div className="text-muted-foreground">Deploy Mode</div>
                      <div className="font-medium flex items-center gap-1.5">
                        {deployMode === "auto" ? (
                          <><Zap className="h-3.5 w-3.5 text-primary" /> Auto (AI-managed)</>
                        ) : (
                          <><Settings2 className="h-3.5 w-3.5" /> Manual</>
                        )}
                      </div>
                      {registryUrl && (
                        <>
                          <div className="text-muted-foreground">Push Registry</div>
                          <div className="font-medium font-mono text-xs truncate" title={registryUrl}>{registryUrl}</div>
                        </>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            )}

            {/* ── STEP 5: TARGET SERVERS ── */}
            {step === 5 && (
              <motion.div
                key="step5"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="space-y-6"
              >
                <Card className="border-primary/20">
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <Server className="h-5 w-5 text-primary" />
                      <CardTitle>Target Servers</CardTitle>
                    </div>
                    <CardDescription>
                      Select the target server for your deployment. You can deploy to the local server or select a single managed node.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {/* Local server selection */}
                    <button
                      type="button"
                      onClick={() => {
                        setIncludeLocal(prev => {
                          const nextVal = !prev
                          if (nextVal) {
                            setSelectedServers([])
                          }
                          return nextVal
                        })
                      }}
                      className={cn(
                        "w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all",
                        includeLocal
                          ? "border-emerald-500/30 bg-emerald-500/5 shadow-sm"
                          : "border-border hover:border-primary/50 hover:bg-muted/30"
                      )}
                    >
                      {/* Checkbox */}
                      <div className={cn(
                        "w-5 h-5 rounded border-2 flex items-center justify-center transition-colors",
                        includeLocal ? "bg-emerald-500 border-emerald-500" : "border-muted-foreground/40"
                      )}>
                        {includeLocal && <CheckCircle2 className="h-3.5 w-3.5 text-white" />}
                      </div>

                      <div className="w-8 h-8 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                        <Monitor className="h-4 w-4 text-emerald-500" />
                      </div>
                      <div className="flex-1">
                        <p className="font-medium text-sm">This Server (Local)</p>
                        <p className="text-xs text-muted-foreground">Primary deployment target</p>
                      </div>
                      {includeLocal && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-500 font-medium">Included</span>
                      )}
                    </button>

                    {/* Remote servers */}
                    {serversLoading ? (
                      <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                        <span className="ml-2 text-sm text-muted-foreground">Loading servers...</span>
                      </div>
                    ) : servers.length === 0 ? (
                      <div className="text-center py-6 text-sm text-muted-foreground">
                        <Server className="h-8 w-8 mx-auto mb-2 opacity-40" />
                        <p>No remote servers connected.</p>
                        <p className="text-xs">Add servers in the Servers tab to deploy across your fleet.</p>
                      </div>
                    ) : (
                      servers.map(server => {
                        const isOnline = server.status === 'ONLINE'
                        const isWorkloadTarget = !server.is_primary && server.allow_user_workloads !== false
                        const isDisabled = !isOnline || !isWorkloadTarget
                        const isSelected = selectedServers.includes(server.id)
                        return (
                          <button
                            key={server.id}
                            type="button"
                            onClick={() => toggleServer(server.id)}
                            disabled={isDisabled}
                            className={cn(
                              "w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all",
                              isSelected && !isDisabled
                                ? "border-primary bg-primary/5 shadow-sm"
                                : !isDisabled
                                  ? "border-border hover:border-primary/50 hover:bg-muted/30"
                                  : "border-border opacity-50 cursor-not-allowed"
                            )}
                          >
                            {/* Checkbox */}
                            <div className={cn(
                              "w-5 h-5 rounded border-2 flex items-center justify-center transition-colors",
                              isSelected ? "bg-primary border-primary" : "border-muted-foreground/40"
                            )}>
                              {isSelected && <CheckCircle2 className="h-3.5 w-3.5 text-primary-foreground" />}
                            </div>

                            {/* Server icon */}
                            <div className={cn(
                              "w-8 h-8 rounded-lg flex items-center justify-center",
                              isOnline ? "bg-blue-500/10" : "bg-red-500/10"
                            )}>
                              <Server className={cn("h-4 w-4", isOnline ? "text-blue-500" : "text-red-500")} />
                            </div>

                            {/* Server info */}
                            <div className="flex-1 min-w-0">
                              <p className="font-medium text-sm truncate">{server.name}</p>
                              <p className="text-xs text-muted-foreground truncate">
                                {server.host} · {server.services_count || 0} services
                                {server.server_version ? ` · v${server.server_version}` : ''}
                              </p>
                              {!isWorkloadTarget && (
                                <p className="text-[10px] text-amber-500 font-medium">
                                  Control-plane or workloads disabled
                                </p>
                              )}
                            </div>

                            {/* Status */}
                            <div className="flex items-center gap-1.5">
                              {isOnline ? (
                                <Wifi className="h-3.5 w-3.5 text-emerald-500" />
                              ) : (
                                <WifiOff className="h-3.5 w-3.5 text-red-500" />
                              )}
                              <span className={cn(
                                "text-[10px] font-medium",
                                isOnline ? "text-emerald-500" : "text-red-500"
                              )}>
                                {isOnline ? 'Online' : 'Offline'}
                              </span>
                            </div>
                          </button>
                        )
                      })
                    )}

                    {(selectedServers.length > 0 || includeLocal) && (
                      <div className="flex items-center gap-2 pt-2 text-sm">
                        <Rocket className="h-4 w-4 text-primary" />
                        <span>
                          Deploying to <strong>
                            {includeLocal ? 'Local Server' : `${servers.find(s => s.id === selectedServers[0])?.name || 'Remote Node'}`}
                          </strong>
                        </span>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Deploy results (shown after deploying) */}
                {deployResults && (
                  <Card className="border-emerald-500/20 bg-emerald-500/5">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-base flex items-center gap-2">
                        <Rocket className="h-4 w-4 text-emerald-500" /> Deploy Progress
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      {/* Local */}
                      <div className="flex items-center justify-between text-sm py-1">
                        <span className="font-medium">Local Server</span>
                        <span className={cn(
                          "text-xs px-2 py-0.5 rounded-full font-medium",
                          deployResults.local?.status === 'queued' ? "bg-emerald-500/10 text-emerald-500" :
                          deployResults.local?.status === 'error' ? "bg-red-500/10 text-red-500" :
                          "bg-yellow-500/10 text-yellow-500"
                        )}>
                          {deployResults.local?.status || 'pending'}
                        </span>
                      </div>
                      {/* Remotes */}
                      {deployResults.remotes?.map((r: any) => (
                        <div key={r.server_id} className="flex items-center justify-between text-sm py-1 border-t border-border/50">
                          <span className="font-medium">{r.server_name}</span>
                          <div className="flex items-center gap-2">
                            {r.auto_created && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">Auto-created</span>
                            )}
                            <span className={cn(
                              "text-xs px-2 py-0.5 rounded-full font-medium",
                              r.status === 'queued' ? "bg-emerald-500/10 text-emerald-500" :
                              r.status === 'error' ? "bg-red-500/10 text-red-500" :
                              "bg-yellow-500/10 text-yellow-500"
                            )}>
                              {r.status}
                            </span>
                          </div>
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* ── Navigation Buttons ──────────────────────────── */}
          <div className="flex justify-end gap-4 pt-4">
            {step > 1 && (
              <Button variant="outline" onClick={handleBack} disabled={isDeploying || analyzing}>
                Back
              </Button>
            )}
            {step < 5 ? (
              <Button onClick={handleNext} disabled={analyzing || (step === 2 && !deployMode && !analyzing && !!analysis)}>
                {analyzing ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Analyzing...</>
                ) : step === 2 && deployMode === "auto" ? (
                  <>Deploy Now <Rocket className="ml-2 h-4 w-4" /></>
                ) : (
                  <>Next <ArrowRight className="ml-2 h-4 w-4" /></>
                )}
              </Button>
            ) : (
              <Button onClick={handleDeploy} disabled={isDeploying} size="lg" className="px-8">
                {isDeploying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Rocket className="mr-2 h-4 w-4" />}
                {isDeploying
                  ? "Deploying..."
                  : (selectedServers.length + (includeLocal ? 1 : 0)) > 1
                    ? `🚀 Deploy to ${selectedServers.length + (includeLocal ? 1 : 0)} Servers`
                    : "🚀 Deploy Service"
                }
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
    </DashboardShell>
  )
}

function cn(...classes: (string | undefined | null | boolean)[]) {
  return classes.filter(Boolean).join(" ")
}
