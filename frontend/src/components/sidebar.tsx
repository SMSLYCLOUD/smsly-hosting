"use client"

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { LayoutDashboard, PlusCircle, Settings, Box, Activity } from "lucide-react";

export function Sidebar() {
  const pathname = usePathname();

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
            {/* Logo Placeholder */}
            <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center font-bold">S</div>
          </div>
          <h1 className="text-2xl font-bold">SMSLY</h1>
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
                <route.icon className={cn("h-5 w-5 mr-3", route.active ? "text-indigo-400" : "text-zinc-400")} />
                {route.label}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
