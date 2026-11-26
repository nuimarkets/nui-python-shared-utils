"""
Utilities for extracting CloudWatch logs from Kinesis stream records.

Provides standardized Kinesis log extraction, decompression, and index naming
for Lambda functions that stream CloudWatch logs to Elasticsearch.
"""

import base64
import json
import logging
import zlib
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, TypedDict

logger = logging.getLogger(__name__)


class CloudWatchLogEvent(TypedDict):
    """Single log event from CloudWatch."""

    id: str
    timestamp: int  # Unix timestamp in milliseconds
    message: str


class CloudWatchLogsData(TypedDict):
    """Decompressed CloudWatch logs data structure."""

    messageType: str  # "DATA_MESSAGE" or "CONTROL_MESSAGE"
    owner: str
    logGroup: str
    logStream: str
    subscriptionFilters: List[str]
    logEvents: List[CloudWatchLogEvent]


def extract_cloudwatch_logs_from_kinesis(
    records: List[Dict[str, Any]],
    process_fn: Callable[[str, str, List[Dict]], Iterator[Dict]],
    on_error: Optional[Callable[[Exception, Dict], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Extract CloudWatch logs from Kinesis stream records.

    Handles base64 decoding, gzip decompression, JSON parsing, and
    CONTROL_MESSAGE filtering. Yields documents from the process_fn callback.

    Args:
        records: Kinesis event records (event["Records"])
        process_fn: Callback to process log events. Signature:
            process_fn(log_group: str, log_stream: str, log_events: List[Dict]) -> Iterator[Dict]
            Should yield dicts with at minimum: {"_index": str, "_source": dict}
        on_error: Optional error handler. If None, exceptions are raised.
            Signature: on_error(exception: Exception, record_data: Dict) -> None

    Yields:
        Dict documents ready for Elasticsearch streaming_bulk()

    Example:
        from elasticsearch.helpers import streaming_bulk

        def my_processor(log_group, log_stream, events):
            for event in events:
                yield {
                    "_index": f"log-{log_group.split('/')[-1]}-2025-01",
                    "_id": event["id"],
                    "_source": {"message": event["message"], ...}
                }

        for ok, response in streaming_bulk(
            client=es,
            actions=extract_cloudwatch_logs_from_kinesis(
                event["Records"],
                process_fn=my_processor
            )
        ):
            if not ok:
                logger.error(f"Failed: {response}")
    """
    log_counts = []

    for row in records:
        try:
            raw_data = row["kinesis"]["data"]
        except (KeyError, TypeError) as e:
            logger.exception("Kinesis record missing 'kinesis.data' key")
            if on_error:
                on_error(e, {"row": row})
                continue
            raise

        try:
            decompressed = zlib.decompress(base64.b64decode(raw_data), 16 + zlib.MAX_WBITS).decode("utf-8")
            data = json.loads(decompressed)
        except Exception as e:
            logger.exception("Failed to decode/decompress Kinesis record")
            if on_error:
                on_error(e, {"raw_data": raw_data[:100]})
                continue
            raise

        try:
            message_type = data["messageType"]
            log_group = data["logGroup"]
            log_stream = data["logStream"]
            log_events = data["logEvents"]
        except KeyError as e:
            logger.exception("Malformed CloudWatch logs payload missing key: %s", e)
            if on_error:
                on_error(e, data)
                continue
            raise

        if message_type == "CONTROL_MESSAGE":
            logger.debug("Skipping CONTROL_MESSAGE")
            continue

        log_counts.append(len(log_events))

        try:
            yield from process_fn(log_group, log_stream, log_events)
        except Exception as e:
            logger.exception(f"Failed to process log events from {log_group}")
            if on_error:
                on_error(e, data)
                continue
            raise

    logger.debug(f"Processed {sum(log_counts)} log events from {len(records)} Kinesis records")


def derive_index_name(
    log_group: str,
    timestamp: datetime,
    prefix: str = "log",
    date_format: str = "%Y-m%m",
    target_override: Optional[str] = None,
) -> str:
    """
    Derive Elasticsearch index name from log group and timestamp.

    Default pattern: log-{service}-{YYYY}-m{MM}

    Args:
        log_group: CloudWatch log group name (e.g., "/aws/lambda/my-function")
        timestamp: Event timestamp for date-based index suffix
        prefix: Index name prefix (default: "log")
        date_format: strftime format for date suffix (default: "%Y-m%m")
        target_override: If provided, use this as service name instead of deriving from log_group

    Returns:
        Index name string (e.g., "log-my-function-2025-m01")

    Example:
        >>> derive_index_name("/aws/lambda/order-processor", datetime(2025, 1, 15))
        'log-order-processor-2025-m01'

        >>> derive_index_name("/ecs/my-service", datetime(2025, 1, 15), target_override="custom")
        'log-custom-2025-m01'
    """
    if target_override:
        service = target_override
    else:
        service = log_group.split("/")[-1]

    date_suffix = timestamp.strftime(date_format)

    return f"{prefix}-{service}-{date_suffix}".lower()
