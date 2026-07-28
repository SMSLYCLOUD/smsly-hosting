"""
Rate limiting for SMSLY Hosting deployment endpoints.
Prevents abuse and ensures fair usage of build resources.

SECURITY (Batch I cont 3): the class-level ``rate = 'X/Y'``
attribute was previously set on every throttle below. DRF's
``SimpleRateThrottle.__init__`` checks
``if not getattr(self, 'rate', None): self.rate = self.get_rate()``
because the class-level rate is truthy, the settings value
in ``settings.DEFAULT_THROTTLE_RATES`` was never read. Operators
were throttled at the OLD hard-coded rate (3/minute on
``BurstRateThrottle``) even after the settings file was bumped
to 5000/minute.

Fix: removed the class-level ``rate = 'X/Y'`` attributes below
so ``get_rate()`` is always called and reads the live settings
value. All other DRF defaults (cache backend, scope, ident
function) are unchanged.
"""
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class DeploymentRateThrottle(UserRateThrottle):
    """Long-tail throttle on deployment write actions.

    Rate is read from
    ``settings.DEFAULT_THROTTLE_RATES['deployments']`` at
    instantiation. Operators tune the rate in ``.env`` or
    ``config/settings.py`` without redeploying code.
    """
    scope = 'deployments'


class BurstRateThrottle(UserRateThrottle):
    """Burst-protection on deployment write actions.

    Rate is read from
    ``settings.DEFAULT_THROTTLE_RATES['deployment_burst']``
    at instantiation. Was previously hard-coded to 3/minute
    which made any create/deploy/verify/delete cycle 429
    after the third call within a minute. Far too tight for
    interactive work.
    """
    scope = 'deployment_burst'


class AIChatRateThrottle(UserRateThrottle):
    scope = 'ai_chat'


class AIAnalysisRateThrottle(UserRateThrottle):
    scope = 'ai_analysis'


class NodeTokenExchangeThrottle(AnonRateThrottle):
    """Rate-limit the anonymous node-token-exchange endpoint.

    The endpoint is AllowAny (the caller doesn't have a token
    yet), so this throttle is keyed by client IP. It exists
    to slow down brute-force credential attacks that target
    the admin account.

    Rate is read from
    ``settings.DEFAULT_THROTTLE_RATES['node_token_exchange']``.
    """
    scope = 'node_token_exchange'


# Auth / brute-force guards (Batch I)
# SECURITY: prior to Batch I, login / password-reset / registration
# fell through to the global 'user: 1000000/hour' rate, which
# is effectively unlimited (~278 req/sec). For login that
# defeats the entire point of the throttle (brute-force
# credential attacks). The four throttles below restore sane
# limits on auth-sensitive endpoints. Rate is read from
# settings.DEFAULT_THROTTLE_RATES at instantiation.

class LoginRateThrottle(AnonRateThrottle):
    """Brute-force guard on POST /api/v1/auth/login/.

    Keyed by client IP. The rate comes from
    ``settings.DEFAULT_THROTTLE_RATES['login']``.
    """
    scope = 'login'


class PasswordResetRateThrottle(AnonRateThrottle):
    """Email-bombing guard on password reset.

    Keyed by IP. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['password_reset']``.
    """
    scope = 'password_reset'


class RegistrationRateThrottle(AnonRateThrottle):
    """Bot-account guard on POST /api/v1/auth/registration/.

    Keyed by IP. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['registration']``.
    """
    scope = 'registration'


# Server-identity attestation (Batch I)
# The challenge endpoint is already throttled via
# node_token_exchange (5/min). The verify endpoint was not
# throttled. Bounded work to fail an invalid signature is
# cheap, but unbounded attempts at forging signatures still
# consume CPU on the master.

class AttestationVerifyRateThrottle(AnonRateThrottle):
    """Throttle on POST /api/v1/internal/attest/verify/.

    The endpoint authenticates requests by HMAC signature; an
    invalid signature is rejected without doing the work
    downstream. But each failed attempt still costs the
    master a signature-verify CPU cycle. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['attestation_verify']``.
    """
    scope = 'attestation_verify'


# Database maintenance (Batch I)
# The maintenance actions on addons (query, vacuum,
# rotate-credentials) were uncapped (user 1M/hr). query runs
# arbitrary SQL; vacuum locks the DB; rotate-credentials
# invalidates secrets platform-wide. Each gets a dedicated
# scope with appropriate limits. Rate from settings at
# instantiation.

class DBQueryRateThrottle(UserRateThrottle):
    """Throttle on addon maintenance query endpoint.

    Arbitrary SQL against the shared DB is the highest-impact
    uncapped write on the platform. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['db_query']``.
    """
    scope = 'db_query'


class DBVacuumRateThrottle(UserRateThrottle):
    """Throttle on addon maintenance vacuum endpoint.

    VACUUM locks the addon DB while it runs. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['db_vacuum']``.
    """
    scope = 'db_vacuum'


class DBRotateCredentialsRateThrottle(UserRateThrottle):
    """Throttle on addon maintenance rotate-credentials endpoint.

    Rotation invalidates the addon's secrets across all
    services that depend on it. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['db_rotate']``.
    """
    scope = 'db_rotate'


# SSH / remote-node operations (Batch I)
# Server health checks and run_command run over SSH. Rate from
# settings at instantiation.

class ServerHealthCheckRateThrottle(UserRateThrottle):
    """Throttle on a single server's health_check.

    Each call is a remote SSH probe. Rate from
    ``settings.DEFAULT_THROTTLE_RATES['server_health']``.
    """
    scope = 'server_health'


# Re-export: canonical definition lives in deployments/views/server/serializers.py
from apps.deployments.views.server.serializers import ServerCommandThrottle as ServerRunCommandRateThrottle


# Topology (Batch I)
# The /topology/ endpoint runs an N+1 query over services,
# addons, volumes, and env_vars. Rate from settings at
# instantiation.

class TopologyListRateThrottle(UserRateThrottle):
    """Throttle on GET /api/v1/topology/.

    Rate from
    ``settings.DEFAULT_THROTTLE_RATES['topology_list']``.
    """
    scope = 'topology_list'


# Cron (Batch I cont 3 / Issue 137)
# The cron-jobs viewset has no built-in rate limit. A user could
# create a cron job every request, polluting the scheduler and
# (combined with #136) exhausting the schedule space. Per-user
# throttle from settings at instantiation.

class CronJobCreateRateThrottle(UserRateThrottle):
    """Throttle on POST /api/v1/services/<id>/cron/.

    Rate from
    ``settings.DEFAULT_THROTTLE_RATES['cron_jobs_create']``.
    """
    scope = 'cron_jobs_create'


class AddonDeleteRateThrottle(UserRateThrottle):
    """Throttle on DELETE /api/v1/addons/{id}/.

    Rate from
    ``settings.DEFAULT_THROTTLE_RATES['addon_delete']``.
    """
    scope = 'addon_delete'


class TwoFactorLoginRateThrottle(AnonRateThrottle):
    """Brute-force guard on POST /api/v1/2fa/login/.

    Keyed by client IP. The rate comes from
    ``settings.DEFAULT_THROTTLE_RATES['two_factor_login']``.
    """
    scope = 'two_factor_login'


class TokenCreateRateThrottle(UserRateThrottle):
    """Throttle on POST /api/v1/tokens/create/.

    Rate from
    ``settings.DEFAULT_THROTTLE_RATES['token_create']``.
    """
    scope = 'token_create'
