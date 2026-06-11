import { Suspense } from "react";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { GrafanaEmbed } from "@/components/observability/GrafanaEmbed";

export const dynamic = "force-dynamic";

export const metadata = {
    title: "Grafana — SMSLY Hosting",
    description: "Platform dashboards and embed views for the SMSLY Hosting observability stack.",
};

export default async function GrafanaPage({
    searchParams,
}: {
    searchParams: Promise<{ dashboard?: string; service?: string; time?: string }>;
}) {
    const resolvedSearchParams = await searchParams;
    const dashboard = resolvedSearchParams?.dashboard || "smsly-platform";
    const service = resolvedSearchParams?.service;
    const time = resolvedSearchParams?.time || "now-1h";

    return (
        <DashboardShell>
            <div className="container mx-auto py-8">
                <div className="mb-6">
                    <h1 className="text-3xl font-bold tracking-tight">Grafana</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Live platform telemetry. Times, dashboards and time ranges follow the URL parameters.
                    </p>
                </div>

                <div className="rounded-lg border border-border bg-card shadow-sm overflow-hidden">
                    <Suspense fallback={
                        <div className="p-12 text-center text-muted-foreground">
                            Loading Grafana embed…
                        </div>
                    }>
                        <GrafanaEmbed
                            dashboard={dashboard}
                            service={service}
                            time={time}
                        />
                    </Suspense>
                </div>
            </div>
        </DashboardShell>
    );
}
