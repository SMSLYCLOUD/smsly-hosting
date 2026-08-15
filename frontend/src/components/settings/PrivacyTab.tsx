"use client";

import { useState } from "react";
import { Activity, Megaphone, ShieldCheck } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useTelemetryOptOut } from "@/components/telemetry/TelemetryProvider";
import { useAdsDismissed } from "@/components/dashboard/TrulayAdBanner";

/**
 * Privacy & Preferences — anonymous telemetry opt-out and Trulay promo toggle.
 * Both are stored locally in the browser (no server call needed).
 */
export function PrivacyTab() {
  const [telemetryOptedOut, setTelemetryOptedOut] = useTelemetryOptOut();
  const [adsDismissed, setAdsDismissed] = useAdsDismissed();
  const [saved, setSaved] = useState(false);

  const flashSaved = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="space-y-6">
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="p-2.5 rounded-lg bg-primary/10 text-primary flex-shrink-0">
              <Activity size={18} />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Anonymous usage telemetry</h3>
              <p className="text-xs text-muted-foreground mt-1 leading-relaxed max-w-lg">
                Send anonymous performance (Web Vitals) and error reports to Trulay so we can
                improve Grid. No personal data, API keys, or secrets are collected. Your choice
                is saved in this browser.
              </p>
            </div>
          </div>
          <Switch
            checked={!telemetryOptedOut}
            onCheckedChange={(v) => {
              setTelemetryOptedOut(!v);
              flashSaved();
            }}
            aria-label="Anonymous usage telemetry"
          />
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="p-2.5 rounded-lg bg-primary/10 text-primary flex-shrink-0">
              <Megaphone size={18} />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Show Trulay promos</h3>
              <p className="text-xs text-muted-foreground mt-1 leading-relaxed max-w-lg">
                Show the rotating Trulay product banner (communication APIs, identity, growth
                automation) in the dashboard. You can also dismiss it directly from the banner.
              </p>
            </div>
          </div>
          <Switch
            checked={!adsDismissed}
            onCheckedChange={(v) => {
              setAdsDismissed(!v);
              flashSaved();
            }}
            aria-label="Show Trulay promos"
          />
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 flex items-start gap-3">
        <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-500 flex-shrink-0">
          <ShieldCheck size={18} />
        </div>
        <div>
          <h3 className="text-sm font-semibold">Privacy promise</h3>
          <p className="text-xs text-muted-foreground mt-1 leading-relaxed max-w-lg">
            Grid is open source. You can audit exactly what telemetry is collected in{" "}
            <code className="text-[11px] bg-muted px-1 py-0.5 rounded">
              frontend/src/lib/telemetry.ts
            </code>
            . Nothing is ever sent to your own servers, and you can disable it entirely at any
            time.
          </p>
        </div>
      </div>

      {saved && (
        <p className="text-xs text-emerald-500 font-medium">Preferences saved</p>
      )}
    </div>
  );
}