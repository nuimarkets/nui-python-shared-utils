"""
Tests for es_client module.
"""

import os
import pytest

pytestmark = pytest.mark.unit
from datetime import datetime, timedelta
from unittest.mock import patch, Mock
from nui_shared_utils.es_client import ElasticsearchClient


class TestElasticsearchClient:
    """Tests for ElasticsearchClient class."""

    @patch.dict("os.environ", {}, clear=True)
    @patch("nui_shared_utils.secrets_helper.get_config")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_init_default_values(self, mock_es, mock_get_secret, mock_get_config):
        """Test initialization with default values."""
        # Pin the configured default host; the shared config singleton may carry another
        mock_get_config.return_value = Mock(es_host="localhost:9200")
        mock_get_secret.return_value = {"username": "elastic", "password": "test_password"}

        client = ElasticsearchClient()

        # Verify secret was retrieved
        mock_get_secret.assert_called_once_with("elasticsearch-credentials")
        mock_es.assert_called_once_with(
            ["http://localhost:9200"],
            basic_auth=("elastic", "test_password"),
            request_timeout=30,
            max_retries=3,
            retry_on_timeout=True,
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_init_custom_host(self, mock_es, mock_get_secret):
        """Test initialization with custom host."""
        mock_get_secret.return_value = {"username": "elastic", "password": "test_password"}

        client = ElasticsearchClient(host="10.0.0.1")

        # Verify custom host was used in client creation
        mock_es.assert_called_once_with(
            ["http://10.0.0.1:9200"],
            basic_auth=("elastic", "test_password"),
            request_timeout=30,
            max_retries=3,
            retry_on_timeout=True,
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_init_env_vars(self, mock_es, mock_get_secret):
        """Test initialization with environment variables."""
        mock_get_secret.return_value = {"username": "elastic", "password": "test_password"}

        with patch.dict("os.environ", {"ES_HOST": "es.example.com", "ES_CREDENTIALS_SECRET": "custom-es-secret"}):
            client = ElasticsearchClient()

        # Verify environment variable was used for host and secret
        mock_get_secret.assert_called_once_with("custom-es-secret")
        mock_es.assert_called_once_with(
            ["http://es.example.com:9200"],
            basic_auth=("elastic", "test_password"),
            request_timeout=30,
            max_retries=3,
            retry_on_timeout=True,
        )


class TestSearch:
    """Tests for search method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_search_success(self, mock_es, mock_get_secret):
        """Test successful search."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client

        mock_client.search.return_value = {
            "hits": {"hits": [{"_source": {"field1": "value1"}}, {"_source": {"field2": "value2"}}]}
        }

        client = ElasticsearchClient()
        results = client.search("test-index", {"query": {"match_all": {}}})

        assert len(results) == 2
        assert results[0] == {"field1": "value1"}
        assert results[1] == {"field2": "value2"}

        mock_client.search.assert_called_once_with(
            index="test-index", body={"query": {"match_all": {}}, "size": 100}, ignore_unavailable=True
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_search_custom_size(self, mock_es, mock_get_secret):
        """Test search with custom size parameter."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.search.return_value = {"hits": {"hits": []}}

        client = ElasticsearchClient()
        client.search("test-index", {}, size=500)

        mock_client.search.assert_called_once_with(index="test-index", body={"size": 500}, ignore_unavailable=True)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_search_does_not_mutate_caller_body(self, mock_es, mock_get_secret):
        """Search leaves the caller's body dict untouched so it can be reused."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.search.return_value = {"hits": {"hits": []}}

        body = {"query": {"match_all": {}}}

        client = ElasticsearchClient()
        client.search("test-index", body, size=10)
        client.search("test-index", body, size=20)

        assert body == {"query": {"match_all": {}}}
        assert mock_client.search.call_args_list[0][1]["body"]["size"] == 10
        assert mock_client.search.call_args_list[1][1]["body"]["size"] == 20

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_search_size_argument_wins_over_body_size(self, mock_es, mock_get_secret):
        """An explicit size argument overrides a size already in the body."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.search.return_value = {"hits": {"hits": []}}

        client = ElasticsearchClient()
        client.search("test-index", {"query": {"match_all": {}}, "size": 5}, size=50)

        mock_client.search.assert_called_once_with(
            index="test-index", body={"query": {"match_all": {}}, "size": 50}, ignore_unavailable=True
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_search_error_handling(self, mock_es, mock_get_secret):
        """Test search error handling."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.search.side_effect = Exception("Connection error")

        client = ElasticsearchClient()
        results = client.search("test-index", {})

        assert results == []  # Should return empty list on error


class TestAggregate:
    """Tests for aggregate method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_aggregate_success(self, mock_es, mock_get_secret):
        """Test successful aggregation."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client

        mock_client.search.return_value = {
            "aggregations": {"total_count": {"value": 100}, "avg_value": {"value": 50.5}}
        }

        client = ElasticsearchClient()
        aggs = client.aggregate(
            "test-index",
            {"aggs": {"total_count": {"sum": {"field": "count"}}, "avg_value": {"avg": {"field": "value"}}}},
        )

        assert aggs["total_count"]["value"] == 100
        assert aggs["avg_value"]["value"] == 50.5

        mock_client.search.assert_called_once_with(
            index="test-index",
            body={
                "aggs": {"total_count": {"sum": {"field": "count"}}, "avg_value": {"avg": {"field": "value"}}},
                "size": 0,
            },
            ignore_unavailable=True,
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_aggregate_error_handling(self, mock_es, mock_get_secret):
        """Test aggregation error handling."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.search.side_effect = Exception("Aggregation error")

        client = ElasticsearchClient()
        aggs = client.aggregate("test-index", {})

        assert aggs == {}  # Should return empty dict on error


class TestCount:
    """Tests for count method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_count_success(self, mock_es, mock_get_secret):
        """Test successful document count."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.count.return_value = {"count": 42}

        client = ElasticsearchClient()
        count = client.count("test-index")

        assert count == 42
        mock_client.count.assert_called_once_with(index="test-index", body=None, ignore_unavailable=True)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_count_with_query(self, mock_es, mock_get_secret):
        """Test count with query body."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.count.return_value = {"count": 10}

        query = {"query": {"match": {"status": "error"}}}

        client = ElasticsearchClient()
        count = client.count("test-index", query)

        assert count == 10
        mock_client.count.assert_called_once_with(index="test-index", body=query, ignore_unavailable=True)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_count_error_handling(self, mock_es, mock_get_secret):
        """Test count error handling."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_client.count.side_effect = Exception("Count error")

        client = ElasticsearchClient()
        count = client.count("test-index")

        assert count == 0  # Should return 0 on error


class TestGetServiceStats:
    """Tests for get_service_stats method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_get_service_stats_success(self, mock_es, mock_get_secret):
        """Test successful service stats retrieval."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client

        # Mock aggregation response
        mock_client.search.return_value = {
            "aggregations": {
                "total": {"value": 1000},
                "errors": {"count": {"value": 50}},
                "response_times": {"values": {"50.0": 100, "95.0": 500, "99.0": 1000}},
            }
        }

        client = ElasticsearchClient()
        stats = client.get_service_stats("order", hours=12)

        assert stats["total_count"] == 1000
        assert stats["error_count"] == 50
        assert stats["error_rate"] == 5.0  # 50/1000 * 100
        assert stats["p95_response_time"] == 500

        # Verify the search was called with correct parameters
        call_args = mock_client.search.call_args
        assert call_args[1]["index"] == "logs-order-*"
        assert call_args[1]["body"]["size"] == 0

        # Verify time range in query
        query = call_args[1]["body"]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        assert "gte" in query
        assert "lte" in query

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_get_service_stats_no_data(self, mock_es, mock_get_secret):
        """Test service stats with no data."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client

        # Mock empty aggregation response
        mock_client.search.return_value = {
            "aggregations": {"total": {"value": 0}, "errors": {"count": {"value": 0}}, "response_times": {"values": {}}}
        }

        client = ElasticsearchClient()
        stats = client.get_service_stats("auth")

        assert stats["total_count"] == 0
        assert stats["error_count"] == 0
        assert stats["error_rate"] == 0  # Should handle division by zero
        assert stats["p95_response_time"] == 0

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.datetime")
    def test_get_service_stats_time_window(self, mock_datetime, mock_es, mock_get_secret):
        """Test service stats with custom time window."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client

        # Mock datetime
        mock_now = datetime(2024, 1, 30, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now

        mock_client.search.return_value = {
            "aggregations": {
                "total": {"value": 100},
                "errors": {"count": {"value": 10}},
                "response_times": {"values": {"95.0": 200}},
            }
        }

        client = ElasticsearchClient()
        stats = client.get_service_stats("product", hours=48)

        # Verify time calculation
        call_args = mock_client.search.call_args
        query = call_args[1]["body"]["query"]["bool"]["filter"][0]["range"]["@timestamp"]

        # Should be 48 hours ago
        expected_start = (mock_now - timedelta(hours=48)).isoformat()
        assert query["gte"] == expected_start
        assert query["lte"] == mock_now.isoformat()


class TestStreamingBulk:
    """Tests for streaming_bulk method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_success(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test successful bulk indexing."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_es.return_value = Mock()

        # Simulate 3 successful documents
        mock_streaming_bulk.return_value = iter([
            (True, {"index": {"_index": "test-index", "_id": "1"}}),
            (True, {"index": {"_index": "test-index", "_id": "2"}}),
            (True, {"index": {"_index": "test-index", "_id": "3"}}),
        ])

        client = ElasticsearchClient()

        def generate_docs():
            for i in range(3):
                yield {"_index": "test-index", "_source": {"doc_id": i}}

        success, failed = client.streaming_bulk(generate_docs())

        assert success == 3
        assert failed == 0

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_partial_failures(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test bulk indexing with partial failures."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_es.return_value = Mock()

        # Simulate 2 successes and 1 failure
        mock_streaming_bulk.return_value = iter([
            (True, {"index": {"_index": "test-index", "_id": "1"}}),
            (False, {"index": {"_index": "test-index", "_id": "2", "error": {"type": "mapper_parsing_exception"}}}),
            (True, {"index": {"_index": "test-index", "_id": "3"}}),
        ])

        client = ElasticsearchClient()

        def generate_docs():
            for i in range(3):
                yield {"_index": "test-index", "_source": {"doc_id": i}}

        success, failed = client.streaming_bulk(generate_docs())

        assert success == 2
        assert failed == 1

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_all_failures(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test bulk indexing with all failures."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_es.return_value = Mock()

        # Simulate all failures
        mock_streaming_bulk.return_value = iter([
            (False, {"index": {"_index": "test-index", "_id": "1", "error": {"type": "validation_exception"}}}),
            (False, {"index": {"_index": "test-index", "_id": "2", "error": {"type": "validation_exception"}}}),
        ])

        client = ElasticsearchClient()

        def generate_docs():
            for i in range(2):
                yield {"_index": "test-index", "_source": {"doc_id": i}}

        success, failed = client.streaming_bulk(generate_docs())

        assert success == 0
        assert failed == 2

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_custom_chunk_size(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test streaming_bulk with custom chunk_size."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_client = Mock()
        mock_es.return_value = mock_client
        mock_streaming_bulk.return_value = iter([])

        client = ElasticsearchClient()
        client.streaming_bulk(iter([]), chunk_size=500, max_retries=5)

        # Verify parameters passed to streaming_bulk
        call_kwargs = mock_streaming_bulk.call_args[1]
        assert call_kwargs["chunk_size"] == 500
        assert call_kwargs["max_retries"] == 5

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_exception_no_raise(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test streaming_bulk handles exceptions gracefully when raise_on_error=False."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_es.return_value = Mock()
        mock_streaming_bulk.side_effect = Exception("Connection error")

        client = ElasticsearchClient()

        # Should not raise, return zeros
        success, failed = client.streaming_bulk(iter([]))

        assert success == 0
        assert failed == 0

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_exception_with_raise(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test streaming_bulk raises exception when raise_on_error=True."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_es.return_value = Mock()
        mock_streaming_bulk.side_effect = Exception("Connection error")

        client = ElasticsearchClient()

        with pytest.raises(Exception, match="Connection error"):
            client.streaming_bulk(iter([]), raise_on_error=True)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch("nui_shared_utils.es_client.es_streaming_bulk")
    def test_streaming_bulk_empty_iterator(self, mock_streaming_bulk, mock_es, mock_get_secret):
        """Test streaming_bulk with empty iterator."""
        mock_get_secret.return_value = {"username": "elastic", "password": "pass"}
        mock_es.return_value = Mock()
        mock_streaming_bulk.return_value = iter([])

        client = ElasticsearchClient()
        success, failed = client.streaming_bulk(iter([]))

        assert success == 0
        assert failed == 0


class TestElasticsearchCredentialResolution:
    """Tests for Elasticsearch credential resolution precedence."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    def test_explicit_credentials_bypass_secrets_manager(self, mock_es, mock_get_secret):
        """Explicit credentials dict should skip SM entirely."""
        creds = {"username": "admin", "password": "direct-pass"}
        client = ElasticsearchClient(credentials=creds)

        mock_get_secret.assert_not_called()
        assert client.credentials == creds

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch.dict("os.environ", {"ES_PASSWORD": "env-pass"}, clear=False)
    def test_env_var_bypasses_secrets_manager(self, mock_es, mock_get_secret):
        """ES_PASSWORD env var should skip SM."""
        # Ensure no SLACK/DB env vars leak in
        os.environ.pop("ES_USERNAME", None)

        client = ElasticsearchClient()

        mock_get_secret.assert_not_called()
        assert client.credentials["password"] == "env-pass"
        assert client.credentials["username"] == "elastic"  # default

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch.dict("os.environ", {"ES_PASSWORD": "env-pass", "ES_USERNAME": "custom-user"}, clear=False)
    def test_env_var_custom_username(self, mock_es, mock_get_secret):
        """ES_USERNAME should override the default when set."""
        client = ElasticsearchClient()

        mock_get_secret.assert_not_called()
        assert client.credentials["username"] == "custom-user"
        assert client.credentials["password"] == "env-pass"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch.dict("os.environ", {"ES_PASS": "short-pass", "ES_USER": "short-user"}, clear=True)
    def test_env_var_short_form(self, mock_es, mock_get_secret):
        """ES_PASS/ES_USER convention also works."""
        client = ElasticsearchClient()

        mock_get_secret.assert_not_called()
        assert client.credentials["password"] == "short-pass"
        assert client.credentials["username"] == "short-user"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch.dict("os.environ", {"ES_PASSWORD": "env-pass"}, clear=False)
    def test_explicit_credentials_win_over_env_vars(self, mock_es, mock_get_secret):
        """Explicit credentials should take priority over env vars."""
        creds = {"username": "explicit-user", "password": "explicit-pass"}
        client = ElasticsearchClient(credentials=creds)

        mock_get_secret.assert_not_called()
        assert client.credentials["password"] == "explicit-pass"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.es_client.Elasticsearch")
    @patch.dict("os.environ", {}, clear=False)
    def test_falls_through_to_secrets_manager(self, mock_es, mock_get_secret):
        """Without explicit creds or env vars, should use SM."""
        os.environ.pop("ES_PASSWORD", None)
        mock_get_secret.return_value = {"username": "elastic", "password": "sm-pass"}

        client = ElasticsearchClient(secret_name="test-es-secret")

        mock_get_secret.assert_called_once_with("test-es-secret")
        assert client.credentials["password"] == "sm-pass"


class TestElasticsearchHostResolution:
    """The client and get_elasticsearch_credentials pick the same host."""

    @pytest.fixture(autouse=True)
    def _clean_es_env(self, monkeypatch):
        for name in ["ES_HOST", "ELASTICSEARCH_HOST", "ES_PASSWORD", "ES_PASS", "ES_USERNAME", "ES_USER"]:
            monkeypatch.delenv(name, raising=False)

    def test_client_and_helper_agree_on_host_from_secret(self):
        from nui_shared_utils import secrets_helper

        secret = {"host": "es-from-secret:9200", "username": "elastic", "password": "pw"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            helper_host = secrets_helper.get_elasticsearch_credentials("es")["host"]
        with (
            patch("nui_shared_utils.base_client.get_secret", return_value=dict(secret)),
            patch("nui_shared_utils.es_client.Elasticsearch") as es,
        ):
            ElasticsearchClient(secret_name="es")
            client_url = es.call_args[0][0][0]

        assert helper_host == "es-from-secret:9200"
        assert client_url == "http://es-from-secret:9200"

    @staticmethod
    def _client_url(secret, **kwargs):
        with (
            patch("nui_shared_utils.base_client.get_secret", return_value=dict(secret)),
            patch("nui_shared_utils.es_client.Elasticsearch") as es,
        ):
            ElasticsearchClient(secret_name="es", **kwargs)
        return es.call_args[0][0][0]

    @staticmethod
    def _helper_host(secret):
        from nui_shared_utils import secrets_helper

        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            return secrets_helper.get_elasticsearch_credentials("es")["host"]

    @pytest.mark.parametrize("env_var", ["ES_HOST", "ELASTICSEARCH_HOST"])
    def test_env_host_beats_secret_host(self, monkeypatch, env_var):
        monkeypatch.setenv(env_var, "es-from-env:9200")
        secret = {"host": "es-from-secret:9200", "password": "pw"}

        assert self._client_url(secret) == "http://es-from-env:9200"
        assert self._helper_host(secret) == "es-from-env:9200"

    def test_host_argument_beats_env_and_secret(self, monkeypatch):
        monkeypatch.setenv("ES_HOST", "es-from-env:9200")
        secret = {"host": "es-from-secret:9200", "password": "pw"}

        assert self._client_url(secret, host="es-from-arg:9201") == "http://es-from-arg:9201"

    def test_secret_without_host_uses_configured_default(self):
        secret = {"password": "pw"}
        with patch("nui_shared_utils.secrets_helper.get_config", return_value=Mock(es_host="es-config:9200")):
            assert self._client_url(secret) == "http://es-config:9200"
            assert self._helper_host(secret) == "es-config:9200"

    def test_secret_host_with_scheme_is_used_as_is(self):
        secret = {"host": "https://es.example.com:443", "password": "pw"}

        assert self._client_url(secret) == "https://es.example.com:443"
        assert self._helper_host(secret) == "https://es.example.com:443"

    def test_secret_host_without_port_gets_default_port(self):
        secret = {"host": "es-from-secret", "password": "pw"}

        assert self._client_url(secret) == "http://es-from-secret:9200"
        assert self._helper_host(secret) == "es-from-secret:9200"

    def test_explicit_credentials_host_is_used(self):
        with patch("nui_shared_utils.es_client.Elasticsearch") as es:
            ElasticsearchClient(credentials={"host": "es-explicit:9200", "password": "pw"})

        assert es.call_args[0][0][0] == "http://es-explicit:9200"

    def test_env_credentials_never_read_secret_host(self, monkeypatch):
        monkeypatch.setenv("ES_PASSWORD", "env-pw")
        with (
            patch("nui_shared_utils.base_client.get_secret") as get_secret,
            patch("nui_shared_utils.secrets_helper.get_config", return_value=Mock(es_host="es-config:9200")),
            patch("nui_shared_utils.es_client.Elasticsearch") as es,
        ):
            ElasticsearchClient()

        get_secret.assert_not_called()
        assert es.call_args[0][0][0] == "http://es-config:9200"

