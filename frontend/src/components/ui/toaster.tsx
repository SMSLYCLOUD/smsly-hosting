"use client"

import { useToast } from "@/components/ui/use-toast"
import { X } from "lucide-react"

export function Toaster() {
    const { toasts, dismiss } = useToast()

    return (
        <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
            {toasts.map((toast) => (
                <div
                    key={toast.id}
                    className={`rounded-lg border px-4 py-3 shadow-lg transition-all ${toast.variant === "destructive"
                            ? "border-red-500 bg-red-50 text-red-900 dark:bg-red-900 dark:text-red-50"
                            : "border-border bg-background text-foreground"
                        }`}
                >
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                            {toast.title && <div className="font-semibold">{toast.title}</div>}
                            {toast.description && (
                                <div className="text-sm opacity-90">{toast.description}</div>
                            )}
                        </div>
                        <button
                            type="button"
                            aria-label="Dismiss notification"
                            onClick={() => dismiss(toast.id)}
                            className="rounded-md p-1 opacity-70 hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </div>
                    {toast.action}
                </div>
            ))}
        </div>
    )
}
