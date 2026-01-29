import React from 'react';
import Link from 'next/link';
import {
  Activity,
  Terminal,
  Settings,
  Database,
  Layers,
  Shield,
  Clock,
  ArrowLeft
} from 'lucide-react';
import clsx from 'clsx';

const SidebarItem = ({ icon: Icon, label, id, activeTab, onClick }: any) => (
  <button
    onClick={() => onClick(id)}
    className={clsx(
      "flex items-center gap-3 w-full px-4 py-2.5 text-sm font-medium transition-all rounded-lg my-0.5",
      activeTab === id
        ? "bg-emerald-50 text-emerald-700 shadow-sm border border-emerald-100"
        : "text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100"
    )}
  >
    <Icon size={18} className={clsx(activeTab === id ? "text-emerald-600" : "text-zinc-400")} />
    {label}
  </button>
);

export const ServiceLayout = ({ service, activeTab, setActiveTab, children }: any) => {
  return (
    <div className="flex h-screen bg-white text-zinc-900 font-sans">
      {/* Sidebar */}
      <div className="w-64 border-r border-zinc-200 flex flex-col bg-zinc-50/50">
        <div className="p-6 border-b border-zinc-200">
          <Link href="/services" className="flex items-center gap-2 text-zinc-500 hover:text-zinc-900 text-xs uppercase tracking-widest mb-6 transition-colors font-semibold">
            <ArrowLeft size={12} /> Back to Canvas
          </Link>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-white border border-zinc-200 shadow-sm flex items-center justify-center text-xl">
                🚀
            </div>
            <div className="overflow-hidden">
                <h1 className="font-bold text-lg tracking-tight truncate leading-tight" title={service.name}>{service.name}</h1>
                <p className="text-xs text-zinc-500 font-mono mt-0.5 truncate">{service.branch}</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 p-4 overflow-y-auto">
          <div className="text-xs font-bold text-zinc-400 uppercase tracking-wider px-4 mb-2 mt-2">Observe</div>
          <SidebarItem icon={Activity} label="Overview" id="overview" activeTab={activeTab} onClick={setActiveTab} />
          <SidebarItem icon={Layers} label="Metrics" id="metrics" activeTab={activeTab} onClick={setActiveTab} />
          <SidebarItem icon={Terminal} label="Logs" id="logs" activeTab={activeTab} onClick={setActiveTab} />

          <div className="text-xs font-bold text-zinc-400 uppercase tracking-wider px-4 mb-2 mt-6">Manage</div>
          <SidebarItem icon={Database} label="Variables" id="env" activeTab={activeTab} onClick={setActiveTab} />
          <SidebarItem icon={Clock} label="Deployments" id="deployments" activeTab={activeTab} onClick={setActiveTab} />
          <SidebarItem icon={Settings} label="Settings" id="settings" activeTab={activeTab} onClick={setActiveTab} />

          <div className="text-xs font-bold text-zinc-400 uppercase tracking-wider px-4 mb-2 mt-6">Add-ons</div>
          <SidebarItem icon={Shield} label="Security" id="security" activeTab={activeTab} onClick={setActiveTab} />
        </nav>

        <div className="p-4 border-t border-zinc-200 text-xs text-zinc-500 flex justify-between items-center bg-white">
            <span>Status</span>
            <span className="flex items-center gap-1.5 text-emerald-600 font-bold bg-emerald-50 px-2 py-1 rounded-full border border-emerald-100">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" /> Active
            </span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex flex-col h-screen overflow-hidden bg-white">
        {/* Header */}
        <header className="h-16 border-b border-zinc-100 flex items-center justify-between px-8 bg-white z-10">
            <div className="flex items-center gap-4">
                <h2 className="font-bold text-xl text-zinc-800 tracking-tight">{activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}</h2>
            </div>
            <div className="flex gap-3">
                <button className="text-zinc-500 hover:text-zinc-900 px-3 py-2 text-sm font-medium transition-colors">
                    Documentation
                </button>
                <button className="bg-zinc-900 text-white px-4 py-2 rounded-lg text-sm font-bold hover:bg-zinc-800 transition-colors shadow-sm">
                    Visit App ↗
                </button>
            </div>
        </header>

        {/* Scrollable Area */}
        <main className="flex-1 overflow-y-auto p-8 bg-gray-50/50">
            <div className="max-w-6xl mx-auto">
                {children}
            </div>
        </main>
      </div>
    </div>
  );
};
