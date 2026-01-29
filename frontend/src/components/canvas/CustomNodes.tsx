import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { Server, Database, Globe, Activity } from 'lucide-react';
import clsx from 'clsx';

const NodeCard = ({ data, icon: Icon, colorClass, status }: any) => {
  return (
    <div className={clsx(
      "w-64 rounded-2xl border bg-card text-card-foreground shadow-lg backdrop-blur-sm transition-all duration-300 hover:shadow-xl hover:scale-[1.02] group",
      data.selected ? "border-primary ring-2 ring-primary/20" : "border-border"
    )}>
      <Handle type="target" position={Position.Top} className="!bg-muted-foreground !w-3 !h-3 !border-background" />

      {/* Header */}
      <div className="p-4 border-b border-border/50 flex items-center justify-between bg-muted/30">
        <div className="flex items-center gap-3">
          <div className={clsx("p-2.5 rounded-xl shadow-inner", colorClass)}>
            <Icon size={18} className="text-white" />
          </div>
          <div>
            <h3 className="font-bold text-sm tracking-tight">{data.label}</h3>
            <p className="text-[10px] text-muted-foreground font-mono truncate w-32 uppercase tracking-wider">{data.subLabel}</p>
          </div>
        </div>
        <div className={clsx(
          "w-2.5 h-2.5 rounded-full shadow-lg",
          status === 'ACTIVE' ? "bg-emerald-500 shadow-emerald-500/50 animate-pulse" :
          status === 'FAILED' ? "bg-destructive shadow-destructive/50" : "bg-yellow-500"
        )} />
      </div>

      {/* Body */}
      <div className="p-4 space-y-3">
        <div className="flex justify-between items-center text-xs font-medium text-muted-foreground">
            <span>CPU</span>
            <div className="w-20 h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-blue-500 to-cyan-400 rounded-full" style={{ width: '45%' }} />
            </div>
        </div>
        <div className="flex justify-between items-center text-xs font-medium text-muted-foreground">
            <span>RAM</span>
            <div className="w-20 h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-purple-500 to-pink-400 rounded-full" style={{ width: '60%' }} />
            </div>
        </div>
      </div>

      <Handle type="source" position={Position.Bottom} className="!bg-muted-foreground !w-3 !h-3 !border-background" />
    </div>
  );
};

export const ServiceNode = memo(({ data }: any) => (
  <NodeCard data={data} icon={Globe} colorClass="bg-emerald-500" status={data.status} />
));

export const DatabaseNode = memo(({ data }: any) => (
  <NodeCard data={data} icon={Database} colorClass="bg-blue-500" status="ACTIVE" />
));

export const RedisNode = memo(({ data }: any) => (
  <NodeCard data={data} icon={Activity} colorClass="bg-red-500" status="ACTIVE" />
));
