'use client';

import { useState, Suspense } from 'react';
import { servicesApi } from '@/lib/api';
import { useRouter, useSearchParams } from 'next/navigation';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Github, Upload, Container, LayoutTemplate, Loader2 } from 'lucide-react';

function NewServiceForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [activeTab, setActiveTab] = useState('git');

  // Shared State
  const [name, setName] = useState(searchParams.get('name') || '');
  const [port, setPort] = useState(searchParams.get('port') || '8000');
  const [domain, setDomain] = useState('');

  // Git State
  const [repoUrl, setRepoUrl] = useState(searchParams.get('repo') || '');
  const [branch, setBranch] = useState('main');

  // Docker State
  const [dockerImage, setDockerImage] = useState('');

  // Upload State
  const [file, setFile] = useState<File | null>(null);

  // Template State
  const [selectedTemplate, setSelectedTemplate] = useState('');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      let payload: any = {
        name,
        internal_port: parseInt(port),
        public_domain: domain,
        deploy_type: activeTab.toUpperCase()
      };

      if (activeTab === 'git') {
        payload = { ...payload, repository_url: repoUrl, branch };
      } else if (activeTab === 'docker') {
        payload = { ...payload, docker_image: dockerImage };
      } else if (activeTab === 'template') {
        payload = { ...payload, template_id: selectedTemplate };
      } else if (activeTab === 'upload') {
        if (!file) throw new Error("Please select a file to upload");
        const formData = new FormData();
        formData.append('name', name);
        formData.append('internal_port', port);
        formData.append('public_domain', domain);
        formData.append('deploy_type', 'UPLOAD');
        formData.append('source_file', file);
        payload = formData; // api.create handles FormData
      }

      // 1. Create Service
      const service = await servicesApi.create(payload);

      // 2. Trigger Initial Deployment (if not upload/template handled automatically by create)
      if (activeTab === 'git' || activeTab === 'docker') {
          await servicesApi.deploy(service.id, activeTab === 'git' ? 'HEAD' : 'latest');
      }

      router.push(`/services/${service.id}`);
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || err.message || 'Failed to deploy service');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex flex-col bg-background">
      <Navbar />
      <div className="flex-1 container max-w-4xl mx-auto p-4 md:p-8">
        <h1 className="text-3xl font-bold mb-2">Deploy New Service</h1>
        <p className="text-muted-foreground mb-8">Choose how you want to deploy your application.</p>

        <Tabs defaultValue="git" value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList className="grid w-full grid-cols-4 mb-8 h-auto p-1 bg-muted/50">
                <TabsTrigger value="git" className="flex flex-col gap-2 py-3 data-[state=active]:bg-background data-[state=active]:shadow-sm">
                    <Github className="w-5 h-5" />
                    <span>Git Repo</span>
                </TabsTrigger>
                <TabsTrigger value="docker" className="flex flex-col gap-2 py-3 data-[state=active]:bg-background data-[state=active]:shadow-sm">
                    <Container className="w-5 h-5" />
                    <span>Docker</span>
                </TabsTrigger>
                <TabsTrigger value="upload" className="flex flex-col gap-2 py-3 data-[state=active]:bg-background data-[state=active]:shadow-sm">
                    <Upload className="w-5 h-5" />
                    <span>Upload</span>
                </TabsTrigger>
                <TabsTrigger value="template" className="flex flex-col gap-2 py-3 data-[state=active]:bg-background data-[state=active]:shadow-sm">
                    <LayoutTemplate className="w-5 h-5" />
                    <span>Template</span>
                </TabsTrigger>
            </TabsList>

            <Card className="p-6">
                <form onSubmit={handleSubmit} className="space-y-6">
                    {error && (
                        <div className="p-3 bg-destructive/10 border border-destructive/20 text-destructive rounded-md text-sm">
                            {error}
                        </div>
                    )}

                    <div className="grid md:grid-cols-2 gap-6">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Service Name</label>
                            <input
                                type="text"
                                required
                                placeholder="my-app"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                            />
                        </div>
                         <div className="space-y-2">
                            <label className="text-sm font-medium">Internal Port</label>
                            <input
                                type="number"
                                placeholder="8000"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={port}
                                onChange={(e) => setPort(e.target.value)}
                            />
                        </div>
                    </div>

                    <TabsContent value="git" className="space-y-4 mt-0">
                         <div className="space-y-2">
                            <label className="text-sm font-medium">Repository URL</label>
                            <input
                                type="url"
                                placeholder="https://github.com/username/repo"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={repoUrl}
                                onChange={(e) => setRepoUrl(e.target.value)}
                                required={activeTab === 'git'}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Branch</label>
                            <input
                                type="text"
                                placeholder="main"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={branch}
                                onChange={(e) => setBranch(e.target.value)}
                            />
                        </div>
                    </TabsContent>

                    <TabsContent value="docker" className="space-y-4 mt-0">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Docker Image</label>
                            <input
                                type="text"
                                placeholder="nginx:latest"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={dockerImage}
                                onChange={(e) => setDockerImage(e.target.value)}
                                required={activeTab === 'docker'}
                            />
                            <p className="text-xs text-muted-foreground">Image must be public or you must provide credentials in settings.</p>
                        </div>
                    </TabsContent>

                    <TabsContent value="upload" className="space-y-4 mt-0">
                        <div className="border-2 border-dashed border-muted-foreground/25 rounded-lg p-8 text-center hover:bg-muted/50 transition-colors">
                            <Upload className="w-10 h-10 mx-auto text-muted-foreground mb-4" />
                            <p className="text-sm font-medium mb-2">Drag and drop your source code archive</p>
                            <p className="text-xs text-muted-foreground mb-4">Supported formats: .zip, .tar.gz</p>
                            <input
                                type="file"
                                accept=".zip,.tar,.tar.gz"
                                onChange={(e) => setFile(e.target.files?.[0] || null)}
                                className="block w-full text-sm text-slate-500
                                file:mr-4 file:py-2 file:px-4
                                file:rounded-full file:border-0
                                file:text-sm file:font-semibold
                                file:bg-primary file:text-primary-foreground
                                hover:file:bg-primary/90"
                            />
                        </div>
                    </TabsContent>

                    <TabsContent value="template" className="space-y-4 mt-0">
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                            {['PostgreSQL', 'Redis', 'MongoDB', 'MySQL', 'WordPress', 'n8n'].map((t) => (
                                <div
                                    key={t}
                                    className={`p-4 border rounded-lg cursor-pointer transition-all ${selectedTemplate === t ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'hover:border-primary/50'}`}
                                    onClick={() => setSelectedTemplate(t)}
                                >
                                    <div className="font-medium text-center">{t}</div>
                                </div>
                            ))}
                        </div>
                    </TabsContent>

                    <div className="pt-4 border-t">
                        <div className="space-y-2 mb-4">
                            <label className="text-sm font-medium">Public Domain (Optional)</label>
                            <input
                                type="text"
                                placeholder="app.example.com"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={domain}
                                onChange={(e) => setDomain(e.target.value)}
                            />
                        </div>

                        <Button type="submit" className="w-full bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-600 hover:to-cyan-600 font-bold" disabled={loading}>
                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : 'Deploy Project'}
                        </Button>
                    </div>
                </form>
            </Card>
        </Tabs>
      </div>
    </main>
  );
}

export default function NewServicePage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <NewServiceForm />
    </Suspense>
  );
}
