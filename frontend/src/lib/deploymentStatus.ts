/**
 * Backend-parity status sets.
 *
 * Mirrors backend/apps/deployments/models/deployment.py (Status) and
 * models/service.py (Status). Import these instead of scattering string
 * literals: invented statuses (SUCCESS / PENDING / RUNNING) silently
 * evaluate to false and hide live pipelines, and lowercase compares
 * ('building', 'deletion_failed') never match the uppercase wire values.
 */

/** Pipeline still moving: not terminal, outcome unknown. */
export const DEPLOYMENT_IN_PROGRESS = [
  'QUEUED',
  'REVIEW',
  'BUILDING',
  'AWAITING_APPROVAL',
  'BACKUP_RUNNING',
  'MIGRATION_PLANNING',
  'MIGRATION_RUNNING',
  'DEPLOYING',
  'HEALTH_CHECK',
  'STAGED',
  'ROLLING_BACK',
] as const;

/** Pipeline over with a bad outcome. */
export const DEPLOYMENT_FAILED_STATES = [
  'BUILD_FAILED',
  'BACKUP_FAILED',
  'MIGRATION_FAILED',
  'HEALTH_CHECK_FAILED',
  'FAILED',
] as const;

/** Pipeline over, terminally (success, failure, or stopped). */
export const DEPLOYMENT_TERMINAL_STATES = [
  'ACTIVE',
  ...DEPLOYMENT_FAILED_STATES,
  'CANCELLED',
  'INACTIVE',
  'ROLLED_BACK',
] as const;

export function isDeploymentInProgress(status?: string | null): boolean {
  return !!status && (DEPLOYMENT_IN_PROGRESS as readonly string[]).includes(status);
}

export function isDeploymentFailed(status?: string | null): boolean {
  return !!status && (DEPLOYMENT_FAILED_STATES as readonly string[]).includes(status);
}

export function isDeploymentTerminal(status?: string | null): boolean {
  return !!status && (DEPLOYMENT_TERMINAL_STATES as readonly string[]).includes(status);
}

export function isDeploymentLive(status?: string | null): boolean {
  return status === 'ACTIVE';
}

/** Service wire statuses (backend Service.Status). */
export const SERVICE_RUNNING = 'ACTIVE';
export const SERVICE_FAILED = 'DELETION_FAILED';
/** Not serving: deleted, unknown, or mid-deletion. */
export const SERVICE_STOPPED_STATES = ['DELETED', 'UNKNOWN', 'DELETION_PENDING'] as const;

export function isServiceRunning(status?: string | null): boolean {
  return status === SERVICE_RUNNING;
}

export function isServiceFailed(status?: string | null): boolean {
  return status === SERVICE_FAILED;
}

export function isServiceStopped(status?: string | null): boolean {
  return !!status && (SERVICE_STOPPED_STATES as readonly string[]).includes(status);
}
