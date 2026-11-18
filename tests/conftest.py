"""
Pytest configuration and shared fixtures for lambda-shared-utils tests.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime
import json


@pytest.fixture
def clear_database_env(monkeypatch):
    """Keep database tests independent of the invoking shell's credentials."""
    for name in (
        "DB_HOST",
        "DB_PASSWORD",
        "DB_PORT",
        "DB_USERNAME",
        "DB_DATABASE",
        "DB_CREDENTIALS_SECRET",
        "DBCREDENTIALS_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_boto3_session():
    """Mock boto3 session."""
    with patch("boto3.session.Session") as mock_session_class:
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        yield mock_session


@pytest.fixture
def mock_secrets_manager(mock_boto3_session):
    """Mock AWS Secrets Manager client."""
    mock_sm_client = Mock()
    mock_boto3_session.client.return_value = mock_sm_client

    # Default secret responses
    mock_sm_client.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {"username": "test_user", "password": "test_pass", "host": "test_host", "port": 3306, "database": "test_db"}
        )
    }

    # Set region_name
    mock_boto3_session.region_name = "ap-southeast-2"

    return mock_sm_client


@pytest.fixture
def mock_slack_response():
    """Standard successful Slack API response."""
    return {
        "ok": True,
        "ts": "1234567890.123456",
        "channel": "C1234567890",
        "message": {"text": "Test message", "ts": "1234567890.123456"},
    }


@pytest.fixture
def mock_es_client():
    """Mock Elasticsearch client."""
    client = Mock()
    client.search.return_value = {"hits": {"total": {"value": 10}, "hits": []}, "aggregations": {}}
    client.info.return_value = {"version": {"number": "7.10.0"}}
    return client


@pytest.fixture
def mock_db_connection():
    """Mock database connection."""
    connection = Mock()
    cursor = Mock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.rowcount = 0
    connection.cursor.return_value = cursor
    connection.commit.return_value = None
    connection.close.return_value = None
    return connection


@pytest.fixture
def mock_datetime():
    """Mock datetime for consistent time testing."""
    with patch("nui_shared_utils.timezone.datetime") as mock_dt:
        mock_now = datetime(2024, 1, 30, 10, 30, 45)
        mock_dt.now.return_value = mock_now
        mock_dt.utcnow.return_value = mock_now
        yield mock_dt


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear any caches before each test."""
    # Clear secrets cache
    from nui_shared_utils.secrets_helper import clear_cache

    clear_cache()
    yield
    # Clear again after test
    clear_cache()


@pytest.fixture
def sample_slack_blocks():
    """Sample Slack blocks for testing."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Test Header"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "Test section content"}},
    ]


@pytest.fixture
def sample_es_query():
    """Sample Elasticsearch query for testing."""
    return {
        "query": {
            "bool": {"filter": [{"range": {"@timestamp": {"gte": "now-1h"}}}, {"term": {"service": "test-service"}}]}
        },
        "aggs": {"errors_over_time": {"date_histogram": {"field": "@timestamp", "interval": "5m"}}},
    }


@pytest.fixture
def sample_cloudwatch_metrics():
    """Sample CloudWatch metrics data."""
    return [{"MetricName": "TestMetric", "Value": 100, "Unit": "Count", "Timestamp": datetime.utcnow()}]
