"""
Rate limiting for SMSLY Hosting deployment endpoints.
Prevents abuse and ensures fair usage of build resources.
"""
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class DeploymentRateThrottle(UserRateThrottle):
    """
    Limits deployments to 10 per hour per user.
    Prevents resource exhaustion from excessive builds.
    """
    scope = 'deployments'
    rate = '10/hour'


class BurstRateThrottle(UserRateThrottle):
    """
    Burst protection - limits to 3 deployments per minute.
    Prevents rapid-fire deployment attempts.
    """
    scope = 'deployment_burst'
    rate = '3/minute'


class AIChatRateThrottle(UserRateThrottle):
    rate = '30/minute'
    scope = 'ai_chat'


class AIAnalysisRateThrottle(UserRateThrottle):
    rate = '10/minute'
    scope = 'ai_analysis'


class NodeTokenExchangeThrottle(AnonRateThrottle):
    """Rate-limit the anonymous node-token-exchange endpoint.

    The endpoint is AllowAny (the caller doesn't have a token yet),
    so this throttle is keyed by client IP. It exists to slow down
    brute-force credential attacks that target the admin account.
    """
    scope = 'node_token_exchange'
    rate = '5/minute'


# ─── Auth / brute-force guards (Batch I) ─────────────────────────────
# SECURITY: prior to Batch I, login / password-reset / registration
# fell through to the global 'user: 1000000/hour' rate, which is
# effectively unlimited (≈278 req/sec per user). For login that
# defeats the entire point of the throttle — brute-force
# credential attacks. The four throttles below restore sane
# limits on auth-sensitive endpoints.

class LoginRateThrottle(AnonRateThrottle):
    """Brute-force guard on POST /api/v1/auth/login/.

    Keyed by client IP. At 10/min a single attacker can try at
    most 600 passwords per hour per IP, which is well below the
    rate at which most online services detect credential
    stuffing but high enough that a forgetful user isn't
    locked out by typing their password 5 times.
    """
    scope = 'login'
    rate = '10/minute'


class PasswordResetRateThrottle(AnonRateThrottle):
    """Email-bombing guard on password reset.

    Keyed by IP. 5/hour prevents an attacker from spamming
    reset emails to a victim (which itself is a denial-of-service
    attack against the victim's inbox). Per-user reset is
    additionally capped by the email channel provider.
    """
    scope = 'password_reset'
    rate = '5/hour'


class RegistrationRateThrottle(AnonRateThrottle):
    """Bot-account guard on POST /api/v1/auth/registration/.

    Keyed by IP. 5/hour matches the platform's billing-enforced
    account-creation cap. Operators who need bulk accounts
    should use the admin CLI.
    """
    scope = 'registration'
    rate = '5/hour'


# ─── Server-identity attestation (Batch I) ──────────────────────────
# The challenge endpoint is already throttled via
# node_token_exchange (5/min). The verify endpoint was not
# throttled — bounded work to fail an invalid signature is
# cheap, but unbounded attempts at forging signatures still
# consume CPU on the master.

class AttestationVerifyRateThrottle(AnonRateThrottle):
    """Throttle on POST /api/v1/internal/attest/verify/.

    The endpoint authenticates requests by HMAC signature; an
    invalid signature is rejected without doing the work
    downstream. But each failed attempt still costs the master
    a signature-verify CPU cycle. 30/min/IP is generous for
    legitimate retries and capped for brute-force forging
    attempts.
    """
    scope = 'attestation_verify'
    rate = '30/minute'


# ─── Database maintenance (Batch I) ─────────────────────────────────
# The maintenance actions on addons (``query``, ``vacuum``,
# ``rotate-credentials``) were uncapped (user 1M/hr). ``query``
# runs arbitrary SQL; ``vacuum`` locks the DB; ``rotate-credentials``
# invalidates secrets platform-wide. Each gets a dedicated scope
# with appropriate limits.

class DBQueryRateThrottle(UserRateThrottle):
    """Throttle on addon maintenance query endpoint.

    Arbitrary SQL against the shared DB is the highest-impact
    uncapped write on the platform. 30/min/user allows
    interactive debugging without enabling a DoS surface.
    """
    scope = 'db_query'
    rate = '30/minute'


class DBVacuumRateThrottle(UserRateThrottle):
    """Throttle on addon maintenance vacuum endpoint.

    VACUUM locks the addon DB while it runs. 1/hour is enough
    for routine maintenance.
    """
    scope = 'db_vacuum'
    rate = '1/hour'


class DBRotateCredentialsRateThrottle(UserRateThrottle):
    """Throttle on addon maintenance rotate-credentials endpoint.

    Rotation invalidates the addon's secrets across all
    services that depend on it. 1/hour bounds the blast radius
    of a misclick.
    """
    scope = 'db_rotate'
    rate = '1/hour'


# ─── SSH / remote-node operations (Batch I) ────────────────────────
# Server health checks and run_command run over SSH. Prior
# limits were 2/min which is too tight for an operator running
# ``docker ps; docker logs; df -h`` in sequence during an
# incident. Health checks were uncapped (1M/hr) which is
# generous for a per-node probe but unbounded for the
# ``check_all`` action that probes every node.

class ServerHealthCheckRateThrottle(UserRateThrottle):
    """Throttle on a single server's health_check.

    30/min/user is fine for "click Refresh on the servers page";
    a single SSH probe typically takes ~1-2s.
    """
    scope = 'server_health'
    rate = '30/minute'


class ServerRunCommandRateThrottle(UserRateThrottle):
    """Throttle on per-server run_command.

    5/min/user allows a handful of exec calls in quick
    succession (``docker ps; docker logs container; df -h``)
    without forcing the operator to wait a minute between
    each command. Previously 2/min which made incident
    response painful.
    """
    scope = 'server_run_command'
    rate = '5/minute'


# ─── Topology (Batch I) ────────────────────────────────────────────
# The /topology/ endpoint runs an N+1 query over services +
# addons + volumes + env_vars. Capping it at 30/min/user keeps
# the dashboard reactive without exposing the DB to a tight
# poll loop.

class TopologyListRateThrottle(UserRateThrottle):
    """Throttle on GET /api/v1/topology/.

    The endpoint joins services, addons, volumes, env_vars for
    every visible node. 30/min is enough for the auto-refresh
    loop on the topology page.
    """
    scope = 'topology_list'
    rate = '30/minute'
