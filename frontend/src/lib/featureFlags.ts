export const featureFlags = {
  autoscaler: true,
  replication: true,
  tunnels: true,
  vpnMesh: true,
  functions: true,
  transfers: true,
  grafana: true,
};

export const featureDisabledReason = {
  grafana: "Grafana is active.",
  autoscaler: "Autoscaler is active.",
  replication: "Replication is active.",
  tunnels: "Tunnels are active.",
  vpnMesh: "Mesh is active.",
  functions: "Functions are active.",
  transfers: "Server transfers are active.",
};
