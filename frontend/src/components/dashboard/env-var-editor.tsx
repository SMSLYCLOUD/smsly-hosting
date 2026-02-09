"use client"

import * as React from "react"
import { Plus, Trash2, Eye, EyeOff, Save, Copy, FileText, List } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { cn } from "@/lib/utils"

export interface EnvVar {
  key: string
  value: string
  isSecret: boolean
  id?: string
}

interface EnvVarEditorProps {
  initialVars?: EnvVar[]
  onSave: (vars: EnvVar[]) => Promise<void>
  readOnly?: boolean
}

export function EnvVarEditor({ initialVars = [], onSave, readOnly = false }: EnvVarEditorProps) {
  const [vars, setVars] = React.useState<EnvVar[]>(initialVars)
  const [mode, setMode] = React.useState<"simple" | "bulk">("simple")
  const [bulkText, setBulkText] = React.useState("")
  const [isSaving, setIsSaving] = React.useState(false)

  // Sync simple <-> bulk when switching modes
  React.useEffect(() => {
    if (mode === "bulk") {
      const text = vars
        .map((v) => `${v.key}=${v.value}`)
        .join("\n")
      setBulkText(text)
    } else {
      // Parse bulk text back to simple
      const newVars: EnvVar[] = []
      bulkText.trim().split("\n").forEach((line, i) => {
        const parts = line.split("=")
        if (parts.length >= 2) {
            const key = parts[0].trim()
            const value = parts.slice(1).join("=").trim()
            if (key) {
                // Preserve secret status if key matches existing, else default false
                const existing = vars.find(v => v.key === key)
                newVars.push({
                    key, 
                    value, 
                    isSecret: existing?.isSecret || false,
                    id: existing?.id
                })
            }
        }
      })
      if (newVars.length > 0 || bulkText.trim() === "") {
        setVars(newVars)
      }
    }
  }, [mode])

  const addVar = () => {
    setVars([...vars, { key: "", value: "", isSecret: false }])
  }

  const removeVar = (index: number) => {
    const newVars = [...vars]
    newVars.splice(index, 1)
    setVars(newVars)
  }

  const updateVar = (index: number, field: keyof EnvVar, value: any) => {
    const newVars = [...vars]
    newVars[index] = { ...newVars[index], [field]: value }
    setVars(newVars)
  }

  const handleSave = async () => {
    setIsSaving(true)
    try {
      // If in bulk mode, parse first
      let finalVars = vars
      if (mode === "bulk") {
          const parsed: EnvVar[] = []
          bulkText.trim().split("\n").forEach(line => {
              if (!line.trim() || line.startsWith("#")) return
              const [k, ...v] = line.split("=")
              if (k) parsed.push({ key: k.trim(), value: v.join("=").trim(), isSecret: false })
          })
          finalVars = parsed
      }
      
      await onSave(finalVars)
      toast({ title: "Saved", description: "Environment variables updated successfully." })
    } catch (err) {
      toast({ title: "Error", description: "Failed to save variables.", variant: "destructive" })
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="space-y-4 rounded-lg border p-4 shadow-sm bg-card">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h3 className="text-lg font-medium">Environment Variables</h3>
          <p className="text-sm text-muted-foreground">
            Manage secrets and configuration for your service.
          </p>
        </div>
        <div className="flex items-center gap-2">
            <div className="flex items-center rounded-lg border bg-muted p-1">
                <Button 
                    variant={mode === "simple" ? "secondary" : "ghost"} 
                    size="sm" 
                    onClick={() => setMode("simple")}
                >
                    <List className="mr-2 h-4 w-4" /> Simple
                </Button>
                <Button 
                    variant={mode === "bulk" ? "secondary" : "ghost"} 
                    size="sm" 
                    onClick={() => setMode("bulk")}
                >
                    <FileText className="mr-2 h-4 w-4" /> Bulk
                </Button>
            </div>
        </div>
      </div>

      {mode === "simple" ? (
        <div className="space-y-2">
          {vars.map((v, i) => (
            <div key={i} className="flex gap-2 items-start">
              <Input
                placeholder="KEY"
                value={v.key}
                onChange={(e) => updateVar(i, "key", e.target.value)}
                className="font-mono w-1/3"
                disabled={readOnly}
              />
              <div className="relative flex-1">
                <Input
                    type={v.isSecret ? "password" : "text"}
                    placeholder="VALUE"
                    value={v.value}
                    onChange={(e) => updateVar(i, "value", e.target.value)}
                    className="font-mono pr-10"
                    disabled={readOnly}
                />
                <button
                    type="button"
                    onClick={() => updateVar(i, "isSecret", !v.isSecret)}
                    className="absolute right-3 top-2.5 text-muted-foreground hover:text-foreground"
                >
                    {v.isSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {!readOnly && (
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => removeVar(i)}
                    className="text-destructive hover:bg-destructive/10"
                >
                    <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          ))}
          {!readOnly && (
            <Button variant="outline" size="sm" onClick={addVar} className="w-full mt-2 border-dashed">
                <Plus className="mr-2 h-4 w-4" /> Add Variable
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
            <textarea
                value={bulkText}
                onChange={(e) => setBulkText(e.target.value)}
                className="w-full h-64 font-mono text-sm p-4 rounded-md border bg-muted/50 focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="KEY=VALUE&#10;ANOTHER_KEY=another_value"
                disabled={readOnly}
            />
            <p className="text-xs text-muted-foreground">
                Paste your .env file content here. Secrets are not encrypted until saved.
            </p>
        </div>
      )}

      {!readOnly && (
        <div className="flex justify-end pt-4 border-t">
            <Button onClick={handleSave} disabled={isSaving}>
                <Save className="mr-2 h-4 w-4" />
                {isSaving ? "Saving..." : "Save Changes"}
            </Button>
        </div>
      )}
    </div>
  )
}
