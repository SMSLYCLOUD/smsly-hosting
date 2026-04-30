export const featureFlags = {
  autoscaler: true,
  replication: true,
  tunnels: true,
  vpnMesh: true,
  functions: true,
  transfers: true,
};

export const featureDisabledReason = {
  autoscaler: "Autoscaling is currently in beta and only available for enterprise plans.",
  replication: "Database replication is being optimized and will return soon.",
  tunnels: "Secure tunneling is temporarily unavailable during maintenance.",
  vpnMesh: "VPN Mesh networking requires a dedicated infrastructure node.",
  functions: "Serverless functions are in early access.",
  transfers: "Server transfers are undergoing security audits.",
};
