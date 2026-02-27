'use client';
import { Badge } from "@/components/ui/badge";
import { useTier } from "@/context/TierContext";

export function TierBadge() {
  const { license } = useTier();
  const tier = license?.tier || 'community';

  const colors = {
    community: 'bg-blue-500 hover:bg-blue-600',
    pro: 'bg-purple-500 hover:bg-purple-600',
    enterprise: 'bg-amber-500 hover:bg-amber-600'
  };

  return (
    <Badge className={`${colors[tier]} text-white capitalize`}>
      {tier}
    </Badge>
  );
}
