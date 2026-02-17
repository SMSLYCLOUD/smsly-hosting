import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
    "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
    {
        variants: {
            variant: {
                default:
                    "border-transparent bg-primary text-primary-foreground hover:bg-primary/80",
                secondary:
                    "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
                destructive:
                    "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80",
                outline: "text-foreground",
                success:
                    "border-transparent bg-emerald-500/20 text-emerald-600 hover:bg-emerald-500/30 border-emerald-500/30",
                warning:
                    "border-transparent bg-yellow-500/20 text-yellow-600 hover:bg-yellow-500/30 border-yellow-500/30",
                info:
                    "border-transparent bg-blue-500/20 text-blue-600 hover:bg-blue-500/30 border-blue-500/30",
                purple:
                    "border-transparent bg-purple-500/20 text-purple-600 hover:bg-purple-500/30 border-purple-500/30",
                gray:
                    "border-transparent bg-slate-500/20 text-slate-600 hover:bg-slate-500/30 border-slate-500/30",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    }
)

export interface BadgeProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> { }

function Badge({ className, variant, ...props }: BadgeProps) {
    return (
        <div className={cn(badgeVariants({ variant }), className)} {...props} />
    )
}

export { Badge, badgeVariants }
