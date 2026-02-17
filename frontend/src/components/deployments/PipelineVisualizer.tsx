'use client';

import React from 'react';
import { motion } from 'framer-motion';
import { CheckCircle2, Circle, Loader2, XCircle, Clock } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface PipelineStage {
  name: string;
  status: string; // 'pending', 'running', 'success', 'failed'
  duration?: number;
}

interface PipelineVisualizerProps {
  stages: PipelineStage[];
  className?: string;
}

export function PipelineVisualizer({ stages, className }: PipelineVisualizerProps) {
  if (!stages || stages.length === 0) return null;

  return (
    <div className={cn("w-full py-6 overflow-x-auto", className)}>
      <div className="flex items-center justify-between min-w-[500px]">
        {stages.map((stage, index) => {
          const isLast = index === stages.length - 1;
          const isPending = stage.status === 'pending';
          const isRunning = stage.status === 'running';
          const isSuccess = stage.status === 'success';
          const isFailed = stage.status === 'failed';

          return (
            <div key={stage.name} className="flex-1 flex items-center">
              {/* Step Circle */}
              <div className="relative flex flex-col items-center gap-2">
                <motion.div
                  initial={{ scale: 0.8, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ duration: 0.3 }}
                  className={cn(
                    "w-8 h-8 rounded-full flex items-center justify-center border-2 z-10 bg-background",
                    isPending && "border-muted text-muted-foreground",
                    isRunning && "border-blue-500 text-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.5)]",
                    isSuccess && "border-emerald-500 bg-emerald-500 text-white border-transparent",
                    isFailed && "border-red-500 bg-red-500 text-white border-transparent"
                  )}
                >
                  {isPending && <Circle className="w-4 h-4" />}
                  {isRunning && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isSuccess && <CheckCircle2 className="w-5 h-5" />}
                  {isFailed && <XCircle className="w-5 h-5" />}
                </motion.div>

                {/* Label */}
                <div className="absolute top-10 flex flex-col items-center w-32">
                  <span className={cn(
                    "text-xs font-semibold uppercase tracking-wider",
                    isPending && "text-muted-foreground",
                    isRunning && "text-blue-400",
                    isSuccess && "text-emerald-400",
                    isFailed && "text-red-400"
                  )}>
                    {stage.name}
                  </span>
                  {stage.duration !== undefined && stage.duration > 0 && (
                    <span className="text-[10px] text-muted-foreground flex items-center gap-1 mt-0.5">
                      <Clock className="w-3 h-3" />
                      {stage.duration < 1 ? "<1s" : `${stage.duration.toFixed(1)}s`}
                    </span>
                  )}
                </div>
              </div>

              {/* Connector Line */}
              {!isLast && (
                <div className="flex-1 h-[2px] bg-muted mx-2 relative">
                  <motion.div
                    className={cn(
                      "absolute inset-0 h-full origin-left",
                      isSuccess ? "bg-emerald-500" : isRunning ? "bg-blue-500" : isFailed ? "bg-red-500" : "bg-transparent"
                    )}
                    initial={{ scaleX: 0 }}
                    animate={{ scaleX: isSuccess ? 1 : 0 }}
                    transition={{ duration: 0.5 }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
