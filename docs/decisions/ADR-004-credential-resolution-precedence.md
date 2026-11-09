# ADR-004: Credential resolution precedence

**Status**: Proposed
**Date**: 2026-07-13

## Context

This package ships service clients (Slack, Elasticsearch, database, Snowflake) that run in two
very different places. The primary runtime is short-lived functions with an IAM role and no
secrets in the environment, where a named secret in a managed secret store (AWS Secrets Manager)
is the idiomatic credential source: rotatable, IAM-scoped, absent from the process listing. The
secondary runtime is local development, CLI tools, and the unit-test suite, where AWS may be
unreachable, no role is attached, and the caller already holds the credentials or has them in the
environment.

A client that reaches for Secrets Manager unconditionally imposes that store on both runtimes. It
forces every construction to have AWS reachable plus IAM permission plus a network round-trip,
even when the caller passed the credentials in directly, and it couples what should be a pure
object construction to AWS mocking in tests. Callers legitimately supply credentials three ways:
an explicit dict at construction, per-client environment variables, or (the default) a named
secret. The store cannot be the only path without breaking local dev, CLI, and test isolation.

The function-based helper historically undercut this. `get_elasticsearch_credentials`
(`secrets_helper.py`) called Secrets Manager first even when `ES_PASSWORD` was set, so CLI and
local-dev paths paid an avoidable AWS call. Commit `0d3f8e8` (PR #23) added the env-var check
ahead of the store lookup, aligning the helper with the precedence the clients already enforced.
The database and Slack helpers kept calling the store first until they were routed through the
clients' own env resolvers, and the Elasticsearch client ignored a secret's `host` field that the
helper honoured.

## Decision

Credentials resolve in a fixed order, first match wins, owned by `BaseClient._resolve_credentials`
in `base_client.py`:

1. **Explicit `credentials` dict** passed to the constructor. Returned as-is; nothing else is
   consulted. The caller owns these values and the package does not merge or second-guess them.
2. **Per-client environment variables**, via each client's `_resolve_credentials_from_env` override
   (Elasticsearch reads `ES_PASSWORD`/`ES_PASS` in `es_client.py`; database requires
   `DB_HOST` + `DB_PASSWORD` in `db_client.py`; Slack requires `SLACK_BOT_TOKEN` in
   `slack_client.py`). When the required vars are present, the resolved dict is returned without
   touching AWS.
3. **AWS Secrets Manager** as the default fallback, via `_fetch_credentials_from_sm` and
   `get_secret`, reading the named secret resolved from constructor arg, env var, or config.

A later tier fires only when every earlier tier is absent. An AWS call happens only when neither
an explicit dict nor env vars resolve, so local-dev, CLI, and test paths that supply explicit or
environment credentials make **zero** Secrets Manager calls. The direct function helpers mirror
the tail of this ladder for callers that bypass the client classes: `get_elasticsearch_credentials`,
`get_database_credentials` and `get_slack_credentials` each check the same env vars as their
client, then the store. Client hook and helper call the same per-service functions in
`secrets_helper.py` (an env resolver and a secret normaliser per service, with per-engine defaults
for MySQL and PostgreSQL), so the two paths cannot drift apart.

The Elasticsearch host follows its own order on both paths: the client's `host` argument, then
`ES_HOST` (or `ELASTICSEARCH_HOST`), then the `host` field of the resolved credentials (the
secret's, or an explicit dict's), then the configured default.

## Alternatives considered

- **Always call Secrets Manager (first, or unconditionally).** Simplest single code path, and the
  right default for the Lambda runtime taken alone. Rejected: it forces AWS reachability, IAM
  permission, and a network round-trip onto every construction in every runtime, including local
  dev, CLI, and unit tests that have no reason to touch AWS. Explicit and env credentials degrade
  to dead options or a bolted-on override, and the test suite has to mock AWS to build an object.
  This is the live rejected option: precedence exists specifically so the store is a fallback, not
  a precondition.
- **Environment variables only (strict 12-factor).** Clean for containers, but the primary runtime's
  idiomatic store is Secrets Manager (rotation, IAM scoping, secrets kept out of the env). Dropping
  the store would push plaintext secrets into every function's environment.
- **Explicit injection everywhere (no env, no store).** Maximally testable, but forces every
  entrypoint to hand-write secret retrieval, defeating the point of a shared client that just works
  under an attached role.
- **Per-client ad-hoc ordering.** Each client invents its own precedence. Rejected: callers cannot
  reason uniformly about where credentials come from, and the orders drift. One ladder lives in the
  base class; clients override only the env-detection and default-secret hooks.

## Consequences

- One predictable resolution order across every client. A new client implements
  `_resolve_credentials_from_env` and `_get_default_secret_name` and inherits the full ladder.
- Unit tests construct clients from an explicit dict with no AWS mock needed to do so; the explicit-
  and env-credential tests assert the store is not called (`get_secret_value.assert_not_called()` in
  `tests/test_secrets_helper.py`). Fallback-path tests still patch `get_secret` where they exercise the
  store tier.
- Local dev and CLI run against real services using environment variables with no AWS credentials
  present.
- Secrets Manager stays the zero-config default in the Lambda runtime: moving from local env creds
  to the store needs no code change, only unsetting the env vars.
- Explicit-over-remote is a deliberate trust choice. A caller passing a `credentials` dict is
  trusted to own those values in-process; the package neither validates them against the store nor
  falls back when they are wrong.

## Load-bearing premises (supersede triggers)

- Explicit dict and environment variables stay first-class, supported paths for local dev, CLI, and
  tests. If every runtime became Secrets-Manager-only (no consumer ever supplies explicit or env
  credentials), tiers 1 and 2 are dead weight and the precedence collapses to "always store".
- The caller owns any credentials it passes explicitly or sets in the environment. The
  explicit/env-beats-remote posture holds only while supplying credentials is an intentional act by
  a trusted in-process caller, not attacker-controlled input. If untrusted input could populate the
  `credentials` dict or the environment, the ordering needs re-evaluation.
- Secrets Manager remains the right default store for the primary runtime. A move to a different
  managed store changes tier 3's implementation but not the ladder's shape.
- Env-var **presence** is the trigger, not correctness. A required var being set means "use env"
  (`ES_PASSWORD`; `DB_HOST` + `DB_PASSWORD`; `SLACK_BOT_TOKEN`); a set-but-wrong value fails loudly
  rather than silently falling through to the store. If callers expected fallback-on-bad-env, the
  trigger semantics reopen.
