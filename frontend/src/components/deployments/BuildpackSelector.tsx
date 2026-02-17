'use client';

import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Box, Code2, FileCode2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export type BuildpackType = 'NIXPACKS' | 'DOCKER' | 'STATIC';

interface BuildpackSelectorProps {
  value: BuildpackType;
  onChange: (value: BuildpackType) => void;
  className?: string;
}

export function BuildpackSelector({ value, onChange, className }: BuildpackSelectorProps) {
  return (
    <div className={cn("space-y-3", className)}>
      <Label>Build Strategy</Label>
      <RadioGroup
        value={value}
        onValueChange={(val) => onChange(val as BuildpackType)}
        className="grid grid-cols-1 md:grid-cols-3 gap-4"
      >
        <div>
          <RadioGroupItem value="NIXPACKS" id="nixpacks" className="peer sr-only" />
          <Label
            htmlFor="nixpacks"
            className="flex flex-col items-center justify-between rounded-md border-2 border-muted bg-popover p-4 hover:bg-accent hover:text-accent-foreground peer-data-[state=checked]:border-primary [&:has([data-state=checked])]:border-primary cursor-pointer h-full"
          >
            <Box className="mb-3 h-6 w-6" />
            <div className="text-center">
              <div className="font-semibold">Nixpacks</div>
              <div className="text-xs text-muted-foreground mt-1">
                Zero-config, auto-detects stack (Node, Python, Go...)
              </div>
            </div>
          </Label>
        </div>

        <div>
          <RadioGroupItem value="DOCKER" id="docker" className="peer sr-only" />
          <Label
            htmlFor="docker"
            className="flex flex-col items-center justify-between rounded-md border-2 border-muted bg-popover p-4 hover:bg-accent hover:text-accent-foreground peer-data-[state=checked]:border-primary [&:has([data-state=checked])]:border-primary cursor-pointer h-full"
          >
            <FileCode2 className="mb-3 h-6 w-6" />
            <div className="text-center">
              <div className="font-semibold">Dockerfile</div>
              <div className="text-xs text-muted-foreground mt-1">
                Use your own Dockerfile for full control
              </div>
            </div>
          </Label>
        </div>

        <div>
          <RadioGroupItem value="STATIC" id="static" className="peer sr-only" />
          <Label
            htmlFor="static"
            className="flex flex-col items-center justify-between rounded-md border-2 border-muted bg-popover p-4 hover:bg-accent hover:text-accent-foreground peer-data-[state=checked]:border-primary [&:has([data-state=checked])]:border-primary cursor-pointer h-full"
          >
            <Code2 className="mb-3 h-6 w-6" />
            <div className="text-center">
              <div className="font-semibold">Static Site</div>
              <div className="text-xs text-muted-foreground mt-1">
                HTML/CSS/JS or static generators (Hugo, Jekyll)
              </div>
            </div>
          </Label>
        </div>
      </RadioGroup>
    </div>
  );
}
