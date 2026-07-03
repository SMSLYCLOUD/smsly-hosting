# `frontend/src/hooks/__tests__/` — Audit Log

This directory contains Vitest specs for every custom hook under
`frontend/src/hooks/`.

## Files in `frontend/src/hooks/`

| Hook                | Source file             | Tested? | Spec file                                  |
|---------------------|-------------------------|---------|--------------------------------------------|
| `usePermissions`    | `usePermissions.ts`     | Yes     | [`usePermissions.test.ts`](./usePermissions.test.ts) |
| `useTeam`           | `use-team.ts`           | Skipped | —                                          |
| `useGraphData`      | `useGraphData.ts`       | Skipped | —                                          |
| `useLiveData`       | `useLiveData.ts`        | Skipped | —                                          |

## What is covered

### `usePermissions` (`usePermissions.test.ts`)

The hook reads `AuthContext` from `@/components/auth-provider`. The spec
imports the real `AuthContext` and wraps `renderHook` in a small
`<AuthContext.Provider>` (or omits the wrapper to exercise the
`createContext` default). It does **not** invoke the real `<AuthProvider>`
because that component runs an effect that fetches `/auth/user/` on mount
— the test stubs `AuthProvider` to a passthrough.

Cases covered:

- No auth context → empty permissions, all flags `false`.
- Superuser (`is_superuser === true`) → `has`/`hasAny`/`hasAll` always
  return `true`.
- Regular user → `has` checks `permissions.includes(code)`; `hasAny` is
  `some`; `hasAll` is `every`; vacuous `hasAll()` is `true`,
  vacuous `hasAny()` is `false`.
- Team roles → `isTeamAdmin` is true iff any team has `role === "ADMIN"`.
- Org roles → `isOrgOwner` is true iff any org has `role === "OWNER"`.
- Memoization → the returned object reference is stable across rerenders
  when the underlying user is unchanged.
- `PERMISSION` constants → spot-check that
  `PERMISSION.SERVICE_CREATE === "service.create"` etc.

## What is intentionally skipped

The orchestrator's hard scope is "tests for `usePermissions` plus one
more hook, or an audit placeholder if none of the others are easy".
The other three hooks in this folder are data-fetching / side-effect
hooks with non-trivial dependencies:

- `useTeam` — calls `teamsApi.list()` from `@/lib/api`, mutates
  `localStorage`, and dispatches a `smsly:team-changed` `CustomEvent`.
  Realistic testing would require mocking `@/lib/api`, stubbing
  `localStorage`, and capturing dispatched events.
- `useGraphData` — calls `api.get('/topology/')` and the raw
  `fetch('/api/v1/topology/ecosystem/')` endpoint. Mocking the ecosystem
  endpoint shape and the topology merge logic is out of scope for this
  step.
- `useLiveData` — purely a polling/visibility hook around a callback.
  Would need fake timers and jsdom `visibilitychange` simulation.

All three are flagged for a follow-up task rather than crammed into this
spec, which would either need new dependencies (`@testing-library/user-event`
fake timers) or heavy mocking of `@/lib/api` that belongs in a separate
infra-level test suite.
