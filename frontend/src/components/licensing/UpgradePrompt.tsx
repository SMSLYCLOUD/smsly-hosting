'use client';

import React from 'react';
import Link from 'next/link';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useTier } from '@/context/TierContext';

interface Props {
  requiredTier: 'pro' | 'enterprise';
  title?: string;
  description?: string;
}

export function UpgradePrompt({ requiredTier, title, description }: Props) {
  const { isPro, isEnterprise } = useTier();

  const tierName = requiredTier.charAt(0).toUpperCase() + requiredTier.slice(1);
  const price = requiredTier === 'pro' ? '$29/mo' : '$99/mo';

  return (
    <Card className="border-2 border-dashed border-muted bg-muted/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          🔒 {title || `${tierName} Feature Locked`}
        </CardTitle>
        <CardDescription>
          {description || `This feature is available on the ${tierName} plan.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between p-4 bg-background rounded-lg border">
          <div>
            <p className="font-medium">Upgrade to {tierName}</p>
            <p className="text-sm text-muted-foreground">Unlock this feature and more.</p>
          </div>
          <div className="text-right">
             <span className="text-lg font-bold">{price}</span>
          </div>
        </div>
      </CardContent>
      <CardFooter>
        <Link href="/settings/billing" className="w-full">
            <Button className="w-full" variant="default">
              Upgrade Now
            </Button>
        </Link>
      </CardFooter>
    </Card>
  );
}
