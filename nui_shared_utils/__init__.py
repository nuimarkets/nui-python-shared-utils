"""
Enterprise-grade utilities for AWS Lambda functions with Slack, Elasticsearch, and monitoring integrations.

Public API is resolved lazily via PEP 562 ``__getattr__`` to keep package import
cheap. Submodules and their dependencies (boto3, slack-sdk, elasticsearch, etc.)
are only imported when an attribute is first accessed.
"""

import importlib
from typing import TYPE_CHECKING, Any, List

# Map public name -> (submodule, attr_name).
# Optional integrations: keep their entries here; on ImportError during lazy
# resolution we return None to preserve historical behaviour where consumers
# could check ``if nui.SlackClient is not None``.
_LAZY_EXPORTS = {
    # Configuration system
    "Config": ("config", "Config"),
    "get_config": ("config", "get_config"),
    "set_config": ("config", "set_config"),
    "configure": ("config", "configure"),
    "get_es_host": ("config", "get_es_host"),
    "get_es_credentials_secret": ("config", "get_es_credentials_secret"),
    "get_db_credentials_secret": ("config", "get_db_credentials_secret"),
    "get_slack_credentials_secret": ("config", "get_slack_credentials_secret"),
    # Secrets
    "get_secret": ("secrets_helper", "get_secret"),
    "get_database_credentials": ("secrets_helper", "get_database_credentials"),
    "get_elasticsearch_credentials": ("secrets_helper", "get_elasticsearch_credentials"),
    "get_slack_credentials": ("secrets_helper", "get_slack_credentials"),
    "get_api_key": ("secrets_helper", "get_api_key"),
    "clear_cache": ("secrets_helper", "clear_cache"),
    # Common utilities
    "resolve_config_value": ("utils", "resolve_config_value"),
    "resolve_aws_region": ("utils", "resolve_aws_region"),
    "create_aws_client": ("utils", "create_aws_client"),
    "handle_client_errors": ("utils", "handle_client_errors"),
    "merge_dimensions": ("utils", "merge_dimensions"),
    "validate_required_param": ("utils", "validate_required_param"),
    "safe_close_connection": ("utils", "safe_close_connection"),
    "format_log_context": ("utils", "format_log_context"),
    "DEFAULT_AWS_REGION": ("utils", "DEFAULT_AWS_REGION"),
    # Base client architecture
    "BaseClient": ("base_client", "BaseClient"),
    "ServiceHealthMixin": ("base_client", "ServiceHealthMixin"),
    "RetryableOperationMixin": ("base_client", "RetryableOperationMixin"),
    # Timezone helpers
    "nz_time": ("timezone", "nz_time"),
    "format_nz_time": ("timezone", "format_nz_time"),
    # Slack formatting (no external deps)
    "SlackBlockBuilder": ("slack_formatter", "SlackBlockBuilder"),
    "format_currency": ("slack_formatter", "format_currency"),
    "format_percentage": ("slack_formatter", "format_percentage"),
    "format_number": ("slack_formatter", "format_number"),
    "format_nz_time_slack": ("slack_formatter", "format_nz_time"),
    "format_date_range": ("slack_formatter", "format_date_range"),
    "format_daily_header": ("slack_formatter", "format_daily_header"),
    "format_weekly_header": ("slack_formatter", "format_weekly_header"),
    "format_error_alert": ("slack_formatter", "format_error_alert"),
    "SEVERITY_EMOJI": ("slack_formatter", "SEVERITY_EMOJI"),
    "STATUS_EMOJI": ("slack_formatter", "STATUS_EMOJI"),
    # Error handling
    "RetryableError": ("error_handler", "RetryableError"),
    "NonRetryableError": ("error_handler", "NonRetryableError"),
    "ErrorPatternMatcher": ("error_handler", "ErrorPatternMatcher"),
    "ErrorAggregator": ("error_handler", "ErrorAggregator"),
    "with_retry": ("error_handler", "with_retry"),
    "retry_on_network_error": ("error_handler", "retry_on_network_error"),
    "retry_on_db_error": ("error_handler", "retry_on_db_error"),
    "retry_on_es_error": ("error_handler", "retry_on_es_error"),
    "handle_lambda_error": ("error_handler", "handle_lambda_error"),
    "categorize_retryable_error": ("error_handler", "categorize_retryable_error"),
    # CloudWatch metrics
    "MetricsPublisher": ("cloudwatch_metrics", "MetricsPublisher"),
    "MetricAggregator": ("cloudwatch_metrics", "MetricAggregator"),
    "StandardMetrics": ("cloudwatch_metrics", "StandardMetrics"),
    "TimedMetric": ("cloudwatch_metrics", "TimedMetric"),
    "track_lambda_performance": ("cloudwatch_metrics", "track_lambda_performance"),
    "create_service_dimensions": ("cloudwatch_metrics", "create_service_dimensions"),
    "publish_health_metric": ("cloudwatch_metrics", "publish_health_metric"),
    # Log processing (no external deps)
    "extract_cloudwatch_logs_from_kinesis": ("log_processors", "extract_cloudwatch_logs_from_kinesis"),
    "derive_index_name": ("log_processors", "derive_index_name"),
    "CloudWatchLogEvent": ("log_processors", "CloudWatchLogEvent"),
    "CloudWatchLogsData": ("log_processors", "CloudWatchLogsData"),
    # Lambda context helpers
    "get_lambda_environment_info": ("lambda_helpers", "get_lambda_environment_info"),
    # Optional: Slack client (slack-sdk)
    "SlackClient": ("slack_client", "SlackClient"),
    "SentMessage": ("slack_client", "SentMessage"),
    # Optional: Elasticsearch client + query builder
    "ElasticsearchClient": ("es_client", "ElasticsearchClient"),
    "ESQueryBuilder": ("es_query_builder", "ESQueryBuilder"),
    "build_error_rate_query": ("es_query_builder", "build_error_rate_query"),
    "build_top_errors_query": ("es_query_builder", "build_top_errors_query"),
    "build_response_time_query": ("es_query_builder", "build_response_time_query"),
    "build_service_volume_query": ("es_query_builder", "build_service_volume_query"),
    "build_user_activity_query": ("es_query_builder", "build_user_activity_query"),
    "build_pattern_detection_query": ("es_query_builder", "build_pattern_detection_query"),
    "build_tender_participant_query": ("es_query_builder", "build_tender_participant_query"),
    # Optional: Database client (pymysql / psycopg2)
    "DatabaseClient": ("db_client", "DatabaseClient"),
    "PostgreSQLClient": ("db_client", "PostgreSQLClient"),
    "get_pool_stats": ("db_client", "get_pool_stats"),
    # Optional: AWS Powertools
    "get_powertools_logger": ("powertools_helpers", "get_powertools_logger"),
    "powertools_handler": ("powertools_helpers", "powertools_handler"),
    # Optional: Snowflake client adapter (snowflake-sql-api)
    "create_snowflake_client": ("snowflake_client", "create_snowflake_client"),
    "create_async_snowflake_client": ("snowflake_client", "create_async_snowflake_client"),
    "get_snowflake_credentials": ("snowflake_client", "get_snowflake_credentials"),
    "redacting_query_logger": ("snowflake_client", "redacting_query_logger"),
    # Optional: JWT validation (rsa)
    "validate_jwt": ("jwt_auth", "validate_jwt"),
    "require_auth": ("jwt_auth", "require_auth"),
    "check_auth": ("jwt_auth", "check_auth"),
    "get_jwt_public_key": ("jwt_auth", "get_jwt_public_key"),
    "JWTValidationError": ("jwt_auth", "JWTValidationError"),
    "AuthenticationError": ("jwt_auth", "AuthenticationError"),
    # Optional: Anthropic (Claude) LLM helper (anthropic[bedrock])
    "build_anthropic_client": ("llm", "build_anthropic_client"),
    "call_tool": ("llm", "call_tool"),
    "call_text": ("llm", "call_text"),
    # Structured logging contract (stdlib-only field registry + binders)
    "F": ("logging", "F"),
    "FIELDS": ("logging", "FIELDS"),
    "Field": ("logging", "Field"),
    "validate_fields": ("logging", "validate_fields"),
    "validate_registry": ("logging", "validate_registry"),
    "redaction_keys": ("logging", "redaction_keys"),
    "alias_map": ("logging", "alias_map"),
    "default_redactor": ("logging", "default_redactor"),
    "powertools_binder": ("logging", "powertools_binder"),
    "stdlib_binder": ("logging", "stdlib_binder"),
    "PowertoolsBinder": ("logging", "PowertoolsBinder"),
    "StdlibBinder": ("logging", "StdlibBinder"),
    "bind_request": ("logging", "bind_request"),
    "bind_user": ("logging", "bind_user"),
    "bind_build_context": ("logging", "bind_build_context"),
    "log_exception": ("logging", "log_exception"),
    "request_body_fields": ("logging", "request_body_fields"),
    "response_output_fields": ("logging", "response_output_fields"),
}

# Submodules that are optional integrations; ImportError during lazy load
# resolves to None instead of propagating, matching pre-1.4 behaviour.
# Includes ``slack_setup`` which is also handled by a special-case branch in
# ``__getattr__`` (it is exposed as a submodule object, not an attribute).
_OPTIONAL_SUBMODULES = {
    "slack_client",
    "es_client",
    "es_query_builder",
    "db_client",
    "powertools_helpers",
    "jwt_auth",
    "snowflake_client",
    "llm",
    "slack_setup",
}


def __getattr__(name: str) -> Any:
    # ``slack_setup`` is exposed as a submodule attribute (``nui.slack_setup``).
    if name == "slack_setup":
        try:
            mod = importlib.import_module(".slack_setup", __name__)
        except ImportError:
            if name in _OPTIONAL_SUBMODULES:
                mod = None
            else:
                raise
        globals()["slack_setup"] = mod
        return mod

    if name in _LAZY_EXPORTS:
        submod_name, attr = _LAZY_EXPORTS[name]
        try:
            submod = importlib.import_module(f".{submod_name}", __name__)
            value = getattr(submod, attr)
        except ImportError:
            if submod_name in _OPTIONAL_SUBMODULES:
                value = None
            else:
                raise
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | {"slack_setup"})


if TYPE_CHECKING:
    # Imported for type checkers / IDE completion only; not executed at runtime.
    from .config import (
        Config,
        configure,
        get_config,
        get_db_credentials_secret,
        get_es_credentials_secret,
        get_es_host,
        get_slack_credentials_secret,
        set_config,
    )
    from .secrets_helper import (
        clear_cache,
        get_api_key,
        get_database_credentials,
        get_elasticsearch_credentials,
        get_secret,
        get_slack_credentials,
    )
    from .utils import (
        DEFAULT_AWS_REGION,
        create_aws_client,
        format_log_context,
        handle_client_errors,
        merge_dimensions,
        resolve_aws_region,
        resolve_config_value,
        safe_close_connection,
        validate_required_param,
    )
    from .base_client import BaseClient, RetryableOperationMixin, ServiceHealthMixin
    from .timezone import format_nz_time, nz_time
    from .slack_formatter import (
        SEVERITY_EMOJI,
        STATUS_EMOJI,
        SlackBlockBuilder,
        format_currency,
        format_daily_header,
        format_date_range,
        format_error_alert,
        format_number,
        format_nz_time as format_nz_time_slack,
        format_percentage,
        format_weekly_header,
    )
    from .error_handler import (
        ErrorAggregator,
        ErrorPatternMatcher,
        NonRetryableError,
        RetryableError,
        categorize_retryable_error,
        handle_lambda_error,
        retry_on_db_error,
        retry_on_es_error,
        retry_on_network_error,
        with_retry,
    )
    from .cloudwatch_metrics import (
        MetricAggregator,
        MetricsPublisher,
        StandardMetrics,
        TimedMetric,
        create_service_dimensions,
        publish_health_metric,
        track_lambda_performance,
    )
    from .log_processors import (
        CloudWatchLogEvent,
        CloudWatchLogsData,
        derive_index_name,
        extract_cloudwatch_logs_from_kinesis,
    )
    from .lambda_helpers import get_lambda_environment_info
    from .slack_client import SentMessage, SlackClient
    from .es_client import ElasticsearchClient
    from .es_query_builder import (
        ESQueryBuilder,
        build_error_rate_query,
        build_pattern_detection_query,
        build_response_time_query,
        build_service_volume_query,
        build_tender_participant_query,
        build_top_errors_query,
        build_user_activity_query,
    )
    from .db_client import DatabaseClient, PostgreSQLClient, get_pool_stats
    from .snowflake_client import (
        create_async_snowflake_client,
        create_snowflake_client,
        get_snowflake_credentials,
        redacting_query_logger,
    )
    from .powertools_helpers import get_powertools_logger, powertools_handler
    from .jwt_auth import (
        AuthenticationError,
        JWTValidationError,
        check_auth,
        get_jwt_public_key,
        require_auth,
        validate_jwt,
    )
    from .llm import build_anthropic_client, call_text, call_tool
    from .logging import (
        FIELDS,
        F,
        Field,
        PowertoolsBinder,
        StdlibBinder,
        alias_map,
        bind_build_context,
        bind_request,
        bind_user,
        default_redactor,
        log_exception,
        powertools_binder,
        redaction_keys,
        request_body_fields,
        response_output_fields,
        stdlib_binder,
        validate_fields,
        validate_registry,
    )
    from . import slack_setup


__all__ = list(sorted(set(_LAZY_EXPORTS) | {"slack_setup"}))
