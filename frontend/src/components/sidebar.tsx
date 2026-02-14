"use client"

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard, PlusCircle, Settings, Box, Activity,
  Server, Rocket, Globe, ChevronDown, Wifi, WifiOff, ExternalLink
} from "lucide-react";
import { serversApi, type ManagedServer } from "@/lib/api";

export function Sidebar() {
  const pathname = usePathname();
  const [servers, setServers] = React.useState<ManagedServer[]>([]);
  const [activeServer, setActiveServer] = React.useState<string | null>(null);
  const [showSelector, setShowSelector] = React.useState(false);

  // Load managed servers for the selector
  React.useEffect(() => {
    serversApi.list().then(setServers).catch(() => {});
    // Load persisted active server
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
      // Dispatch custom event so dashboard can react
      window.dispatchEvent(new CustomEvent("smsly:server-changed", { detail: serverId }));
    }
  };

  const activeServerObj = servers.find(s => s.id === activeServer);

  const routes = [
    {
      label: "Dashboard",
      icon: LayoutDashboard,
      href: "/dashboard",
      active: pathname === "/dashboard",
    },
    {
      label: "Services",
      icon: Box,
      href: "/services",
      active: pathname?.startsWith("/services"),
    },
    {
      label: "Deployments",
      icon: Rocket,
      href: "/deployments",
      active: pathname === "/deployments",
    },
    {
      label: "Servers",
      icon: Server,
      href: "/servers",
      active: pathname === "/servers",
    },
    {
      label: "Ecosystem",
      icon: Globe,
      href: "/ecosystem",
      active: pathname === "/ecosystem",
    },
    {
      label: "New Project",
      icon: PlusCircle,
      href: "/new",
      active: pathname === "/new",
    },
    {
      label: "Intelligence",
      icon: Activity,
      href: "/intelligence",
      active: pathname === "/intelligence",
    },
    {
      label: "Settings",
      icon: Settings,
      href: "/settings",
      active: pathname === "/settings",
    },
  ];

  return (
    <div className="space-y-4 py-4 flex flex-col h-full bg-slate-900 text-white">
      <div className="px-3 py-2 flex-1">
        <Link href="/dashboard" className="flex items-center pl-3 mb-14">
          <div className="relative w-8 h-8 mr-4">
            <div className="w-8 h-8 bg-emerald-500 rounded-lg flex items-center justify-center font-bold">S</div>
          </div>
          <h1 className="text-2xl font-bold">CloudNeuron</h1>
        </Link>
        <div className="space-y-1">
          {routes.map((route) => (
            <Link
              key={route.href}
              href={route.href}
              className={cn(
                "text-sm group flex p-3 w-full justify-start font-medium cursor-pointer hover:text-white hover:bg-white/10 rounded-lg transition",
                route.active ? "text-white bg-white/10" : "text-zinc-400"
              )}
            >
              <div className="flex items-center flex-1">
                <route.icon className={cn("h-5 w-5 mr-3", route.active ? "text-emerald-400" : "text-zinc-400")} />
                {route.label}
              </div>
            </Link>
          ))}
        </div>
      </div>

      {/* Server Selector — bottom of sidebar */}
      {servers.length > 0 && (
        <div className="px-3 pb-2 border-t border-white/10 pt-3">
          <p className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold mb-2 px-1">
            Active Server
          </p>
          <div className="relative">
            <button
              onClick={() => setShowSelector(!showSelector)}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 transition text-sm text-left"
            >
              {activeServerObj ? (
                <>
                  {activeServerObj.status === "ONLINE"
                    ? <Wifi className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                    : <WifiOff className="h-3.5 w-3.5 text-red-400 shrink-0" />
                  }
                  <span className="truncate flex-1">{activeServerObj.name}</span>
                </>
              ) : (
                <>
                  <Server className="h-3.5 w-3.5 text-zinc-500 shrink-0" />
                  <span className="truncate flex-1 text-zinc-400">Local (this server)</span>
                </>
              )}
              <ChevronDown className={cn("h-3.5 w-3.5 text-zinc-500 transition-transform", showSelector && "rotate-180")} />
            </button>

            {/* Dropdown */}
            {showSelector && (
              <div className="absolute bottom-full left-0 right-0 mb-1 bg-slate-800 border border-white/10 rounded-lg shadow-xl overflow-hidden z-50">
                {/* Local option */}
                <button
                  onClick={() => handleSelectServer(null)}
                  className={cn(
                    "w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-white/10 transition text-left",
                    !activeServer && "bg-emerald-500/10 text-emerald-400"
                  )}
                >
                  <Server className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">Local (this server)</span>
                </button>

                {servers.map(srv => (
                  <button
                    key={srv.id}
                    onClick={() => handleSelectServer(srv.id)}
                    className={cn(
                      "w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-white/10 transition text-left",
                      activeServer === srv.id && "bg-emerald-500/10 text-emerald-400"
                    )}
                  >
                    {srv.status === "ONLINE"
                      ? <Wifi className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                      : <WifiOff className="h-3.5 w-3.5 text-red-400 shrink-0" />
                    }
                    <span className="truncate flex-1">{srv.name}</span>
                    <span className="text-[10px] text-zinc-500">{srv.services_count} svc</span>
                  </button>
                ))}

                <Link
                  href="/servers"
                  className="flex items-center gap-2 px-3 py-2 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-white/5 transition border-t border-white/5"
                  onClick={() => setShowSelector(false)}
                >
                  <ExternalLink className="h-3 w-3" />
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
