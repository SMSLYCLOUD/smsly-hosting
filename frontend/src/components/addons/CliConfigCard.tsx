"use client";

import { useState, useEffect } from "react";
import { addonsApi } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { TerminalSquare, RefreshCw, Eye, EyeOff } from "lucide-react";

interface CliConfig {
    addon_type: string;
    binary: string;
    auth_mode: string;
    model_file: boolean;
    configured: boolean;
    model: string;
    provider: string;
    api_key_env: string;
    api_key_set: boolean;
    suggested_env: string;
}

const AUTH_HINTS: Record<string, string> = {
    OPENCODE: "Runs headless on provider API keys from the environment. Set the key + model here; no login needed.",
    COMMANDCODE: "Uses COMMAND_CODE_API_KEY when set, else its own login. Model is written to its config file.",
    ANTIGRAVITYCLI: "Auth is interactive (Google). This stores nothing secret — expose the status page or exec in to log in.",
    KIMCHI: "KIMCHI_API_KEY env takes precedence; the key is also written to its config file.",
    FORGECODE: "Auth via interactive :login. The stored key is passed through as environment.",
    DEEPAGENTS: "Provider keys via environment (e.g. OPENAI_API_KEY). Stored key is passed through.",
    QWENCODE: "Auth via interactive /auth. The stored key is passed through as environment.",
    FACTORYDROID: "Browser sign-in normally; model selection is written to its settings file.",
};

export default function CliConfigCard({ addonId, addonType }: { addonId: string; addonType: string }) {
    const { toast } = useToast();
    const [cfg, setCfg] = useState<CliConfig | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [apiKey, setApiKey] = useState("");
    const [showKey, setShowKey] = useState(false);
    const [keyEnv, setKeyEnv] = useState("");
    const [model, setModel] = useState("");
    const [provider, setProvider] = useState("");

    useEffect(() => {
        async function load() {
            try {
                const data = await addonsApi.getCliConfig(addonId);
                setCfg(data);
                setKeyEnv(data.api_key_env || "");
                setModel(data.model || "");
                setProvider(data.provider || "");
            } catch {
                toast({ title: "Error", description: "Failed to load CLI config.", variant: "destructive" });
            } finally {
                setLoading(false);
            }
        }
        load();
    }, [addonId, toast]);

    const handleSave = async (clearKey = false) => {
        setSaving(true);
        try {
            const payload: Record<string, string> = { api_key_env: keyEnv.trim(), model: model.trim(), provider: provider.trim() };
            if (clearKey) payload.api_key = "";
            else if (apiKey) payload.api_key = apiKey;
            const res = await addonsApi.setCliConfig(addonId, payload);
            if (res.status === "saved_reprovisioning") {
                toast({ title: "Saved — reprovisioning", description: "New key env applies after the container recreates." });
            } else {
                toast({ title: "CLI config saved", description: res.push_error ? `Warning: ${res.push_error}` : "Config pushed to the container." });
            }
            setApiKey("");
            const data = await addonsApi.getCliConfig(addonId);
            setCfg(data);
        } catch (err: any) {
            toast({ title: "Error", description: err?.response?.data?.error || "Failed to save CLI config.", variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <Card><CardContent className="pt-6 text-center text-muted-foreground">Loading CLI config…</CardContent></Card>;

    return (
        <Card className="border-emerald-500/20">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <TerminalSquare className="w-5 h-5 text-emerald-500" />
                    CLI Config — <code className="font-mono">{cfg?.binary || addonType}</code>
                </CardTitle>
                <CardDescription>
                    Internal-only. {AUTH_HINTS[addonType] || ""}
                    {cfg?.model_file === false && " Model selection is interactive for this CLI; the value below is stored and passed through as environment."}
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="text-sm font-medium">API key {cfg?.api_key_set && <span className="text-emerald-500">(set)</span>}</label>
                        <div className="relative">
                            <input
                                type={showKey ? "text" : "password"}
                                value={apiKey}
                                onChange={(e) => setApiKey(e.target.value)}
                                placeholder={cfg?.api_key_set ? "•••••••• (enter new to replace)" : "Paste API key"}
                                className="w-full mt-1 px-3 py-2 rounded-md border border-input bg-background text-sm font-mono pr-10"
                            />
                            <button type="button" onClick={() => setShowKey(!showKey)} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground">
                                {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </button>
                        </div>
                    </div>
                    <div>
                        <label className="text-sm font-medium">Key env var</label>
                        <input
                            value={keyEnv}
                            onChange={(e) => setKeyEnv(e.target.value.toUpperCase())}
                            placeholder={cfg?.suggested_env}
                            className="w-full mt-1 px-3 py-2 rounded-md border border-input bg-background text-sm font-mono"
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium">Model</label>
                        <input
                            value={model}
                            onChange={(e) => setModel(e.target.value)}
                            placeholder="e.g. anthropic/claude-sonnet-4-5"
                            className="w-full mt-1 px-3 py-2 rounded-md border border-input bg-background text-sm font-mono"
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium">Provider <span className="text-muted-foreground font-normal">(command-code)</span></label>
                        <input
                            value={provider}
                            onChange={(e) => setProvider(e.target.value)}
                            placeholder="anthropic, codex, command-code…"
                            className="w-full mt-1 px-3 py-2 rounded-md border border-input bg-background text-sm font-mono"
                        />
                    </div>
                </div>
                <div className="flex justify-end gap-2">
                    {cfg?.api_key_set && (
                        <Button variant="outline" size="sm" onClick={() => handleSave(true)} disabled={saving}>
                            Clear key
                        </Button>
                    )}
                    <Button size="sm" onClick={() => handleSave(false)} disabled={saving}>
                        {saving && <RefreshCw className="w-3 h-3 animate-spin mr-2" />}
                        Save CLI config
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
