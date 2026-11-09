"""
Refactored Elasticsearch client using BaseClient for DRY code patterns.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Optional, Any, Tuple
from urllib.parse import urlparse
from elasticsearch import Elasticsearch
from elasticsearch.helpers import streaming_bulk as es_streaming_bulk

from .base_client import BaseClient, ServiceHealthMixin
from .utils import handle_client_errors
from .secrets_helper import _es_credentials_from_env, _es_host_with_port, _normalise_es_secret, _resolve_es_host

log = logging.getLogger(__name__)


class ElasticsearchClient(BaseClient, ServiceHealthMixin):
    """
    Refactored Elasticsearch client with standardized patterns.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        secret_name: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Initialize Elasticsearch client.

        Args:
            host: Override ES host
            secret_name: Override secret name
            credentials: Direct credentials dict (keys: username, password), bypasses Secrets Manager
            **kwargs: Additional ES client configuration
        """
        # Store host for later use in service client creation
        self._host_override = host
        super().__init__(secret_name=secret_name, credentials=credentials, **kwargs)

    def _get_default_config_prefix(self) -> str:
        """Return configuration prefix for Elasticsearch."""
        return "es"

    def _get_default_secret_name(self) -> str:
        """Return default secret name for ES credentials."""
        return "elasticsearch-credentials"

    def _resolve_credentials_from_env(self) -> Optional[Dict[str, Any]]:
        """Resolve Elasticsearch credentials from environment variables.

        Accepts both conventions: ES_PASSWORD/ES_USERNAME and ES_PASS/ES_USER.
        Password is required to trigger. Username defaults to "elastic".
        """
        return _es_credentials_from_env()

    def _fetch_credentials_from_sm(self, secret_name: Optional[str]) -> Dict[str, Any]:
        """Fetch from Secrets Manager and normalise to host, username, password."""
        return _normalise_es_secret(super()._fetch_credentials_from_sm(secret_name))

    def _create_service_client(self) -> Elasticsearch:
        """Create Elasticsearch client with resolved configuration."""
        # Host precedence: constructor arg > ES_HOST/ELASTICSEARCH_HOST > configure(es_host=...)
        # > credentials "host" (e.g. from the secret) > default. Shared with get_elasticsearch_credentials.
        if self._host_override is not None:
            host = self._host_override
        else:
            host = _resolve_es_host(self.credentials.get("host"))
        host = _es_host_with_port(host)

        parsed = urlparse(host)
        if parsed.scheme and parsed.netloc:  # Valid URL with scheme://netloc
            es_url = host
        else:
            scheme = self.client_config.get("scheme", "http")
            es_url = f"{scheme}://{host}"

        # Get credentials
        username = self.credentials.get("username", "elastic")
        password = self.credentials.get("password")
        
        if not password:
            raise ValueError("Elasticsearch credentials must include 'password'")

        # Create client with configuration
        return Elasticsearch(
            [es_url],
            basic_auth=(username, password),
            request_timeout=self.client_config.get("request_timeout", 30),
            max_retries=self.client_config.get("max_retries", 3),
            retry_on_timeout=self.client_config.get("retry_on_timeout", True),
        )

    @staticmethod
    def _body_with_size(body: Dict, size: int) -> Dict:
        """Return a copy of ``body`` carrying ``size``.

        ``size`` travels inside the request body rather than as a sibling keyword
        argument, which is the one call shape every supported client major accepts.
        On elasticsearch-py 8.x/9.x, passing ``body=`` alongside a body field as a
        keyword folds that keyword into the caller's dict (mutating it) and raises
        ``ValueError`` if the key is already there, so reusing a body dict for two
        searches breaks. A body that carries its own ``size`` is accepted unchanged
        by 7.17, 8.x and 9.x, and leaves the caller's dict alone.
        """
        return {**body, "size": size}

    @handle_client_errors(default_return=[])
    def search(self, index: str, body: Dict, size: int = 100) -> List[Dict]:
        """
        Execute search query with error handling.

        Args:
            index: Index pattern to search
            body: Elasticsearch query body
            size: Maximum results to return (overrides any ``size`` in ``body``)

        Returns:
            List of hit documents
        """
        def _search_operation():
            response = self._service_client.search(
                index=index,
                body=self._body_with_size(body, size),
                ignore_unavailable=True
            )
            return [hit["_source"] for hit in response["hits"]["hits"]]

        return self._execute_with_error_handling(
            "search",
            _search_operation,
            index=index,
            size=size
        )

    @handle_client_errors(default_return={})
    def aggregate(self, index: str, body: Dict) -> Dict[str, Any]:
        """
        Execute aggregation query with error handling.
        
        Args:
            index: Index pattern to search
            body: Elasticsearch query body with aggregations
            
        Returns:
            Aggregation results
        """
        def _aggregate_operation():
            response = self._service_client.search(
                index=index,
                body=self._body_with_size(body, 0),  # Only need aggregations
                ignore_unavailable=True
            )
            return response.get("aggregations", {})

        return self._execute_with_error_handling(
            "aggregate",
            _aggregate_operation,
            index=index
        )

    @handle_client_errors(default_return=0)
    def count(self, index: str, body: Optional[Dict[str, Any]] = None) -> int:
        """
        Count documents with error handling.
        
        Args:
            index: Index pattern to search
            body: Optional query body
            
        Returns:
            Document count
        """
        def _count_operation():
            response = self._service_client.count(
                index=index,
                body=body,
                ignore_unavailable=True
            )
            return response.get("count", 0)

        return self._execute_with_error_handling(
            "count",
            _count_operation,
            index=index
        )

    @handle_client_errors(default_return={})
    def get_service_stats(
        self,
        service: str,
        hours: int = 24,
        index_prefix: str = "logs"
    ) -> Dict[str, Any]:
        """
        Get comprehensive service statistics.
        
        Args:
            service: Service name
            hours: Time window to analyze
            index_prefix: Index prefix pattern
            
        Returns:
            Dictionary with service statistics
        """
        def _stats_operation():
            now = datetime.utcnow()
            start_time = now - timedelta(hours=hours)
            index = f"{index_prefix}-{service}-*"

            # Build comprehensive stats query
            body = {
                "query": {
                    "bool": {
                        "filter": [{
                            "range": {
                                "@timestamp": {
                                    "gte": start_time.isoformat(),
                                    "lte": now.isoformat()
                                }
                            }
                        }]
                    }
                },
                "aggs": {
                    "total": {
                        "cardinality": {"field": "request_id.keyword"}
                    },
                    "errors": {
                        "filter": {"range": {"response_code": {"gte": 400}}},
                        "aggs": {
                            "count": {
                                "cardinality": {"field": "request_id.keyword"}
                            }
                        }
                    },
                    "response_times": {
                        "percentiles": {
                            "field": "response_time",
                            "percents": [50, 95, 99]
                        }
                    }
                }
            }

            aggs = self.aggregate(index, body)

            total = aggs.get("total", {}).get("value", 0)
            errors = aggs.get("errors", {}).get("count", {}).get("value", 0)
            percentiles = aggs.get("response_times", {}).get("values", {})

            return {
                "service": service,
                "time_window_hours": hours,
                "total_count": total,
                "error_count": errors,
                "error_rate": (errors / total * 100) if total > 0 else 0,
                "p50_response_time": percentiles.get("50.0", 0),
                "p95_response_time": percentiles.get("95.0", 0),
                "p99_response_time": percentiles.get("99.0", 0),
            }

        return self._execute_with_error_handling(
            "get_service_stats",
            _stats_operation,
            service=service,
            hours=hours
        )

    @handle_client_errors(default_return=[])
    def get_recent_errors(
        self,
        service: str,
        hours: int = 1,
        limit: int = 10,
        index_prefix: str = "logs"
    ) -> List[Dict]:
        """
        Get recent error logs for a service.
        
        Args:
            service: Service name
            hours: Time window
            limit: Maximum number of errors
            index_prefix: Index prefix
            
        Returns:
            List of recent error documents
        """
        def _errors_operation():
            now = datetime.utcnow()
            start_time = now - timedelta(hours=hours)
            index = f"{index_prefix}-{service}-*"

            body = {
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "range": {
                                    "@timestamp": {
                                        "gte": start_time.isoformat(),
                                        "lte": now.isoformat()
                                    }
                                }
                            },
                            {
                                "range": {"response_code": {"gte": 400}}
                            }
                        ]
                    }
                },
                "sort": [{"@timestamp": {"order": "desc"}}]
            }

            return self.search(index, body, size=limit)

        return self._execute_with_error_handling(
            "get_recent_errors",
            _errors_operation,
            service=service,
            hours=hours,
            limit=limit
        )

    def _perform_health_check(self):
        """Perform Elasticsearch health check."""
        try:
            # Try to get cluster info
            info = self._service_client.info()
            if not info.get("version"):
                raise Exception("Elasticsearch info response invalid")
                
            # Check cluster health
            health = self._service_client.cluster.health()
            if health.get("status") == "red":
                raise Exception(f"Elasticsearch cluster is red: {health}")
                
        except Exception as e:
            raise Exception(f"Elasticsearch health check failed: {e}")

    def get_cluster_info(self) -> Dict:
        """
        Get Elasticsearch cluster information.
        
        Returns:
            Cluster info dictionary
        """
        try:
            info = self._service_client.info()
            health = self._service_client.cluster.health()
            
            return {
                "version": info.get("version", {}).get("number"),
                "cluster_name": info.get("cluster_name"),
                "cluster_status": health.get("status"),
                "number_of_nodes": health.get("number_of_nodes"),
                "number_of_data_nodes": health.get("number_of_data_nodes"),
                "active_primary_shards": health.get("active_primary_shards"),
                "active_shards": health.get("active_shards"),
            }
        except Exception as e:
            log.error(f"Failed to get cluster info: {e}")
            return {"error": str(e)}

    @handle_client_errors(default_return=[])
    def get_indices_info(self, pattern: str = "*") -> List[Dict]:
        """
        Get information about indices.

        Args:
            pattern: Index pattern to match

        Returns:
            List of index information dictionaries
        """
        def _indices_operation():
            response = self._service_client.cat.indices(
                index=pattern,
                format="json",
                h="index,health,status,docs.count,store.size"
            )
            # 8.x/9.x return a response wrapper around the JSON array; normalise to
            # a plain list so callers get the same type on every client major.
            return list(response) if response else []

        return self._execute_with_error_handling(
            "get_indices_info",
            _indices_operation,
            pattern=pattern
        )

    def streaming_bulk(
        self,
        actions: Iterator[Dict],
        chunk_size: int = 100,
        max_retries: int = 2,
        raise_on_error: bool = False,
        **kwargs
    ) -> Tuple[int, int]:
        """
        Stream documents to Elasticsearch with error handling.

        Wrapper around elasticsearch.helpers.streaming_bulk() that provides
        automatic error logging and returns success/failure counts.

        Args:
            actions: Iterator of action dictionaries. Each dict should have
                     '_index' and '_source' keys (and optionally '_id', '_op_type').
            chunk_size: Number of documents to send per batch (default: 100)
            max_retries: Number of retries for failed documents (default: 2)
            raise_on_error: If True, raise exception on first error (default: False)
            **kwargs: Additional arguments passed to streaming_bulk()

        Returns:
            Tuple of (success_count, failure_count)

        Example:
            def generate_docs():
                for item in items:
                    yield {
                        "_index": "my-index",
                        "_source": {"field": item.value}
                    }

            success, failed = client.streaming_bulk(generate_docs())
            print(f"Indexed {success} documents, {failed} failures")
        """
        success_count = 0
        failure_count = 0
        context = {"client_type": self.__class__.__name__, "operation": "streaming_bulk"}

        try:
            for ok, response in es_streaming_bulk(
                client=self._service_client,
                actions=actions,
                chunk_size=chunk_size,
                max_retries=max_retries,
                raise_on_error=raise_on_error,
                **kwargs
            ):
                if ok:
                    success_count += 1
                else:
                    failure_count += 1
                    action_type = list(response.keys())[0] if response else "unknown"
                    error_info = response.get(action_type, {})
                    log.error(
                        f"Bulk indexing failure: {action_type}",
                        extra={
                            **context,
                            "index": error_info.get("_index", "unknown"),
                            "error": error_info.get("error", "unknown"),
                        }
                    )
        except Exception as e:
            log.error(
                f"streaming_bulk failed: {e}",
                extra={**context, "error_type": type(e).__name__, "error_message": str(e)}
            )
            if raise_on_error:
                raise

        log.debug(
            f"streaming_bulk completed: {success_count} successful, {failure_count} failed",
            extra={**context, "success_count": success_count, "failure_count": failure_count}
        )
        return success_count, failure_count
