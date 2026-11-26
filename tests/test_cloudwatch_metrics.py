"""
Tests for cloudwatch_metrics module.
"""

import pytest

pytestmark = pytest.mark.unit
import os
from unittest.mock import patch, Mock
from datetime import datetime
from botocore.exceptions import ClientError

from nui_shared_utils.cloudwatch_metrics import (
    MetricsPublisher,
    MetricAggregator,
    StandardMetrics,
    TimedMetric,
    track_lambda_performance,
    create_service_dimensions,
    publish_health_metric,
)


class TestMetricsPublisher:
    """Tests for MetricsPublisher class."""

    @patch("boto3.client")
    def test_init_default_values(self, mock_boto3_client):
        """Test initialization with default values."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")

        assert publisher.namespace == "TestNamespace"
        assert publisher.default_dimensions == {}
        assert publisher.auto_flush_size == 20
        assert publisher.client == mock_client
        assert publisher.metric_buffer == []

        mock_boto3_client.assert_called_once_with("cloudwatch", region_name=None)

    @patch("boto3.client")
    def test_init_custom_values(self, mock_boto3_client):
        """Test initialization with custom values."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        dimensions = {"Service": "TestService", "Environment": "prod"}
        publisher = MetricsPublisher("CustomNamespace", dimensions=dimensions, auto_flush_size=10, region="us-east-1")

        assert publisher.namespace == "CustomNamespace"
        assert publisher.default_dimensions == dimensions
        assert publisher.auto_flush_size == 10
        assert publisher.client == mock_client

        mock_boto3_client.assert_called_once_with("cloudwatch", region_name="us-east-1")

    @patch("boto3.client")
    def test_put_metric_basic(self, mock_boto3_client):
        """Test basic metric publishing."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")

        # Mock datetime.utcnow() to get predictable timestamp
        with patch("nui_shared_utils.cloudwatch_metrics.datetime") as mock_datetime:
            mock_timestamp = datetime(2023, 1, 1, 12, 0, 0)
            mock_datetime.utcnow.return_value = mock_timestamp

            publisher.put_metric("TestMetric", 123.45, unit="Count")

        assert len(publisher.metric_buffer) == 1
        metric = publisher.metric_buffer[0]

        assert metric["MetricName"] == "TestMetric"
        assert metric["Value"] == 123.45
        assert metric["Unit"] == "Count"
        assert metric["Timestamp"] == mock_timestamp
        assert metric["StorageResolution"] == 60
        assert "Dimensions" not in metric

    @patch("boto3.client")
    def test_put_metric_with_dimensions(self, mock_boto3_client):
        """Test metric publishing with dimensions."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        default_dimensions = {"Service": "TestService"}
        publisher = MetricsPublisher("TestNamespace", dimensions=default_dimensions)

        additional_dimensions = {"Environment": "prod", "Region": "us-east-1"}
        publisher.put_metric("TestMetric", 100, dimensions=additional_dimensions)

        metric = publisher.metric_buffer[0]
        expected_dimensions = [
            {"Name": "Service", "Value": "TestService"},
            {"Name": "Environment", "Value": "prod"},
            {"Name": "Region", "Value": "us-east-1"},
        ]

        assert metric["Dimensions"] == expected_dimensions

    @patch("boto3.client")
    def test_put_metric_with_custom_timestamp(self, mock_boto3_client):
        """Test metric with custom timestamp."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")
        custom_timestamp = datetime(2023, 6, 15, 14, 30, 0)

        publisher.put_metric("TestMetric", 50, timestamp=custom_timestamp, storage_resolution=1)

        metric = publisher.metric_buffer[0]
        assert metric["Timestamp"] == custom_timestamp
        assert metric["StorageResolution"] == 1

    @patch("boto3.client")
    def test_put_metric_auto_flush(self, mock_boto3_client):
        """Test auto-flush when buffer reaches size limit."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace", auto_flush_size=2)

        # Add first metric - should not trigger flush
        publisher.put_metric("Metric1", 1)
        assert len(publisher.metric_buffer) == 1
        mock_client.put_metric_data.assert_not_called()

        # Add second metric - should trigger flush
        publisher.put_metric("Metric2", 2)
        assert len(publisher.metric_buffer) == 0  # Buffer cleared after flush
        mock_client.put_metric_data.assert_called_once()

    @patch("boto3.client")
    def test_put_metric_with_statistics(self, mock_boto3_client):
        """Test metric publishing with statistical values."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")
        values = [10, 20, 30, 40, 50]

        publisher.put_metric_with_statistics("StatsMetric", values, unit="Milliseconds")

        metric = publisher.metric_buffer[0]
        assert metric["MetricName"] == "StatsMetric"
        assert metric["Unit"] == "Milliseconds"
        assert metric["StatisticValues"] == {"SampleCount": 5, "Sum": 150, "Minimum": 10, "Maximum": 50}

    @patch("boto3.client")
    def test_put_metric_with_statistics_empty_values(self, mock_boto3_client):
        """Test metric with statistics with empty values list."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")

        publisher.put_metric_with_statistics("EmptyStatsMetric", [])

        assert len(publisher.metric_buffer) == 0

    @patch("boto3.client")
    def test_flush_success(self, mock_boto3_client):
        """Test successful metrics flush."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")

        # Add some metrics
        publisher.put_metric("Metric1", 1)
        publisher.put_metric("Metric2", 2)

        result = publisher.flush()

        assert result is True
        assert len(publisher.metric_buffer) == 0
        mock_client.put_metric_data.assert_called_once()

        # Verify the call arguments
        call_args = mock_client.put_metric_data.call_args
        assert call_args[1]["Namespace"] == "TestNamespace"
        assert len(call_args[1]["MetricData"]) == 2

    @patch("boto3.client")
    def test_flush_large_batch(self, mock_boto3_client):
        """Test flush with more than 20 metrics (batch splitting)."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace", auto_flush_size=100)

        # Add 25 metrics
        for i in range(25):
            publisher.put_metric(f"Metric{i}", i)

        publisher.flush()

        # Should make 2 calls (20 + 5)
        assert mock_client.put_metric_data.call_count == 2
        assert len(publisher.metric_buffer) == 0

    @patch("boto3.client")
    def test_flush_empty_buffer(self, mock_boto3_client):
        """Test flush with empty buffer."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")

        result = publisher.flush()

        assert result is True
        mock_client.put_metric_data.assert_not_called()

    @patch("boto3.client")
    def test_flush_client_error(self, mock_boto3_client):
        """Test flush with CloudWatch client error."""
        mock_client = Mock()
        mock_client.put_metric_data.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "Invalid metric"}}, "PutMetricData"
        )
        mock_boto3_client.return_value = mock_client

        publisher = MetricsPublisher("TestNamespace")
        publisher.put_metric("TestMetric", 1)

        result = publisher.flush()

        assert result is False
        # Buffer should not be cleared on error
        assert len(publisher.metric_buffer) == 1

    @patch("boto3.client")
    def test_context_manager(self, mock_boto3_client):
        """Test context manager functionality."""
        mock_client = Mock()
        mock_boto3_client.return_value = mock_client

        with MetricsPublisher("TestNamespace") as publisher:
            publisher.put_metric("TestMetric", 1)
            # Buffer should have metric
            assert len(publisher.metric_buffer) == 1

        # Should flush on exit
        mock_client.put_metric_data.assert_called_once()


class TestMetricAggregator:
    """Tests for MetricAggregator class."""

    def test_init(self):
        """Test aggregator initialization."""
        mock_publisher = Mock()
        aggregator = MetricAggregator(mock_publisher)

        assert aggregator.publisher == mock_publisher
        assert len(aggregator.aggregates) == 0
        assert len(aggregator.counters) == 0

    def test_add_value(self):
        """Test adding values for aggregation."""
        mock_publisher = Mock()
        aggregator = MetricAggregator(mock_publisher)

        aggregator.add_value("ResponseTime", 100)
        aggregator.add_value("ResponseTime", 200)
        aggregator.add_value("ErrorRate", 0.5)

        assert aggregator.aggregates["ResponseTime"] == [100.0, 200.0]
        assert aggregator.aggregates["ErrorRate"] == [0.5]

    def test_increment_counter(self):
        """Test counter increment."""
        mock_publisher = Mock()
        aggregator = MetricAggregator(mock_publisher)

        aggregator.increment_counter("Requests")
        aggregator.increment_counter("Requests", 5)
        aggregator.increment_counter("Errors", 2)

        assert aggregator.counters["Requests"] == 6
        assert aggregator.counters["Errors"] == 2

    def test_publish_aggregates(self):
        """Test publishing aggregated metrics."""
        mock_publisher = Mock()
        aggregator = MetricAggregator(mock_publisher)

        # Add some data
        aggregator.add_value("ResponseTime", 100)
        aggregator.add_value("ResponseTime", 200)
        aggregator.increment_counter("Requests", 5)

        dimensions = {"Service": "TestService"}
        aggregator.publish_aggregates(unit="Milliseconds", dimensions=dimensions)

        # Should publish statistics for aggregated values
        mock_publisher.put_metric_with_statistics.assert_called_once_with(
            "ResponseTime", [100.0, 200.0], unit="Milliseconds", dimensions=dimensions
        )

        # Should publish counters as regular metrics
        mock_publisher.put_metric.assert_called_once_with("Requests", 5, unit="Count", dimensions=dimensions)

        # Should clear aggregates
        assert len(aggregator.aggregates) == 0
        assert len(aggregator.counters) == 0

    def test_publish_aggregates_empty(self):
        """Test publishing with no aggregated data."""
        mock_publisher = Mock()
        aggregator = MetricAggregator(mock_publisher)

        aggregator.publish_aggregates()

        mock_publisher.put_metric_with_statistics.assert_not_called()
        mock_publisher.put_metric.assert_not_called()


class TestStandardMetrics:
    """Tests for StandardMetrics constants."""

    def test_constants_exist(self):
        """Test that all expected constants exist."""
        assert hasattr(StandardMetrics, "SERVICE_HEALTH")
        assert hasattr(StandardMetrics, "ERROR_RATE")
        assert hasattr(StandardMetrics, "RESPONSE_TIME")
        assert hasattr(StandardMetrics, "LAMBDA_DURATION")
        assert hasattr(StandardMetrics, "DB_QUERY_TIME")
        assert hasattr(StandardMetrics, "ES_QUERY_TIME")

    def test_constant_values(self):
        """Test some constant values."""
        assert StandardMetrics.SERVICE_HEALTH == "ServiceHealth"
        assert StandardMetrics.ERROR_RATE == "ErrorRate"
        assert StandardMetrics.LAMBDA_DURATION == "LambdaDuration"


class TestTimedMetric:
    """Tests for TimedMetric context manager."""

    def test_timed_metric_success(self):
        """Test successful timing."""
        mock_publisher = Mock()

        with patch("nui_shared_utils.cloudwatch_metrics.time.time") as mock_time:
            mock_time.side_effect = [1000.0, 1000.5]  # 500ms duration

            with TimedMetric(mock_publisher, "TestOperation", unit="Milliseconds"):
                pass

        mock_publisher.put_metric.assert_called_once_with("TestOperation", 500.0, unit="Milliseconds", dimensions=None)

    def test_timed_metric_with_dimensions(self):
        """Test timing with dimensions."""
        mock_publisher = Mock()
        dimensions = {"Service": "TestService"}

        with patch("nui_shared_utils.cloudwatch_metrics.time.time") as mock_time:
            mock_time.side_effect = [1000.0, 1000.2]  # 200ms duration

            with TimedMetric(mock_publisher, "DatabaseQuery", dimensions=dimensions):
                pass

        # Check that put_metric was called with correct arguments (allowing for float precision)
        mock_publisher.put_metric.assert_called_once()
        call_args = mock_publisher.put_metric.call_args
        assert call_args[0][0] == "DatabaseQuery"
        assert abs(call_args[0][1] - 200.0) < 0.001  # Allow for float precision
        assert call_args[1]["unit"] == "Milliseconds"
        assert call_args[1]["dimensions"] == dimensions

    def test_timed_metric_with_exception(self):
        """Test timing when exception occurs."""
        mock_publisher = Mock()

        with patch("nui_shared_utils.cloudwatch_metrics.time.time") as mock_time:
            mock_time.side_effect = [1000.0, 1000.1]  # 100ms duration

            with pytest.raises(ValueError):
                with TimedMetric(mock_publisher, "FailingOperation"):
                    raise ValueError("Test error")

        # Should still record the metric (allowing for float precision)
        mock_publisher.put_metric.assert_called_once()
        call_args = mock_publisher.put_metric.call_args
        assert call_args[0][0] == "FailingOperation"
        assert abs(call_args[0][1] - 100.0) < 0.001  # Allow for float precision
        assert call_args[1]["unit"] == "Milliseconds"
        assert call_args[1]["dimensions"] is None


class TestTrackLambdaPerformance:
    """Tests for track_lambda_performance decorator."""

    @patch("nui_shared_utils.cloudwatch_metrics.MetricsPublisher")
    @patch("nui_shared_utils.cloudwatch_metrics.time.time")
    def test_successful_execution(self, mock_time, mock_publisher_class):
        """Test decorator with successful function execution."""
        mock_publisher = Mock()
        mock_publisher_class.return_value = mock_publisher
        mock_time.side_effect = [1000.0, 1000.5]  # 500ms duration

        # Mock Lambda context
        mock_context = Mock()
        mock_context.function_name = "test-function"
        # Explicitly delete is_warm to simulate cold start
        if hasattr(mock_context, "is_warm"):
            delattr(mock_context, "is_warm")

        @track_lambda_performance()
        def test_handler(event, context):
            return {"statusCode": 200}

        with patch.dict(os.environ, {"STAGE": "prod"}):
            result = test_handler({"test": "event"}, mock_context)

        assert result == {"statusCode": 200}
        assert hasattr(mock_context, "is_warm")

        # Should create publisher with correct dimensions
        mock_publisher_class.assert_called_once_with(
            "Application", dimensions={"FunctionName": "test-function", "Environment": "prod"}
        )

        # Check the calls more flexibly
        put_metric_calls = mock_publisher.put_metric.call_args_list
        call_metrics = [call[0][0] for call in put_metric_calls]

        # Should record cold start, duration, and request count
        assert mock_publisher.put_metric.call_count == 3
        assert StandardMetrics.LAMBDA_COLD_STARTS in call_metrics
        assert StandardMetrics.LAMBDA_DURATION in call_metrics
        assert StandardMetrics.REQUEST_COUNT in call_metrics

        mock_publisher.flush.assert_called_once()

    @patch("nui_shared_utils.cloudwatch_metrics.MetricsPublisher")
    @patch("nui_shared_utils.cloudwatch_metrics.time.time")
    def test_warm_invocation(self, mock_time, mock_publisher_class):
        """Test decorator with warm invocation (no cold start)."""
        mock_publisher = Mock()
        mock_publisher_class.return_value = mock_publisher
        mock_time.side_effect = [1000.0, 1000.2]  # 200ms duration

        # Mock Lambda context with is_warm attribute
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.is_warm = True

        @track_lambda_performance()
        def test_handler(event, context):
            return {"statusCode": 200}

        test_handler({"test": "event"}, mock_context)

        # Should NOT record cold start
        put_metric_calls = [call[0][0] for call in mock_publisher.put_metric.call_args_list]
        assert StandardMetrics.LAMBDA_COLD_STARTS not in put_metric_calls
        assert StandardMetrics.LAMBDA_DURATION in put_metric_calls
        assert StandardMetrics.REQUEST_COUNT in put_metric_calls

    @patch("nui_shared_utils.cloudwatch_metrics.MetricsPublisher")
    @patch("nui_shared_utils.cloudwatch_metrics.time.time")
    def test_exception_handling(self, mock_time, mock_publisher_class):
        """Test decorator with function that raises exception."""
        mock_publisher = Mock()
        mock_publisher_class.return_value = mock_publisher
        mock_time.side_effect = [1000.0, 1000.1]  # 100ms duration

        mock_context = Mock()
        mock_context.function_name = "test-function"

        @track_lambda_performance()
        def failing_handler(event, context):
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            failing_handler({"test": "event"}, mock_context)

        # Should record error metric
        put_metric_calls = [call[0][0] for call in mock_publisher.put_metric.call_args_list]
        assert StandardMetrics.LAMBDA_ERRORS in put_metric_calls

        # Should still flush metrics
        mock_publisher.flush.assert_called_once()


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_create_service_dimensions_basic(self):
        """Test creating service dimensions with basic parameters."""
        with patch.dict(os.environ, {"STAGE": "prod"}):
            dimensions = create_service_dimensions("test-service")

        assert dimensions["ServiceName"] == "test-service"
        assert dimensions["Environment"] == "prod"

    def test_create_service_dimensions_with_environment(self):
        """Test creating service dimensions with explicit environment."""
        dimensions = create_service_dimensions("test-service", environment="dev")

        assert dimensions["ServiceName"] == "test-service"
        assert dimensions["Environment"] == "dev"

    def test_create_service_dimensions_with_region(self):
        """Test creating service dimensions with AWS region."""
        with patch.dict(os.environ, {"AWS_REGION": "us-west-2", "STAGE": "prod"}):
            dimensions = create_service_dimensions("test-service")

        assert dimensions["ServiceName"] == "test-service"
        assert dimensions["Environment"] == "prod"
        assert dimensions["Region"] == "us-west-2"

    def test_create_service_dimensions_default_region(self, monkeypatch):
        """Test creating service dimensions with AWS_DEFAULT_REGION."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        with patch.dict(os.environ, {"AWS_DEFAULT_REGION": "eu-west-1", "STAGE": "dev"}):
            dimensions = create_service_dimensions("test-service")

        assert dimensions["ServiceName"] == "test-service"
        assert dimensions["Environment"] == "dev"
        assert dimensions["Region"] == "eu-west-1"

    @patch("nui_shared_utils.cloudwatch_metrics.MetricsPublisher")
    def test_publish_health_metric_healthy(self, mock_publisher_class):
        """Test publishing health metric for healthy service."""
        mock_publisher = Mock()
        mock_publisher_class.return_value = mock_publisher

        with patch("nui_shared_utils.cloudwatch_metrics.create_service_dimensions") as mock_create_dims:
            mock_create_dims.return_value = {"ServiceName": "test-service"}

            publish_health_metric("test-service", True)

        mock_create_dims.assert_called_once_with("test-service")
        mock_publisher_class.assert_called_once_with("Application", dimensions={"ServiceName": "test-service"})
        mock_publisher.put_metric.assert_called_once_with(StandardMetrics.SERVICE_HEALTH, 1, unit="None")
        mock_publisher.flush.assert_called_once()

    @patch("nui_shared_utils.cloudwatch_metrics.MetricsPublisher")
    def test_publish_health_metric_unhealthy(self, mock_publisher_class):
        """Test publishing health metric for unhealthy service."""
        mock_publisher = Mock()
        mock_publisher_class.return_value = mock_publisher

        with patch("nui_shared_utils.cloudwatch_metrics.create_service_dimensions") as mock_create_dims:
            mock_create_dims.return_value = {"ServiceName": "test-service"}

            publish_health_metric("test-service", False, namespace="CustomNamespace")

        mock_publisher_class.assert_called_once_with("CustomNamespace", dimensions={"ServiceName": "test-service"})
        mock_publisher.put_metric.assert_called_once_with(StandardMetrics.SERVICE_HEALTH, 0, unit="None")
