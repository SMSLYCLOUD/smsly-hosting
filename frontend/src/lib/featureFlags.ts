export type FeatureFlagKey =
  | 'replication'
  | 'vpnMesh'
  | 'tunnels'
  | 'transfers'
  | 'autoscaler'
  | 'backups'
  | 'functions';

const envEnabled = (value: string | undefined, fallback = false): boolean => {
  if (value == null) return fallback;
  const normalized = value.trim().toLowerCase();
  return normalized === '1' || normalized === 'true' || normalized === 'yes' || normalized === 'on';
};

export const featureFlags: Record<FeatureFlagKey, boolean> = {
  replication: envEnabled(process.env.NEXT_PUBLIC_ENABLE_REPLICATION, false),
  vpnMesh: envEnabled(process.env.NEXT_PUBLIC_ENABLE_VPN_MESH, false),
  tunnels: envEnabled(process.env.NEXT_PUBLIC_ENABLE_TUNNELS, false),
  transfers: envEnabled(process.env.NEXT_PUBLIC_ENABLE_TRANSFERS, false),
  autoscaler: envEnabled(process.env.NEXT_PUBLIC_ENABLE_AUTOSCALER, false),
  backups: envEnabled(process.env.NEXT_PUBLIC_ENABLE_BACKUPS, true),
  functions: envEnabled(process.env.NEXT_PUBLIC_ENABLE_FUNCTIONS, false),
};

export const featureDisabledReason: Record<FeatureFlagKey, string> = {
  replication: 'Replication is disabled until mesh + Patroni runtime checks pass.',
  vpnMesh: 'VPN Mesh is disabled until WireGuard handshake and route validation pass.',
  tunnels: 'Tunnels are disabled until FRP endpoint and health checks are verified.',
  transfers: 'Transfers are disabled until source/target validation is fully configured.',
  autoscaler: 'Autoscaler is disabled until scale actions are runtime-verified.',
  backups: 'Backups are disabled by environment configuration.',
  functions: 'Functions are disabled until runtime execution path is enabled.',
};
