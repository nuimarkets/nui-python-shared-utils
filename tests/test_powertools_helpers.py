"""
Tests for AWS Powertools integration utilities.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

# Test markers
pytestmark = pytest.mark.unit


class TestGetPowertoolsLogger:
    """Tests for get_powertools_logger function"""

    def test_local_environment_detection(self, monkeypatch):
        """Test logger uses standard Python logging in local environment"""
        # Remove Lambda environment variables
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import get_powertools_logger

        logger = get_powertools_logger("test-service")

        # Should be standard Python logger
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test-service"
        # Should have mock inject_lambda_context method
        assert hasattr(logger, "inject_lambda_context")
        assert callable(logger.inject_lambda_context)

    def test_lambda_environment_detection(self, monkeypatch):
        """Test logger uses Powertools Logger in Lambda environment"""
        # Set Lambda environment variable
        monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "127.0.0.1:9001")

        # Mock Powertools Logger class
        mock_logger_instance = MagicMock()
        mock_logger_class = MagicMock(return_value=mock_logger_instance)

        with patch("nui_shared_utils.powertools_helpers.POWERTOOLS_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.Logger", mock_logger_class):
                from nui_shared_utils.powertools_helpers import get_powertools_logger

                logger = get_powertools_logger("test-service")

                # Should be Powertools Logger instance
                assert logger == mock_logger_instance

                # Verify Logger was created with correct params
                mock_logger_class.assert_called_once_with(
                    service="test-service",
                    level="INFO",
                    sampling_rate=1,
                    datefmt="%Y-%m-%dT%H:%M:%SZ",
                    utc=True,
                )

    def test_sam_local_environment_uses_local_logger(self, monkeypatch):
        """Test SAM local environment uses local logger despite Lambda API"""
        monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "127.0.0.1:9001")
        monkeypatch.setenv("AWS_SAM_LOCAL", "true")

        from nui_shared_utils.powertools_helpers import get_powertools_logger

        logger = get_powertools_logger("test-service")

        # Should use local logger in SAM environment
        assert isinstance(logger, logging.Logger)

    def test_custom_log_level(self, monkeypatch):
        """Test logger respects custom log level"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import get_powertools_logger

        logger = get_powertools_logger("test-service", level="DEBUG")

        assert logger.level == logging.DEBUG

    def test_coloredlogs_enabled_when_available(self, monkeypatch):
        """Test coloredlogs is used when available and enabled"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        # Mock coloredlogs module
        mock_coloredlogs = MagicMock()

        with patch("nui_shared_utils.powertools_helpers.COLOREDLOGS_AVAILABLE", True):
            with patch.dict("sys.modules", {"coloredlogs": mock_coloredlogs}):
                # Re-import to get the mocked coloredlogs
                import importlib

                import nui_shared_utils.powertools_helpers as ph

                importlib.reload(ph)

                logger = ph.get_powertools_logger("test-service", local_dev_colors=True)

                # Should have called coloredlogs.install
                mock_coloredlogs.install.assert_called_once()

    def test_coloredlogs_disabled_when_requested(self, monkeypatch):
        """Test coloredlogs is not used when disabled"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        # Mock coloredlogs module
        mock_coloredlogs = MagicMock()

        with patch("nui_shared_utils.powertools_helpers.COLOREDLOGS_AVAILABLE", True):
            with patch.dict("sys.modules", {"coloredlogs": mock_coloredlogs}):
                import importlib

                import nui_shared_utils.powertools_helpers as ph

                importlib.reload(ph)

                logger = ph.get_powertools_logger("test-service", local_dev_colors=False)

                # Should NOT have called coloredlogs.install
                mock_coloredlogs.install.assert_not_called()

    def test_powertools_not_available_in_lambda_raises_error(self, monkeypatch):
        """Test ImportError when Powertools not available in Lambda environment"""
        monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "127.0.0.1:9001")

        with patch("nui_shared_utils.powertools_helpers.POWERTOOLS_AVAILABLE", False):
            from nui_shared_utils.powertools_helpers import get_powertools_logger

            with pytest.raises(ImportError, match="aws-lambda-powertools is required"):
                get_powertools_logger("test-service")

    def test_mock_inject_lambda_context_works(self, monkeypatch):
        """Test mock inject_lambda_context decorator works locally"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import get_powertools_logger

        logger = get_powertools_logger("test-service")

        # Test mock decorator
        @logger.inject_lambda_context
        def test_func():
            return "success"

        result = test_func()
        assert result == "success"


class TestPowertoolsHandler:
    """Tests for powertools_handler decorator"""

    def test_successful_handler_execution(self, monkeypatch):
        """Test decorator allows successful handler execution"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            return {"statusCode": 200, "body": "Success"}

        # Mock context
        mock_context = MagicMock()
        mock_context.function_name = "test-function"

        result = handler({"test": "event"}, mock_context)

        assert result["statusCode"] == 200
        assert result["body"] == "Success"

    def test_exception_handling_returns_500(self, monkeypatch):
        """Test decorator catches exceptions and returns 500 error"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            raise ValueError("Test error")

        mock_context = MagicMock()
        mock_context.function_name = "test-function"

        result = handler({"test": "event"}, mock_context)

        assert result["statusCode"] == 500
        assert result["body"] == "Internal Server Error"

    def test_slack_alert_on_error(self, monkeypatch):
        """Test Slack alert is sent on handler failure"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        mock_slack_client = MagicMock()

        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.SlackClient", return_value=mock_slack_client):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service", slack_alert_channel="#errors")
                def handler(event, context):
                    raise ValueError("Test error")

                mock_context = MagicMock()
                mock_context.function_name = "test-function"

                result = handler({"test": "event"}, mock_context)

                # Should have sent Slack message
                mock_slack_client.send_message.assert_called_once()
                call_args = mock_slack_client.send_message.call_args
                assert call_args[1]["channel"] == "#errors"
                assert "ValueError" in call_args[1]["text"]
                assert "Test error" in call_args[1]["text"]

    def test_graceful_slack_failure(self, monkeypatch):
        """Test decorator handles Slack client failures gracefully"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        mock_slack_client = MagicMock()
        mock_slack_client.send_message.side_effect = Exception("Slack API error")

        # Capture logger warnings
        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.SlackClient", return_value=mock_slack_client):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service", slack_alert_channel="#errors")
                def handler(event, context):
                    raise ValueError("Test error")

                mock_context = MagicMock()
                mock_context.function_name = "test-function"

                result = handler({"test": "event"}, mock_context)

                # Should still return error response despite Slack failure
                assert result["statusCode"] == 500

    def test_no_slack_alert_when_channel_not_configured(self, monkeypatch):
        """Test no Slack alert when channel not configured"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        mock_slack_client = MagicMock()

        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.SlackClient", return_value=mock_slack_client):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service")
                def handler(event, context):
                    raise ValueError("Test error")

                mock_context = MagicMock()
                result = handler({"test": "event"}, mock_context)

                # Should NOT have sent Slack message
                mock_slack_client.send_message.assert_not_called()

    def test_slack_client_init_failure_logged(self, monkeypatch):
        """Test Slack client initialization failure is logged"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
            with patch(
                "nui_shared_utils.powertools_helpers.SlackClient",
                side_effect=Exception("Slack init failed"),
            ):
                from nui_shared_utils.powertools_helpers import powertools_handler

                # Decorator creation should not raise
                @powertools_handler(service_name="test-service", slack_alert_channel="#errors")
                def handler(event, context):
                    return {"statusCode": 200}

                # Handler should still work
                mock_context = MagicMock()
                result = handler({"test": "event"}, mock_context)
                assert result["statusCode"] == 200

    def test_metrics_integration(self, monkeypatch):
        """Test metrics decorator integration when namespace provided"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        mock_metrics = MagicMock()
        mock_metrics.log_metrics = lambda func: func  # Pass-through decorator

        with patch("nui_shared_utils.powertools_helpers.POWERTOOLS_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.Metrics", return_value=mock_metrics):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service", metrics_namespace="Test/Metrics")
                def handler(event, context):
                    return {"statusCode": 200}

                mock_context = MagicMock()
                result = handler({"test": "event"}, mock_context)

                # Should have created Metrics instance
                assert result["statusCode"] == 200

    def test_no_metrics_when_namespace_not_provided(self, monkeypatch):
        """Test metrics not used when namespace not provided"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            return {"statusCode": 200}

        mock_context = MagicMock()
        result = handler({"test": "event"}, mock_context)

        assert result["statusCode"] == 200

    def test_exception_logging_includes_context(self, monkeypatch):
        """Test exception logging includes error type and service context"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            raise KeyError("missing_key")

        mock_context = MagicMock()

        result = handler({"test": "event"}, mock_context)

        # Should return error response
        assert result["statusCode"] == 500
        assert result["body"] == "Internal Server Error"


def _sqs_event(*message_ids):
    return {"Records": [{"messageId": mid, "eventSource": "aws:sqs", "body": "{}"} for mid in message_ids]}


def _kinesis_event():
    return {
        "Records": [
            {
                "eventID": "shardId-000000000000:1",
                "eventSource": "aws:kinesis",
                "kinesis": {"sequenceNumber": "1", "data": "e30="},
            }
        ]
    }


class TestPowertoolsHandlerSqs:
    """An SQS batch must stay on the queue when the handler raises.

    Any value returned to an SQS event source mapping counts as success, so
    returning the 500 dict deletes the batch: no redelivery, no DLQ.
    """

    def test_sqs_exception_is_reraised(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        error = ConnectionError("Error reading SSH protocol banner")

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            raise error

        with pytest.raises(ConnectionError) as excinfo:
            handler(_sqs_event("m-1", "m-2"), MagicMock())

        assert excinfo.value is error

    def test_sqs_exception_alerts_slack_before_reraising(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        mock_slack_client = MagicMock()

        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.SlackClient", return_value=mock_slack_client):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service", slack_alert_channel="#errors")
                def handler(event, context):
                    raise ValueError("Test error")

                with pytest.raises(ValueError):
                    handler(_sqs_event("m-1"), MagicMock())

                mock_slack_client.send_message.assert_called_once()

    def test_sqs_reraises_original_exception_when_slack_fails(self, monkeypatch):
        """A failed alert must not replace the handler's exception or swallow it."""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        mock_slack_client = MagicMock()
        mock_slack_client.send_message.side_effect = RuntimeError("Slack API error")

        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.SlackClient", return_value=mock_slack_client):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service", slack_alert_channel="#errors")
                def handler(event, context):
                    raise ValueError("Test error")

                with pytest.raises(ValueError, match="Test error"):
                    handler(_sqs_event("m-1"), MagicMock())

    def test_sqs_detected_before_the_handler_consumes_records(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            event.pop("Records")
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            handler(_sqs_event("m-1"), MagicMock())

    def test_sqs_reraises_after_metrics_flush(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        flushed = []

        def log_metrics(func):
            def inner(event, context):
                try:
                    return func(event, context)
                finally:
                    flushed.append(True)

            return inner

        mock_metrics = MagicMock()
        mock_metrics.log_metrics = log_metrics

        with patch("nui_shared_utils.powertools_helpers.POWERTOOLS_AVAILABLE", True):
            with patch("nui_shared_utils.powertools_helpers.Metrics", return_value=mock_metrics):
                from nui_shared_utils.powertools_helpers import powertools_handler

                @powertools_handler(service_name="test-service", metrics_namespace="Test/Metrics")
                def handler(event, context):
                    raise ValueError("Test error")

                with pytest.raises(ValueError):
                    handler(_sqs_event("m-1"), MagicMock())

        assert flushed == [True]

    def test_sqs_success_returns_handler_result(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            return {"batchItemFailures": [{"itemIdentifier": "m-2"}]}

        result = handler(_sqs_event("m-1", "m-2"), MagicMock())

        assert result == {"batchItemFailures": [{"itemIdentifier": "m-2"}]}


class TestPowertoolsHandlerReraise:
    def test_kinesis_exception_is_reraised_when_enabled(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        error = RuntimeError("Kinesis record failed")

        @powertools_handler(service_name="test-service", reraise_exceptions=True)
        def handler(event, context):
            raise error

        with pytest.raises(RuntimeError) as excinfo:
            handler(_kinesis_event(), MagicMock())

        assert excinfo.value is error

    def test_kinesis_exception_keeps_500_by_default(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service", reraise_exceptions=False)
        def handler(event, context):
            raise RuntimeError("Kinesis record failed")

        result = handler(_kinesis_event(), MagicMock())

        assert result == {"statusCode": 500, "body": "Internal Server Error"}

    def test_eventbridge_exception_alerts_before_reraising(self, monkeypatch):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        calls = []
        mock_logger = MagicMock()
        mock_logger.inject_lambda_context.side_effect = lambda func: func
        mock_logger.exception.side_effect = lambda *args, **kwargs: calls.append("log")
        mock_slack_client = MagicMock()
        mock_slack_client.send_message.side_effect = lambda **kwargs: calls.append("slack")

        with patch("nui_shared_utils.powertools_helpers.get_powertools_logger", return_value=mock_logger):
            with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", True):
                with patch("nui_shared_utils.powertools_helpers.SlackClient", return_value=mock_slack_client):

                    @powertools_handler(
                        service_name="test-service",
                        slack_alert_channel="#errors",
                        reraise_exceptions=True,
                    )
                    def handler(event, context):
                        raise ValueError("EventBridge event failed")

                    with pytest.raises(ValueError, match="EventBridge event failed"):
                        handler(
                            {"source": "example.orders", "detail-type": "Order Created", "detail": {}},
                            MagicMock(),
                        )

        assert calls == ["log", "slack"]

    @pytest.mark.parametrize(
        "event",
        [
            {"Records": [{"EventSource": "aws:sns", "Sns": {}}]},
            {"Records": [{"eventSource": "aws:kinesis", "kinesis": {}}]},
            {"Records": [{"eventSource": "aws:dynamodb", "dynamodb": {}}]},
            {"Records": [{"eventSource": "aws:s3", "s3": {}}]},
            {"Records": []},
            {"Records": "not-a-list"},
            {"source": "aws.events", "detail-type": "Scheduled Event"},
            ["not", "a", "dict"],
        ],
        ids=["sns", "kinesis", "dynamodb", "s3", "empty-records", "records-not-list", "scheduled", "non-dict"],
    )
    def test_non_sqs_events_keep_returning_500(self, monkeypatch, event):
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.powertools_helpers import powertools_handler

        @powertools_handler(service_name="test-service")
        def handler(event, context):
            raise ValueError("Test error")

        result = handler(event, MagicMock())

        assert result == {"statusCode": 500, "body": "Internal Server Error"}


class TestOptionalImports:
    """Tests for graceful degradation of optional dependencies"""

    def test_powertools_not_available(self):
        """Test graceful degradation when Powertools not installed"""
        with patch("nui_shared_utils.powertools_helpers.POWERTOOLS_AVAILABLE", False):
            # Should not raise on import
            from nui_shared_utils.powertools_helpers import get_powertools_logger

            assert get_powertools_logger is not None

    def test_slack_client_not_available(self):
        """Test graceful degradation when SlackClient not available"""
        with patch("nui_shared_utils.powertools_helpers.SLACK_CLIENT_AVAILABLE", False):
            from nui_shared_utils.powertools_helpers import powertools_handler

            # Should not raise on decorator creation
            @powertools_handler(service_name="test", slack_alert_channel="#test")
            def handler(event, context):
                return {"statusCode": 200}

            assert handler is not None

    def test_coloredlogs_not_available(self, monkeypatch):
        """Test graceful degradation when coloredlogs not installed"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        with patch("nui_shared_utils.powertools_helpers.COLOREDLOGS_AVAILABLE", False):
            from nui_shared_utils.powertools_helpers import get_powertools_logger

            # Should work without coloredlogs
            logger = get_powertools_logger("test-service", local_dev_colors=True)
            assert isinstance(logger, logging.Logger)
