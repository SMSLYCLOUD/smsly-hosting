"use client"

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard, PlusCircle, Settings, Box, Brain,
  Server, Rocket, Globe, ChevronDown, Wifi, WifiOff,
  ExternalLink, Radio
} from "lucide-react";
import { serversApi, type ManagedServer } from "@/lib/api";

export function Sidebar() {
  const pathname = usePathname();
  const [servers, setServers] = React.useState<ManagedServer[]>([]);
  const [activeServer, setActiveServer] = React.useState<string | null>(null);
  const [showSelector, setShowSelector] = React.useState(false);

  React.useEffect(() => {
    serversApi.list().then(setServers).catch(() => {});
    if (typeof window !== "undefined") {
      setActiveServer(localStorage.getItem("smsly_active_server"));
    }
  }, []);

  const handleSelectServer = (serverId: string | null) => {
    setActiveServer(serverId);
    setShowSelector(false);
    if (typeof window !== "undefined") {
      if (serverId) {
        localStorage.setItem("smsly_active_server", serverId);
      } else {
        localStorage.removeItem("smsly_active_server");
      }
      window.dispatchEvent(new CustomEvent("smsly:server-changed", { detail: serverId }));
    }
  };

  const activeServerObj = servers.find(s => s.id === activeServer);

  // ── Grouped navigation ────────────────────────────────────

  const mainRoutes = [
    { label: "Dashboard", icon: LayoutDashboard, href: "/dashboard" },
    { label: "Services",  icon: Box,             href: "/services" },
    { label: "Deployments", icon: Rocket,        href: "/deployments" },
  ];

  const infraRoutes = [
    { label: "Servers",      icon: Server, href: "/servers" },
    { label: "Tunnels",      icon: Radio,  href: "/tunnels" },
    { label: "Ecosystem",    icon: Globe,  href: "/ecosystem" },
    { label: "Intelligence", icon: Brain,  href: "/intelligence" },
  ];

  const utilRoutes = [
    { label: "New Project", icon: PlusCircle, href: "/new" },
    { label: "Settings",    icon: Settings,   href: "/settings" },
  ];

  const isActive = (href: string) =>
    href === "/dashboard" ? pathname === href : pathname?.startsWith(href);

  const renderLinks = (routes: typeof mainRoutes) =>
    routes.map((route) => (
      <Link
        key={route.href}
        href={route.href}
        className={cn(
          "text-[13px] group flex py-1.5 px-2.5 w-full justify-start font-medium cursor-pointer hover:text-white hover:bg-white/10 rounded-md transition",
          isActive(route.href) ? "text-white bg-white/10" : "text-zinc-400"
        )}
      >
        <route.icon className={cn("h-4 w-4 mr-2.5 shrink-0", isActive(route.href) ? "text-emerald-400" : "text-zinc-500")} />
        {route.label}
      </Link>
    ));

  return (
    <div className="flex flex-col h-full bg-slate-900 text-white overflow-y-auto">
      {/* Logo */}
      <div className="px-3 pt-4 pb-4">
        <Link href="/dashboard" className="flex items-center gap-2.5 px-2">
          <div className="w-7 h-7 bg-emerald-500 rounded-lg flex items-center justify-center font-bold text-sm shrink-0">S</div>
          <h1 className="text-lg font-bold tracking-tight">CloudNeuron</h1>
        </Link>
      </div>

      {/* Main nav */}
      <nav className="flex-1 px-2 space-y-4 overflow-y-auto">
        {/* Main */}
        <div className="space-y-0.5">
          {renderLinks(mainRoutes)}
        </div>

        {/* Infrastructure */}
        <div>
          <p className="text-[10px] uppercase tracking-widest text-zinc-600 font-semibold px-2.5 mb-1">Infrastructure</p>
          <div className="space-y-0.5">
            {renderLinks(infraRoutes)}
          </div>
        </div>

        {/* Utilities */}
        <div>
          <p className="text-[10px] uppercase tracking-widest text-zinc-600 font-semibold px-2.5 mb-1">Manage</p>
          <div className="space-y-0.5">
            {renderLinks(utilRoutes)}
          </div>
        </div>
      </nav>

      {/* Server Selector — pinned to bottom */}
      {servers.length > 0 && (
        <div className="px-2 pb-3 pt-2 border-t border-white/5 mt-auto shrink-0">
          <p className="text-[10px] uppercase tracking-wider text-zinc-600 font-semibold mb-1.5 px-1">
            Active Server
          </p>
          <div className="relative">
            <button
              onClick={() => setShowSelector(!showSelector)}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-white/5 hover:bg-white/10 transition text-xs text-left"
            >
              {activeServerObj ? (
                <>
                  {activeServerObj.status === "ONLINE"
                    ? <Wifi className="h-3 w-3 text-emerald-400 shrink-0" />
                    : <WifiOff className="h-3 w-3 text-red-400 shrink-0" />
                  }
                  <span className="truncate flex-1">{activeServerObj.name}</span>
                </>
              ) : (
                <>
                  <Server className="h-3 w-3 text-zinc-500 shrink-0" />
                  <span className="truncate flex-1 text-zinc-400">Local</span>
                </>
              )}
              <ChevronDown className={cn("h-3 w-3 text-zinc-500 transition-transform", showSelector && "rotate-180")} />
            </button>

            {showSelector && (
              <div className="absolute bottom-full left-0 right-0 mb-1 bg-slate-800 border border-white/10 rounded-lg shadow-xl overflow-hidden z-50">
                <button
                  onClick={() => handleSelectServer(null)}
                  className={cn(
                    "w-full flex items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-white/10 transition text-left",
                    !activeServer && "bg-emerald-500/10 text-emerald-400"
                  )}
                >
                  <Server className="h-3 w-3 shrink-0" />
                  <span className="truncate">Local (this server)</span>
                </button>

                {servers.map(srv => (
                  <button
                    key={srv.id}
                    onClick={() => handleSelectServer(srv.id)}
                    className={cn(
                      "w-full flex items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-white/10 transition text-left",
                      activeServer === srv.id && "bg-emerald-500/10 text-emerald-400"
                    )}
                  >
                    {srv.status === "ONLINE"
                      ? <Wifi className="h-3 w-3 text-emerald-400 shrink-0" />
                      : <WifiOff className="h-3 w-3 text-red-400 shrink-0" />
                    }
                    <span className="truncate flex-1">{srv.name}</span>
                    <span className="text-[10px] text-zinc-500">{srv.services_count} svc</span>
                  </button>
                ))}

                <Link
                  href="/servers"
                  className="flex items-center gap-2 px-2.5 py-1.5 text-[10px] text-zinc-500 hover:text-zinc-300 hover:bg-white/5 transition border-t border-white/5"
                  onClick={() => setShowSelector(false)}
                >
                  <ExternalLink className="h-2.5 w-2.5" />
                  Manage servers
                </Link>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
