import React from 'react';

interface Props {
    tier: 'pro' | 'enterprise';
    children: React.ReactNode;
    fallback?: React.ReactNode;
    showPrompt?: boolean;
}

export function RequiresTier({ tier, children, fallback, showPrompt = true }: Props) {
    // ALWAYS RENDER CHILDREN (Self-hosted unlock)
    return <>{children}</>;
}
