"use client"

import { useToast } from "@/components/ui/use-toast"

export function Toaster() {
    const { toasts } = useToast()

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
                    {toast.title && <div className="font-semibold">{toast.title}</div>}
                    {toast.description && (
                        <div className="text-sm opacity-90">{toast.description}</div>
                    )}
                    {toast.action}
                </div>
            ))}
        </div>
    )
}
