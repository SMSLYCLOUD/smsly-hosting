const features = [
    {
        icon: GitBranch,
        title: "Deployment Previews",
        description: "Spin up isolated, ephemeral environments for every pull request with auto-injected secrets and storage.",
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        icon: Database,
        title: "Database Cloning",
        description: "Zero-copy PostgreSQL template cloning provides instant staging data for previews without the wait.",
        color: "text-purple-500",
        bg: "bg-purple-500/10"
    },
    {
        icon: Bot,
        title: "Multi-Provider AI Engine",
        description: "17 AI providers with Senate Committee deliberation. Auto-diagnose failures, recommend fixes, and apply remediation.",
        color: "text-violet-500",
        bg: "bg-violet-500/10"
    },
    {
        icon: Container,
        title: "Addon Marketplace",
        description: "35+ managed data services ΓÇö PostgreSQL, Redis, MongoDB, Kafka, Elasticsearch, MinIO, Qdrant, and more ΓÇö one-click provision.",
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        icon: Workflow,
        title: "Blueprints & AI Clusters",
        description: "One-click deployment for GPU-accelerated LLMs like Ollama, DeepSeek, and custom private data stacks.",
        color: "text-amber-500",
        bg: "bg-amber-500/10"
    },
    {
        icon: Server,
        title: "Managed Fleet Servers",
        description: "Connect, provision, and orchestrate multiple VPS nodes. Auto health-check, token exchange, and cluster management.",
        color: "text-indigo-500",
        bg: "bg-indigo-500/10"
    },
    {
        icon: Waypoints,
        title: "Dev Tunnels & Subdomains",
        description: "Expose local dev servers via public URLs with request inspection, replay, and persistent subdomain reservations.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        icon: Globe,
        title: "Global Edge Routing",
        description: "Automated Let's Encrypt SSL and Caddy proxying routes traffic instantly to your global container mesh.",
        color: "text-teal-500",
        bg: "bg-teal-500/10"
    },
    {
        icon: Activity,
        title: "Observability & Mesh",
        description: "Traefik metrics and WireGuard VPN stats feed real-time health insights and autoscale decisions.",
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
    },
    {
        icon: BrainCircuit,
        title: "Auto-Remediation",
        description: "Intelligent log analysis diagnoses crash loops, auto-applies fixes, creates PRs, and re-deploys without human intervention.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: GanttChartSquare,
        title: "Ecosystem Deployer",
        description: "Scan all your repos, build a dependency graph, and deploy 30+ connected microservices in dependency-aware waves.",
        color: "text-pink-500",
        bg: "bg-pink-500/10"
    },
    {
        icon: Blocks,
        title: "Multi-Git Providers",
        description: "Connect GitHub, GitLab, and Bitbucket. Deploy from any provider with unified CI/CD, auto-deploy on push, and instant rollbacks.",
        color: "text-sky-500",
        bg: "bg-sky-500/10"
    },
    {
        icon: Boxes,
        title: "Nixpacks Build Support",
        description: "Auto-detect and build any language with Nixpacks. No Dockerfile needed ΓÇö Python, Node, Go, Rust, Elixir, and more just work.",
        color: "text-fuchsia-500",
        bg: "bg-fuchsia-500/10"
    },
    {
        icon: Cloud,
        title: "S3 Backup Destinations",
        description: "Back up databases, volumes, and configs to S3, Cloudflare R2, or MinIO. Automated schedules with point-in-time recovery.",
        color: "text-amber-500",
        bg: "bg-amber-500/10"
    },
    {
        icon: AppWindow,
        title: "Serverless Functions (FaaS)",
        description: "In-browser Monaco editor to write and deploy Node.js or Python functions instantly ΓÇö no repo, no Dockerfile, no config.",
        color: "text-violet-500",
        bg: "bg-violet-500/10"
    },
    {
        icon: TrendingUp,
        title: "Predictive Auto-Scaling",
        description: "AI-driven scaling that predicts load spikes before they hit. Proactively provisions resources using historical patterns and real-time metrics.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        icon: Terminal,
        title: "Container Terminal",
        description: "Web-based SSH into any running container. Debug, inspect logs, run migrations, and manage state without leaving the dashboard.",
        color: "text-teal-500",
        bg: "bg-teal-500/10"
    },
    {
        icon: Folders,
        title: "File Browser",
        description: "Browse, upload, download, and edit files inside any running container or attached volume ΓÇö no CLI needed.",
        color: "text-yellow-500",
        bg: "bg-yellow-500/10"
    },
    {
        icon: Search,
        title: "Real-Time Log Streaming",
        description: "Tail container logs in real time with Loki-powered search, filtering, and multi-service aggregation ΓÇö all in your browser.",
        color: "text-sky-500",
        bg: "bg-sky-500/10"
    },
    {
        icon: BarChart3,
        title: "Metrics Dashboard",
        description: "CPU, memory, network, and disk metrics powered by Prometheus and cAdvisor. Historical graphs and live snapshots for every service.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: Cpu,
        title: "Horizontal Scaling",
        description: "Scale any service horizontally across replicas. Adjust CPU, memory, and replica count per service with instant apply.",
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        icon: Timer,
        title: "Cron Jobs",
        description: "Schedule recurring tasks per service. Define cron expressions or pre-set intervals ΓÇö backups, cleanup, pings, and automation.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        icon: Network,
        title: "Lite Edge Agents",
        description: "Extend your grid to any VPS or edge location. Lite agents share your master database and registry while running local workloads.",
        color: "text-purple-500",
        bg: "bg-purple-500/10"
    },
    {
        icon: Users,
        title: "Teams & Collaboration",
        description: "Invite team members with role-based access. Manage services, deployments, and environments together ΓÇö unlimited seats, no per-user pricing.",
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        icon: Key,
        title: "API Tokens & CLI",
        description: "Generate scoped API tokens for automated workflows. Full-featured CLI for deployments, logs, secrets, env vars, domains, and certificates.",
        color: "text-fuchsia-500",
        bg: "bg-fuchsia-500/10"
    },
    {
        icon: RefreshCw,
        title: "Blue-Green Deployments",
        description: "Zero-downtime deployments with automatic traffic shifting. Run two identical environments and switch instantly on success.",
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
    },
    {
        icon: Shield,
        title: "Safe Deploy & Approvals",
        description: "Stage rollouts behind approval gates. Deploy to preview, run verification checks, and promote to production with a single click.",
        color: "text-indigo-500",
        bg: "bg-indigo-500/10"
    },
    {
        icon: Cable,
        title: "Docker & Kubernetes Targets",
        description: "Deploy to Docker or Kubernetes clusters. The platform auto-detects your runtime and applies the right orchestrator strategy.",
        color: "text-sky-500",
        bg: "bg-sky-500/10"
    },
    {
        icon: Activity,
        title: "Topology Visualization",
        description: "Interactive service dependency graph showing connections between apps, addons, volumes, domains, and tunnels ΓÇö updated in real time.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: Lock,
        title: "Custom SSL Manager",
        description: "Upload and manage your own SSL certificates per custom domain. Full Let's Encrypt integration with automatic renewal.",
        color: "text-violet-500",
        bg: "bg-violet-500/10"
    },
    {
        icon: ArrowRight,
        title: "Disaster Recovery",
        description: "Automatic master DB snapshots pushed to every lite agent. One-click promote an agent to master if the primary fails ΓÇö no data loss.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    }
];