# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Add an opt-in `reraise_exceptions` parameter to `powertools_handler`. When enabled,
  handler failures are logged and alerted before the original exception is re-raised so
  stream and asynchronous event sources can apply their configured failure handling.
  The default remains the existing 500 response, while SQS events continue to re-raise
  unconditionally.
- The `[elasticsearch]` extra (and `all`) now allows `elasticsearch>=7.17.0,<10.0.0`, up from `<8.0.0`. Client majors 7.17, 8.x and 9.x are supported and tested, so the extra installs alongside a current client instead of failing to resolve. See [ADR-007](docs/decisions/ADR-007-elasticsearch-client-version-range.md).

### Fixed

- `ElasticsearchClient.search()` / `.aggregate()` now send `size` inside the request body instead of as a keyword alongside `body=`. On elasticsearch-py 8.x/9.x the previous form mutated the caller's body dict in place and raised `ValueError: Received multiple values for 'size'` when a body dict was reused for a second call.
- `ElasticsearchClient.get_indices_info()` returns a plain `list` on every client major (8.x/9.x wrap the JSON array in a response object).

### Added

- `tests/test_es_client_compat.py` - drives the real elasticsearch-py client through a stubbed transport, so a call shape a client major stops accepting fails in tests rather than at runtime. CI runs it against 7.17, 8.x and 9.x (`es-compat` job).

## [2.0.0] - 2026-07-13

### Removed

- **BREAKING**: the `nui_lambda_shared_utils` import shim. Import from `nui_shared_utils` instead (`from nui_shared_utils import ...`). The shim forwarded to `nui_shared_utils` with a `DeprecationWarning` since the 1.3.0 import-root rename and was always slated for removal in 2.0.0.
  - The old distribution name still installs: `pip install nui-lambda-shared-utils` continues to resolve via the redirect package (which depends on `nui-python-shared-utils`). Only the old import root is gone.

## [1.5.0] - 2026-06-29

### Added

- `nui_shared_utils.llm` - a generic Anthropic (Claude) client helper behind a new optional `[llm]` extra (`anthropic[bedrock]>=0.45.0,<1.0.0`). Plumbing only; prompts, tool schemas, and model ids stay in the consumer.
  - `build_anthropic_client(mode="api_key"|"bedrock", ...)` - API-key auth (explicit -> `ANTHROPIC_API_KEY` env -> Secrets Manager) or Bedrock IAM auth.
  - `call_tool(client, *, tool, prompt, model, max_tokens, system=None)` - forced tool-use call returning the named tool's `input` dict, or `None` on any model/parse failure (best-effort; never raises).
  - `call_text(client, *, prompt, model, max_tokens, system=None)` - text call returning `{text, input_tokens, output_tokens}`.
  - Lazy-loaded like the other optional integrations: `import nui_shared_utils` does not import `anthropic` (cold-start preserved), and the top-level exports resolve to `None` when the `[llm]` extra is not installed.
- `docs/guides/llm-integration.md` and a README usage example.

## [1.4.0] - 2026-05-06

### Performance

- Cold-start init for Lambda consumers reduced by ~400ms.
  - `boto3` and `botocore` are no longer imported at module load. They are deferred to first use inside `secrets_helper.get_secret`, `utils.create_aws_client`/`resolve_aws_region`, and `cloudwatch_metrics.MetricsPublisher` (now lazy-constructs its boto3 client on first metric publish).
  - The package `__init__` uses PEP 562 `__getattr__` so submodules (`slack_client`, `es_client`, `db_client`, `jwt_auth`, `powertools_helpers`, etc.) only load when their attributes are first accessed. `import nui_shared_utils` is ~10ms, down from ~700ms+.
- Public API is unchanged. Optional integrations still resolve to `None` when their backing dependency is not installed (matching pre-1.4 behaviour).

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

[Unreleased]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.2...HEAD
[1.3.2]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.1...1.3.2
[1.3.1]: https://github.com/nuimarkets/nui-python-shared-utils/compare/1.3.0...1.3.1
[1.3.0]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.6...1.3.0
[0.0.6]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.5...0.0.6
[0.0.5]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.4...0.0.5
[0.0.4]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.3...0.0.4
[0.0.3]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.2...0.0.3
[0.0.2]: https://github.com/nuimarkets/nui-python-shared-utils/compare/0.0.1...0.0.2
[0.0.1]: https://github.com/nuimarkets/nui-python-shared-utils/releases/tag/0.0.1
