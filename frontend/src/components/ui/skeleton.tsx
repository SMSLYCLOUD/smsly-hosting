"use client";

/**
 * Skeleton Loading Components
 * Provides shimmer loading effects for better UX during data fetching
 */

import { cn } from "@/lib/utils";
import { CSSProperties } from "react";

interface SkeletonProps {
    className?: string;
    style?: CSSProperties;
}

export function Skeleton({ className, style }: SkeletonProps) {
    return (
        <div
            className={cn(
                "animate-pulse rounded-md bg-muted/50",
                className
            )}
            style={style}
        />
    );
}

export function SkeletonCard() {
    return (
        <div className="p-6 rounded border border-border bg-card space-y-4">
            <div className="flex items-center justify-between">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-8 w-8 rounded" />
            </div>
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-3 w-32" />
        </div>
    );
}

export function SkeletonServiceRow() {
    return (
        <div className="flex items-center justify-between p-4 rounded-lg bg-muted/20">
            <div className="flex items-center gap-4">
                <Skeleton className="h-10 w-10 rounded-lg" />
                <div className="space-y-2">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-48" />
                </div>
            </div>
            <Skeleton className="h-6 w-20 rounded-full" />
        </div>
    );
}

export function SkeletonDashboard() {
    return (
        <div className="space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex items-center justify-between">
                <Skeleton className="h-9 w-40" />
                <Skeleton className="h-10 w-32 rounded-lg" />
            </div>

            {/* Stats Grid */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
            </div>

            {/* Content Grid */}
            <div className="grid gap-4 md:grid-cols-5">
                <div className="col-span-3 p-6 rounded border border-border bg-card space-y-4">
                    <Skeleton className="h-6 w-32" />
                    <div className="space-y-3">
                        <SkeletonServiceRow />
                        <SkeletonServiceRow />
                        <SkeletonServiceRow />
                        <SkeletonServiceRow />
                    </div>
                </div>
                <div className="col-span-2 p-6 rounded border border-border bg-card space-y-4">
                    <Skeleton className="h-6 w-24" />
                    <div className="space-y-2">
                        <Skeleton className="h-4 w-full" />
                        <Skeleton className="h-4 w-3/4" />
                        <Skeleton className="h-4 w-1/2" />
                    </div>
                </div>
            </div>
        </div>
    );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
    return (
        <div className="space-y-3">
            {/* Header */}
            <div className="flex items-center gap-4 p-3 border-b border-border">
                <Skeleton className="h-4 w-8" />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-24 ml-auto" />
                <Skeleton className="h-4 w-16" />
            </div>
            {/* Rows */}
            {Array.from({ length: rows }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 p-3">
                    <Skeleton className="h-8 w-8 rounded-full" />
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="h-4 w-32 ml-auto" />
                    <Skeleton className="h-6 w-20 rounded-full" />
                </div>
            ))}
        </div>
    );
}

export function SkeletonLogs() {
    return (
        <div className="font-mono text-sm p-4 bg-zinc-950 rounded-lg space-y-2">
            {Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className="flex items-center gap-2">
                    <Skeleton className="h-3 w-16 bg-zinc-800" />
                    <Skeleton className="h-3 bg-zinc-800" style={{ width: `${Math.random() * 40 + 40}%` }} />
                </div>
            ))}
        </div>
    );
}
