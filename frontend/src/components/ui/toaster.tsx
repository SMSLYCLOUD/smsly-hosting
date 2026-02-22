"use client"

import { useToast, ToastVariant } from "@/components/ui/use-toast"
import { X, CheckCircle, AlertTriangle, AlertCircle, Info } from "lucide-react"

const variantStyles: Record<ToastVariant, string> = {
    default: "border-border bg-background text-foreground",
    destructive: "border-red-500/50 bg-red-950/90 text-red-50",
    success: "border-emerald-500/50 bg-emerald-950/90 text-emerald-50",
    warning: "border-amber-500/50 bg-amber-950/90 text-amber-50",
    info: "border-blue-500/50 bg-blue-950/90 text-blue-50",
}

const variantIcons: Record<ToastVariant, React.ReactNode> = {
    default: null,
    destructive: <AlertCircle className="h-5 w-5 text-red-400 shrink-0" />,
    success: <CheckCircle className="h-5 w-5 text-emerald-400 shrink-0" />,
    warning: <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0" />,
    info: <Info className="h-5 w-5 text-blue-400 shrink-0" />,
}

export function Toaster() {
    const { toasts, dismiss } = useToast()

    return (
        <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
            {toasts.map((toast) => {
                const variant = toast.variant ?? "default"
                return (
                    <div
                        key={toast.id}
                        className={`pointer-events-auto rounded-lg border px-4 py-3 shadow-2xl backdrop-blur-sm transition-all animate-in slide-in-from-bottom-4 fade-in duration-300 ${variantStyles[variant]}`}
                        role="alert"
                    >
                        <div className="flex items-start gap-3">
                            {variantIcons[variant]}
                            <div className="min-w-0 flex-1">
                                {toast.title && <div className="font-semibold text-sm">{toast.title}</div>}
                                {toast.description && (
                                    <div className="text-sm opacity-80 mt-0.5">{toast.description}</div>
                                )}
                            </div>
                            <button
                                type="button"
                                aria-label="Dismiss notification"
                                onClick={() => dismiss(toast.id)}
                                className="rounded-md p-1 opacity-50 hover:opacity-100 transition-opacity shrink-0"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                        {toast.action}
                    </div>
                )
            })}
        </div>
    )
}
