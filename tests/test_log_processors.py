import base64
import binascii
import gzip
import json
import zlib
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit

from nui_shared_utils.log_processors import (
    derive_index_name,
    extract_cloudwatch_logs_from_kinesis,
)


class TestExtractCloudwatchLogsFromKinesis:
    """Tests for Kinesis log extraction."""

    def _make_log_data(
        self, *, log_group="/aws/lambda/test", log_stream="stream", events=None, message_type="DATA_MESSAGE"
    ):
        """Helper to create a CloudWatch logs data structure."""
        if events is None:
            events = [{"id": "1", "timestamp": 1705312800000, "message": "test"}]
        return {
            "messageType": message_type,
            "logGroup": log_group,
            "logStream": log_stream,
            "logEvents": events,
        }

    def _make_kinesis_record(self, data: dict) -> dict:
        """Helper to create properly encoded Kinesis record."""
        json_bytes = json.dumps(data).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        encoded = base64.b64encode(compressed).decode("utf-8")
        return {"kinesis": {"data": encoded}}

    def _passthrough_processor(self, _log_group, _log_stream, events):
        """Simple processor that yields minimal ES docs."""
        for event in events:
            yield {"_index": "test", "_id": event["id"], "_source": {"message": event["message"]}}

    def test_basic_extraction(self):
        log_data = self._make_log_data(
            log_group="/aws/lambda/my-function",
            log_stream="2025/01/15/[$LATEST]abc123",
            events=[{"id": "event-1", "timestamp": 1705312800000, "message": "Test message"}],
        )
        records = [self._make_kinesis_record(log_data)]

        results = list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))

        assert len(results) == 1
        assert results[0]["_source"]["message"] == "Test message"

    def test_control_message_skipped(self):
        log_data = self._make_log_data(message_type="CONTROL_MESSAGE")
        records = [self._make_kinesis_record(log_data)]

        results = list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))

        assert len(results) == 0

    def test_multiple_events(self):
        log_data = self._make_log_data(
            events=[
                {"id": "1", "timestamp": 1705312800000, "message": "First"},
                {"id": "2", "timestamp": 1705312801000, "message": "Second"},
                {"id": "3", "timestamp": 1705312802000, "message": "Third"},
            ]
        )
        records = [self._make_kinesis_record(log_data)]

        results = list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))

        assert len(results) == 3
        assert [r["_id"] for r in results] == ["1", "2", "3"]

    def test_multiple_records(self):
        records = [
            self._make_kinesis_record(
                self._make_log_data(
                    log_group="/aws/lambda/func-a",
                    events=[{"id": "a1", "timestamp": 1705312800000, "message": "From A"}],
                )
            ),
            self._make_kinesis_record(
                self._make_log_data(
                    log_group="/aws/lambda/func-b",
                    events=[{"id": "b1", "timestamp": 1705312800000, "message": "From B"}],
                )
            ),
        ]

        results = list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))

        assert len(results) == 2
        assert [r["_id"] for r in results] == ["a1", "b1"]

    def test_process_fn_receives_correct_args(self):
        log_data = self._make_log_data(
            log_group="/aws/lambda/my-function",
            log_stream="2025/01/15/[$LATEST]abc123",
        )
        records = [self._make_kinesis_record(log_data)]

        captured = {}

        def capturing_processor(log_group, log_stream, events):
            captured["log_group"] = log_group
            captured["log_stream"] = log_stream
            captured["events"] = events
            return iter([])

        list(extract_cloudwatch_logs_from_kinesis(records, capturing_processor))

        assert captured["log_group"] == "/aws/lambda/my-function"
        assert captured["log_stream"] == "2025/01/15/[$LATEST]abc123"
        assert len(captured["events"]) == 1

    def test_error_handler_called_on_processing_error(self):
        records = [self._make_kinesis_record(self._make_log_data())]

        errors = []

        def failing_processor(_log_group, _log_stream, _events):
            raise ValueError("Processing failed")

        results = list(
            extract_cloudwatch_logs_from_kinesis(
                records, failing_processor, on_error=lambda exc, data: errors.append((exc, data))
            )
        )

        assert len(results) == 0
        assert len(errors) == 1
        assert isinstance(errors[0][0], ValueError)

    def test_error_raised_without_handler(self):
        records = [self._make_kinesis_record(self._make_log_data())]

        def failing_processor(_log_group, _log_stream, _events):
            raise ValueError("Processing failed")

        with pytest.raises(ValueError, match="Processing failed"):
            list(extract_cloudwatch_logs_from_kinesis(records, failing_processor))

    def test_malformed_record_with_error_handler(self):
        records = [{"kinesis": {"data": "not-valid-base64!!!"}}]

        errors = []
        results = list(
            extract_cloudwatch_logs_from_kinesis(
                records,
                self._passthrough_processor,
                on_error=lambda exc, _data: errors.append(exc),
            )
        )

        assert len(results) == 0
        assert len(errors) == 1

    def test_missing_keys_with_error_handler(self):
        """Missing required keys (e.g. logGroup) should route to on_error."""
        data = {"messageType": "DATA_MESSAGE"}  # missing logGroup, logStream, logEvents
        json_bytes = json.dumps(data).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        encoded = base64.b64encode(compressed).decode("utf-8")
        records = [{"kinesis": {"data": encoded}}]

        errors = []
        results = list(
            extract_cloudwatch_logs_from_kinesis(
                records,
                self._passthrough_processor,
                on_error=lambda exc, _data: errors.append(exc),
            )
        )

        assert len(results) == 0
        assert len(errors) == 1
        assert isinstance(errors[0], KeyError)

    def test_missing_keys_raises_without_handler(self):
        """Missing required keys should raise KeyError when no on_error handler."""
        data = {"messageType": "DATA_MESSAGE"}  # missing logGroup, logStream, logEvents
        json_bytes = json.dumps(data).encode("utf-8")
        compressed = gzip.compress(json_bytes)
        encoded = base64.b64encode(compressed).decode("utf-8")
        records = [{"kinesis": {"data": encoded}}]

        with pytest.raises(KeyError):
            list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))

    def test_missing_kinesis_key_with_error_handler(self):
        """Records missing 'kinesis' or 'data' keys should route to on_error."""
        records = [{"not_kinesis": {}}]

        errors = []
        results = list(
            extract_cloudwatch_logs_from_kinesis(
                records,
                self._passthrough_processor,
                on_error=lambda exc, _data: errors.append(exc),
            )
        )

        assert len(results) == 0
        assert len(errors) == 1
        assert isinstance(errors[0], KeyError)

    def test_missing_kinesis_key_raises_without_handler(self):
        """Records missing 'kinesis' key should raise when no on_error handler."""
        records = [{"not_kinesis": {}}]

        with pytest.raises(KeyError):
            list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))

    def test_malformed_record_raises_without_handler(self):
        records = [{"kinesis": {"data": "not-valid-base64!!!"}}]

        with pytest.raises((binascii.Error, zlib.error)):
            list(extract_cloudwatch_logs_from_kinesis(records, self._passthrough_processor))


class TestDeriveIndexName:
    """Tests for index name derivation."""

    def test_basic_derivation(self):
        ts = datetime(2025, 1, 15, tzinfo=timezone.utc)
        assert derive_index_name("/aws/lambda/order-processor", ts) == "log-order-processor-2025-m01"

    def test_target_override(self):
        ts = datetime(2025, 1, 15, tzinfo=timezone.utc)
        assert (
            derive_index_name("/aws/lambda/test", ts, target_override="custom-target") == "log-custom-target-2025-m01"
        )

    def test_custom_prefix(self):
        ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
        assert derive_index_name("/ecs/my-service", ts, prefix="logs") == "logs-my-service-2025-m06"

    def test_custom_date_format(self):
        ts = datetime(2025, 3, 15, tzinfo=timezone.utc)
        assert derive_index_name("/aws/lambda/test", ts, date_format="%Y-%m-%d") == "log-test-2025-03-15"

    def test_lowercase_enforcement(self):
        ts = datetime(2025, 1, 15, tzinfo=timezone.utc)
        assert derive_index_name("/aws/lambda/MyFunction", ts) == "log-myfunction-2025-m01"

    def test_ecs_log_group(self):
        ts = datetime(2025, 2, 1, tzinfo=timezone.utc)
        assert derive_index_name("/ecs/api-gateway", ts) == "log-api-gateway-2025-m02"
