"""
CloudWatch metrics publishing utilities for Lambda services.
Provides efficient batching and standardized metric publishing patterns.
"""

import os
import time
import logging
from typing import Dict, List, Optional, Union
from datetime import datetime
from collections import defaultdict

log = logging.getLogger(__name__)


class MetricsPublisher:
    """
    Efficient CloudWatch metrics publisher with batching support.

    Example:
        publisher = MetricsPublisher('Application')
        publisher.put_metric('RecordsProcessed', 150, unit='Count')
        publisher.put_metric('ResponseTime', 245.5, unit='Milliseconds')
        publisher.flush()  # Send all metrics
    """

    def __init__(
        self,
        namespace: str,
        dimensions: Optional[Dict[str, str]] = None,
        auto_flush_size: int = 20,
        region: Optional[str] = None,
    ):
        """
        Initialize metrics publisher.

        Args:
            namespace: CloudWatch namespace for metrics
            dimensions: Default dimensions to apply to all metrics
            auto_flush_size: Automatically flush when batch reaches this size
            region: AWS region (uses default if not specified)
        """
        self.namespace = namespace
        self.default_dimensions = dimensions or {}
        self.auto_flush_size = auto_flush_size
        self._region = region
        self._client = None
        self.metric_buffer: List[Dict] = []

    @property
    def client(self):
        """Lazily construct the boto3 CloudWatch client on first use."""
        if self._client is None:
            import boto3

            self._client = boto3.client("cloudwatch", region_name=self._region)
        return self._client

    def put_metric(
        self,
        metric_name: str,
        value: Union[int, float],
        unit: str = "None",
        timestamp: Optional[datetime] = None,
        dimensions: Optional[Dict[str, str]] = None,
        storage_resolution: int = 60,
    ) -> None:
        """
        Add a metric to the buffer.

        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: CloudWatch unit (Count, Milliseconds, Bytes, etc.)
            timestamp: Metric timestamp (defaults to now)
            dimensions: Additional dimensions for this metric
            storage_resolution: 1 for high-resolution, 60 for standard
        """
        metric_data = {
            "MetricName": metric_name,
            "Value": float(value),
            "Unit": unit,
            "Timestamp": timestamp or datetime.utcnow(),
            "StorageResolution": storage_resolution,
        }

        # Merge dimensions
        all_dimensions = {**self.default_dimensions}
        if dimensions:
            all_dimensions.update(dimensions)

        if all_dimensions:
            metric_data["Dimensions"] = [{"Name": k, "Value": str(v)} for k, v in all_dimensions.items()]

        self.metric_buffer.append(metric_data)

        # Auto-flush if buffer is full
        if len(self.metric_buffer) >= self.auto_flush_size:
            self.flush()

    def put_metric_with_statistics(
        self,
        metric_name: str,
        values: List[Union[int, float]],
        unit: str = "None",
        timestamp: Optional[datetime] = None,
        dimensions: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Add a metric with statistical values.

        Args:
            metric_name: Name of the metric
            values: List of values to calculate statistics from
            unit: CloudWatch unit
            timestamp: Metric timestamp
            dimensions: Additional dimensions
        """
        if not values:
            return

        # Calculate statistics
        sorted_values = sorted(values)
        count = len(values)
        sum_value = sum(values)
        min_value = sorted_values[0]
        max_value = sorted_values[-1]

        metric_data = {
            "MetricName": metric_name,
            "StatisticValues": {"SampleCount": count, "Sum": sum_value, "Minimum": min_value, "Maximum": max_value},
            "Unit": unit,
            "Timestamp": timestamp or datetime.utcnow(),
        }

        # Merge dimensions
        all_dimensions = {**self.default_dimensions}
        if dimensions:
            all_dimensions.update(dimensions)

        if all_dimensions:
            metric_data["Dimensions"] = [{"Name": k, "Value": str(v)} for k, v in all_dimensions.items()]

        self.metric_buffer.append(metric_data)

        if len(self.metric_buffer) >= self.auto_flush_size:
            self.flush()

    def flush(self) -> bool:
        """
        Send all buffered metrics to CloudWatch.

        Successful requests remove their batches immediately. A failed request
        leaves its batch and all unattempted metrics buffered for a later retry.
        A request accepted by CloudWatch whose response is lost can be duplicated
        on retry; this publisher does not provide exactly-once delivery.

        Returns:
            bool: True if successful, False on a CloudWatch ClientError.
                Other exceptions propagate with unsent metrics still buffered.
        """
        if not self.metric_buffer:
            return True

        from botocore.exceptions import ClientError

        try:
            published_count = 0
            while self.metric_buffer:
                batch = self.metric_buffer[:20]
                self.client.put_metric_data(Namespace=self.namespace, MetricData=batch)
                # Acknowledged batches must not be retried if a later request fails.
                del self.metric_buffer[: len(batch)]
                published_count += len(batch)

            log.info(f"Published {published_count} metrics to {self.namespace}")
            return True

        except ClientError as e:
            log.error(f"Failed to publish metrics: {e}")
            return False

    def __enter__(self):
        """Context manager support."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure metrics are flushed on exit."""
        self.flush()


class MetricAggregator:
    """
    Aggregate metrics over time before publishing.
    Useful for high-frequency metrics that need aggregation.
    """

    def __init__(self, publisher: MetricsPublisher):
        self.publisher = publisher
        self.aggregates: Dict[str, List[float]] = defaultdict(list)
        self.counters: Dict[str, float] = defaultdict(float)

    def add_value(self, metric_name: str, value: Union[int, float]) -> None:
        """Add a value to be aggregated."""
        self.aggregates[metric_name].append(float(value))

    def increment_counter(self, metric_name: str, value: Union[int, float] = 1) -> None:
        """Increment a counter metric."""
        self.counters[metric_name] += value

    def publish_aggregates(self, unit: str = "None", dimensions: Optional[Dict[str, str]] = None) -> None:
        """Publish all aggregated metrics with statistics."""
        # Publish aggregated values
        for metric_name, values in self.aggregates.items():
            if values:
                self.publisher.put_metric_with_statistics(metric_name, values, unit=unit, dimensions=dimensions)

        # Publish counters
        for metric_name, value in self.counters.items():
            self.publisher.put_metric(metric_name, value, unit="Count", dimensions=dimensions)

        # Clear aggregates
        self.aggregates.clear()
        self.counters.clear()


# Standard metric names for consistency across services
class StandardMetrics:
    """Standard metric names used across application services."""

    # Service health metrics
    SERVICE_HEALTH = "ServiceHealth"
    ERROR_RATE = "ErrorRate"
    RESPONSE_TIME = "ResponseTime"
    REQUEST_COUNT = "RequestCount"

    # Business metrics
    RECORDS_CREATED = "RecordsCreated"
    RECORDS_PROCESSED = "RecordsProcessed"
    USERS_ACTIVE = "ActiveUsers"
    REVENUE_PROCESSED = "RevenueProcessed"

    # Lambda metrics
    LAMBDA_DURATION = "LambdaDuration"
    LAMBDA_ERRORS = "LambdaErrors"
    LAMBDA_THROTTLES = "LambdaThrottles"
    LAMBDA_COLD_STARTS = "LambdaColdStarts"

    # Database metrics
    DB_QUERY_TIME = "DatabaseQueryTime"
    DB_CONNECTION_ERRORS = "DatabaseConnectionErrors"
    DB_ACTIVE_CONNECTIONS = "DatabaseActiveConnections"

    # Elasticsearch metrics
    ES_QUERY_TIME = "ElasticsearchQueryTime"
    ES_QUERY_ERRORS = "ElasticsearchQueryErrors"
    ES_DOCUMENT_COUNT = "ElasticsearchDocumentCount"

    # External API metrics
    API_CALL_DURATION = "ExternalAPICallDuration"
    API_CALL_ERRORS = "ExternalAPICallErrors"
    API_RATE_LIMIT_HITS = "APIRateLimitHits"


def track_lambda_performance(namespace: str = "Application"):
    """
    Decorator to automatically track Lambda function performance.

    Example:
        @track_lambda_performance()
        def handler(event, context):
            # Your Lambda logic
            return response
    """

    def decorator(func):
        def wrapper(event, context):
            start_time = time.time()
            cold_start = not hasattr(context, "is_warm")

            publisher = MetricsPublisher(
                namespace,
                dimensions={"FunctionName": context.function_name, "Environment": os.environ.get("STAGE", "dev")},
            )

            try:
                # Mark as warm for next invocation
                context.is_warm = True

                # Track cold start
                if cold_start:
                    publisher.put_metric(StandardMetrics.LAMBDA_COLD_STARTS, 1, unit="Count")

                # Execute function
                result = func(event, context)

                # Track success metrics
                duration = (time.time() - start_time) * 1000  # Convert to milliseconds
                publisher.put_metric(StandardMetrics.LAMBDA_DURATION, duration, unit="Milliseconds")
                publisher.put_metric(StandardMetrics.REQUEST_COUNT, 1, unit="Count")

                return result

            except Exception:
                # Track error
                publisher.put_metric(StandardMetrics.LAMBDA_ERRORS, 1, unit="Count")
                raise

            finally:
                # Ensure metrics are published
                publisher.flush()

        return wrapper

    return decorator


def create_service_dimensions(service_name: str, environment: Optional[str] = None) -> Dict[str, str]:
    """
    Create standard dimensions for service metrics.

    Args:
        service_name: Name of the service
        environment: Environment (defaults to STAGE env var)

    Returns:
        Dict of dimensions
    """
    dimensions = {"ServiceName": service_name, "Environment": environment or os.environ.get("STAGE", "dev")}

    # Add region if available
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION"))
    if region:
        dimensions["Region"] = region

    return dimensions


def publish_health_metric(service_name: str, is_healthy: bool, namespace: str = "Application") -> None:
    """
    Publish a simple health metric for a service.

    Args:
        service_name: Name of the service
        is_healthy: Whether the service is healthy
        namespace: CloudWatch namespace
    """
    publisher = MetricsPublisher(namespace, dimensions=create_service_dimensions(service_name))

    publisher.put_metric(StandardMetrics.SERVICE_HEALTH, 1 if is_healthy else 0, unit="None")

    publisher.flush()


class TimedMetric:
    """
    Context manager for timing operations and publishing metrics.

    Example:
        with MetricsPublisher('MyApp') as publisher:
            with TimedMetric(publisher, 'DatabaseQuery', unit='Milliseconds'):
                # Perform database query
                results = db.query("SELECT * FROM records")
    """

    def __init__(
        self,
        publisher: MetricsPublisher,
        metric_name: str,
        unit: str = "Milliseconds",
        dimensions: Optional[Dict[str, str]] = None,
    ):
        self.publisher = publisher
        self.metric_name = metric_name
        self.unit = unit
        self.dimensions = dimensions
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (time.time() - self.start_time) * 1000  # Convert to milliseconds
        self.publisher.put_metric(self.metric_name, duration, unit=self.unit, dimensions=self.dimensions)
