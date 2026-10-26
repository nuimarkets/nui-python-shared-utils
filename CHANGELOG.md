# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and breaking changes are called out explicitly below, including historical minor releases
that contained them.

## [Unreleased]

### Changed

- **BREAKING**: require Python 3.10 or newer in both the main package and the legacy
  redirect package. Python 3.9 is no longer supported or included in CI test matrices.
- Powertools docstring and guide examples name `nui-tender-operations`, the repo formerly
  `nui-tender-analyser`. Docs only, no behaviour change.

### Fixed

- Use canonical IANA names `America/New_York` and `America/Los_Angeles` for US timezone
  constants and option labels, so importing timezone utilities and Slack formatting works
  on systems without legacy timezone links.

## [2.4] - 2026-10-20

### Added

- Add an opt-in `reraise_exceptions` parameter to `powertools_handler`. When enabled,
  handler failures are logged and alerted before the original exception is re-raised so
  stream and asynchronous event sources can apply their configured failure handling.
  The default remains the existing 500 response, while SQS events continue to re-raise
  unconditionally.

## [2.3] - 2026-10-12

### Fixed

- `powertools_handler` re-raises exceptions for SQS event-source mappings after logging and
  alerting, so failed batches are redelivered and can reach a configured DLQ instead of
  being acknowledged by the wrapper's 500 response.

## [2.2] - 2026-09-14

### Added

- Add response-returning Slack write methods (`post_message`, `post_thread_reply`,
  `post_update`, and `post_file`) for callers that need message timestamps, channel IDs,
  or the raw upload response. The existing bool-returning `send_*` methods retain their
  error-swallowing behavior.
- Add `SentMessage` as the result type for posted messages and thread replies.

### Changed

- `send_file` and `post_file` accept bytes and can upload into a thread with `thread_ts`.

## [2.1] - 2026-08-03

### Changed

- **BREAKING**: replace `pytz` with the standard-library `zoneinfo` API and remove `pytz`
  from the base dependencies. Code importing timezone constants must replace `.localize()`
  with `tzinfo=...`, passing `fold=1` for repeated wall times where the old `is_dst=False`
  default mattered, and replace `.zone` with `.key`. Declare `pytz` directly if application
  code still imports it. The `[timezone]` extra supplies `tzdata` for minimal images without
  a system IANA database.
- Allow Elasticsearch client majors 7.17, 8.x, and 9.x through the `[elasticsearch]` and
  `all` extras. See
  [ADR-007](docs/decisions/ADR-007-elasticsearch-client-version-range.md).

### Fixed

- Send Elasticsearch search `size` inside a copied request body, avoiding caller-dict
  mutation and duplicate-value errors on client majors 8.x and 9.x.
- Return a plain `list` from `ElasticsearchClient.get_indices_info()` on every supported
  Elasticsearch client major.
- Make no-argument `slack_formatter.format_nz_time()` independent of the host timezone.

### Added

- Add an Elasticsearch compatibility test matrix for client majors 7.17, 8.x, and 9.x.
- Add ADRs covering Elasticsearch version support and the move to `zoneinfo`.

## [2.0] - 2026-07-13

### Removed

- **BREAKING**: the `nui_lambda_shared_utils` import shim. Import from `nui_shared_utils` instead (`from nui_shared_utils import ...`). The shim forwarded to `nui_shared_utils` with a `DeprecationWarning` since the 1.3.1 import-root rename and was always slated for removal in 2.0.0.
  - The old distribution name still installs: `pip install nui-lambda-shared-utils` continues to resolve via the redirect package (which depends on `nui-python-shared-utils`). Only the old import root is gone.

### Added

- Add ADRs for the package's generic scope, lazy integrations, credential precedence,
  explicit Secrets Manager region resolution, and Snowflake SQL API choice.

## [1.6.0] - 2026-07-13

### Added

- Add a backend-neutral structured logging field registry, request-scoped Powertools and
  stdlib binders, binding helpers, field validation, and recursive payload redaction.
- Add the structured logging contract guide and ADR-001.

## [1.5.0] - 2026-07-02

### Added

- Add optional `unfurl_links` and `unfurl_media` controls to `SlackClient.send_message`.
  Unset values preserve Slack's existing defaults.

## [1.4.1] - 2026-06-29

### Fixed

- Replace the Snowflake extra's direct Git dependency with the published
  `snowflake-sql-api>=0.1.1,<0.2.0` range so PyPI accepts the package metadata.

## [1.4.0] - 2026-06-29

### Added

- Add the pure-Python Snowflake SQL API client behind the optional `[snowflake]` extra,
  including sync and async factories, key-pair credential resolution, query logging, and
  offline tests.
- Add a generic Anthropic client helper behind the optional `[llm]` extra, with API-key and
  Bedrock authentication, forced tool-use calls, and text calls.
- Add Snowflake and LLM integration guides.

### Changed

- **BREAKING**: resolve the Secrets Manager region from the boto3 session, `AWS_REGION`, or
  `AWS_DEFAULT_REGION`, and fail explicitly instead of silently defaulting to
  `ap-southeast-2`.

## [1.3.3] - 2026-05-06

### Performance

- Cold-start init for Lambda consumers reduced by ~400ms.
  - `boto3` and `botocore` are no longer imported at module load. They are deferred to first use inside `secrets_helper.get_secret`, `utils.create_aws_client`/`resolve_aws_region`, and `cloudwatch_metrics.MetricsPublisher` (now lazy-constructs its boto3 client on first metric publish).
  - The package `__init__` uses PEP 562 `__getattr__` so submodules (`slack_client`, `es_client`, `db_client`, `jwt_auth`, `powertools_helpers`, etc.) only load when their attributes are first accessed. `import nui_shared_utils` is ~10ms, down from ~700ms+.
- Public API is unchanged. Optional integrations still resolve to `None` when their backing dependency is not installed (matching the previous behaviour).

### Added

- `scripts/bench_imports.py` for measuring `python -X importtime` cumulative cost across common entry points.
- `tests/test_lazy_imports.py` regression tests (boto3 must not appear in `sys.modules` after a bare package import or after importing `jwt_auth`/`powertools_helpers`).

## [1.3.2] - 2026-04-10

### Fixed

- `get_elasticsearch_credentials()` now checks env vars (`ES_PASSWORD`/`ES_PASS`) before Secrets Manager, aligning with `BaseServiceClient` credential precedence
- `ESClient._resolve_credentials_from_env()` accepts both `ES_PASSWORD`/`ES_USERNAME` and `ES_PASS`/`ES_USER` conventions

## [1.3.1] - 2026-04-05

### Changed

- Renamed internal import from `nui_lambda_shared_utils` to `nui_shared_utils`

## [1.3.0] - 2026-04-05

### Changed

- Renamed PyPI package from `nui-lambda-shared-utils` to `nui-python-shared-utils`
- Added explicit license field for twine 6.x compatibility
- Updated CI publish workflow for new package name and tag pattern

## [1.0.0 - 1.2.3] - 2025-11 to 2026-03

### Added

- **Log Processors Module** - `log_processors` for Kinesis/CloudWatch log extraction
- **AWS Powertools Integration** - `powertools_helpers` module with `get_powertools_logger()` and `powertools_handler()`
- **JWT Authentication** - RS256 token validation for API Gateway Lambdas
- **Slack Block Kit improvements** - Enhanced block builder patterns
- **`slack-channel-setup` CLI tool** for Slack workspace automation
- `service_name` parameter to `SlackClient` for custom display names
- `[powertools]` optional dependency group
- Streaming bulk operations for Elasticsearch
- Environment-based credential resolution in `BaseServiceClient`
- Pass-through account names support

### Changed

- Rebranded as generic production-ready package (not NUI-specific)
- Improved Slack Lambda header formatting (cleaner 2-line layout)
- Documentation reorganization and expansion

### Breaking Changes

- Removed `SERVICE_EMOJI` from public API exports
- Removed `get_entity_stats()` and `get_record_stats()` from `DatabaseClient`
- `format_currency()` now requires explicit currency parameter

## [0.0.6] - 2025-11-16

### Added

- Configuration YAML support for account info
- RabbitMQ integration utilities

### Changed

- Enhanced configuration system

## [0.0.5] - 2025-09-29

### Added

- Comprehensive test improvements
- Better test coverage across modules

### Fixed

- Various test fixes and improvements

## [0.0.4] - 2025-09-05

### Changed

- Minor improvements and fixes

## [0.0.3] - 2025-09-05

### Changed

- Initial improvements

## [0.0.2] - 2025-09-05

### Added

- Initial package structure
- Core client implementations (Slack, Elasticsearch, Database)
- Metrics publishing utilities
- Error handling decorators
- Timezone utilities for NZ timezone handling

## [0.0.1] - 2025-09-05

### Added

- Initial release
- Basic project structure
- Python package configuration

[Unreleased]: https://github.com/nuimarkets/nui-python-shared-utils/compare/2.4...HEAD
[2.4]: https://github.com/nuimarkets/nui-python-shared-utils/compare/2.3...2.4
[2.3]: https://github.com/nuimarkets/nui-python-shared-utils/compare/2.2...2.3
[2.2]: https://github.com/nuimarkets/nui-python-shared-utils/compare/2.1...2.2
[2.1]: https://github.com/nuimarkets/nui-python-shared-utils/compare/2.0...2.1
[2.0]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.6.0...2.0
[1.6.0]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.5.0...1.6.0
[1.5.0]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.4.1...1.5.0
[1.4.1]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.4.0...1.4.1
[1.4.0]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.3...1.4.0
[1.3.3]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.2...1.3.3
[1.3.2]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.1...1.3.2
[1.3.1]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.0...1.3.1
[1.3.0]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.6...1.3.0
[0.0.6]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.5...0.0.6
[0.0.5]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.4...0.0.5
[0.0.4]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.3...0.0.4
[0.0.3]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.2...0.0.3
[0.0.2]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.1...0.0.2
[0.0.1]: https://github.com/nuimarkets/nui-python-shared-utils/releases/tag/0.0.1
