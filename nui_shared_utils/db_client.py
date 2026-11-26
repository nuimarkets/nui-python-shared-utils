"""
Refactored Database client using BaseClient for DRY code patterns.
"""

import time
import logging
import hmac
import json
import secrets
from typing import Dict, List, Optional, Any
from contextlib import contextmanager
import pymysql

from .base_client import BaseClient, ServiceHealthMixin
from .utils import handle_client_errors, safe_close_connection
from .secrets_helper import _db_credentials_from_env, _normalise_db_secret

# Optional PostgreSQL support
try:
    import psycopg2
    import psycopg2.extras

    HAS_POSTGRESQL = True
except ImportError:
    HAS_POSTGRESQL = False

log = logging.getLogger(__name__)

# Global connection pool for reuse across invocations
_connection_pool = {}
# A process-local HMAC keeps credentials out of observable pool identifiers.
_pool_identity_secret = secrets.token_bytes(32)


def get_pool_stats() -> Dict[str, Any]:
    """
    Get current connection pool statistics.

    Returns:
        Dictionary with pool status for monitoring
    """
    stats = {"total_pools": len(_connection_pool), "pools": {}}
    current_time = time.monotonic()

    for pool_key, connection_entries in _connection_pool.items():
        pool_stats = {
            "active_connections": len(connection_entries),
            "healthy_connections": 0,
            "aged_connections": 0,
        }

        # Test health of pooled connections
        healthy = 0
        aged = 0
        for entry in connection_entries:
            conn = entry["connection"]
            created_at = entry["created_at"]
            age = current_time - created_at

            try:
                conn.ping(reconnect=False)
                healthy += 1
            except Exception:
                pass  # Connection is unhealthy

            # Count connections older than 1 hour as aged
            if age > 3600:
                aged += 1

        pool_stats["healthy_connections"] = healthy
        pool_stats["aged_connections"] = aged
        stats["pools"][pool_key] = pool_stats

    return stats


def _snapshot_mysql_credentials(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Copy driver inputs to immutable values without caller-defined conversions."""
    snapshot = dict(credentials)
    snapshot.setdefault("port", 3306)
    snapshot.setdefault("database", "app")
    for field in ("host", "username", "password", "database"):
        if field not in snapshot:
            continue
        value = snapshot[field]
        if isinstance(value, str):
            snapshot[field] = str.__str__(value)
        elif isinstance(value, bytes):
            # Base slicing bypasses both conversion and Python-level buffer hooks.
            snapshot[field] = bytes.__getitem__(value, slice(None))
        elif isinstance(value, bytearray):
            snapshot[field] = bytes(bytearray.__getitem__(value, slice(None)))
        elif value is not None:
            raise TypeError(f"MySQL credential {field} must be a string, bytes, bytearray or None")
    port = snapshot["port"]
    if isinstance(port, int):
        snapshot["port"] = int.__int__(port)
    elif port is not None:
        raise TypeError("MySQL credential port must be an integer or None")
    return snapshot


class DatabaseClient(BaseClient, ServiceHealthMixin):
    """
    Refactored Database client with connection pooling and standardized patterns.
    """

    def __init__(
        self,
        secret_name: Optional[str] = None,
        use_pool: bool = True,
        pool_size: int = 5,
        pool_recycle: int = 3600,
        credentials: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Initialize database client.

        Args:
            secret_name: Override secret name
            use_pool: Enable connection pooling
            pool_size: Maximum pooled connections
            pool_recycle: Recycle connections after seconds
            credentials: Direct credentials dict (keys: host, port, username, password, database),
                bypasses Secrets Manager
            **kwargs: Additional configuration
        """
        self.use_pool = use_pool
        self.pool_size = pool_size
        self.pool_recycle = pool_recycle

        super().__init__(secret_name=secret_name, credentials=credentials, **kwargs)

        # Snapshot once so caller mutation cannot separate pool identity from settings.
        self.credentials = dict(self.credentials)
        self._connection_credentials = _snapshot_mysql_credentials(self.credentials)
        identity = (
            self._connection_credentials["host"],
            self._connection_credentials["port"],
            self._connection_credentials.get("username"),
            self._connection_credentials.get("password"),
            self._connection_credentials["database"],
            10,  # connect_timeout
            30,  # read_timeout
        )
        # Type tags and JSON field boundaries keep byte/string values unambiguous.
        encoded_identity = json.dumps(
            [(type(value).__name__, value.hex() if type(value) is bytes else value) for value in identity],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        self._pool_key = "mysql:" + hmac.digest(_pool_identity_secret, encoded_identity, "sha256").hex()

    def _get_default_config_prefix(self) -> str:
        """Return configuration prefix for database."""
        return "db"

    def _get_default_secret_name(self) -> str:
        """Return default secret name for DB credentials."""
        return "database-credentials"

    def _create_service_client(self) -> None:
        """Database client doesn't have a single service client - uses connections."""
        return None

    def _resolve_credentials_from_env(self) -> Optional[Dict[str, Any]]:
        """Resolve MySQL credentials from DB_* env vars (DB_HOST + DB_PASSWORD required)."""
        return _db_credentials_from_env("mysql")

    def _fetch_credentials_from_sm(self, secret_name: Optional[str]) -> Dict[str, Any]:
        """Fetch and normalize the secret after the base class selects the store tier."""
        from .secrets_helper import get_secret

        raw_creds = get_secret(self._resolve_secret_name(secret_name))
        return _normalise_db_secret(raw_creds, "mysql")

    def _clean_expired_connections(self, pool_key: str) -> None:
        """
        Clean expired connections from the pool.

        Args:
            pool_key: Pool identifier
        """
        if not self.pool_recycle or self.pool_recycle <= 0:
            return

        if pool_key not in _connection_pool:
            return

        current_time = time.monotonic()
        pool_entries = _connection_pool[pool_key]

        # Filter out expired connections
        active_entries = []
        expired_count = 0

        for entry in pool_entries:
            age = current_time - entry["created_at"]
            if age >= self.pool_recycle:
                safe_close_connection(entry["connection"])
                expired_count += 1
            else:
                active_entries.append(entry)

        _connection_pool[pool_key] = active_entries

        if expired_count > 0:
            log.debug(f"Cleaned {expired_count} expired connections from pool {pool_key}")

    @contextmanager
    def get_connection(self, database: Optional[str] = None):
        """
        Borrow a connection; commit explicitly to keep writes.

        Pooled returns roll back any remaining transaction before reuse. A
        database override uses a separate connection that closes on exit.
        """
        connection = None
        created_at = None
        pool_key = self._pool_key if self.use_pool and not database else None

        try:
            if pool_key:
                pool_entries = _connection_pool.get(pool_key, [])
                while pool_entries:
                    entry = pool_entries.pop()
                    conn = entry["connection"]
                    age = time.monotonic() - entry["created_at"]
                    if self.pool_recycle and self.pool_recycle > 0 and age >= self.pool_recycle:
                        safe_close_connection(conn)
                        log.debug(f"Recycled expired connection (age: {age:.1f}s) for {pool_key}")
                        continue

                    try:
                        conn.ping(reconnect=False)
                    except Exception:
                        safe_close_connection(conn)
                        log.debug(f"Closed dead pooled connection for {pool_key}")
                        continue

                    # A slow health check can cross the recycle boundary.
                    age = time.monotonic() - entry["created_at"]
                    if self.pool_recycle and self.pool_recycle > 0 and age >= self.pool_recycle:
                        safe_close_connection(conn)
                        log.debug(f"Recycled connection after checkout health check for {pool_key}")
                        continue

                    connection = conn
                    created_at = entry["created_at"]
                    log.debug(f"Reused pooled connection (age: {age:.1f}s) for {pool_key}")
                    break

            if connection is None:
                credentials = self._connection_credentials
                connection = pymysql.connect(
                    host=credentials["host"],
                    port=credentials["port"],
                    user=credentials["username"],
                    password=credentials["password"],
                    database=database or credentials["database"],
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    read_timeout=30,
                )
                created_at = time.monotonic()
                if pool_key:
                    log.debug(f"Created new pooled connection for {pool_key}")

            yield connection

        finally:
            if connection is not None:
                if pool_key:
                    try:
                        # SELECTs also hold a transaction snapshot. End it before reuse.
                        connection.rollback()
                        connection.ping(reconnect=False)
                        self._clean_expired_connections(pool_key)
                        age = time.monotonic() - created_at
                        expired = self.pool_recycle and self.pool_recycle > 0 and age >= self.pool_recycle
                        pool_entries = _connection_pool.setdefault(pool_key, [])
                        if not expired and len(pool_entries) < self.pool_size:
                            pool_entries.append({"connection": connection, "created_at": created_at})
                            log.debug(f"Returned connection to pool {pool_key} " f"(pool size: {len(pool_entries)})")
                        else:
                            safe_close_connection(connection)
                    except Exception:
                        # Cleanup must not replace an exception from the borrower.
                        safe_close_connection(connection)
                        log.debug(f"Connection cleanup failed; discarded connection for {pool_key}")
                else:
                    safe_close_connection(connection)

    @handle_client_errors(default_return=[])
    def query(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None) -> List[Dict]:
        """
        Execute a SELECT query with error handling.

        Args:
            sql: SQL query with %s placeholders
            params: Query parameters
            database: Override default database

        Returns:
            List of result rows as dictionaries
        """

        def _query_operation():
            with self.get_connection(database) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    return cursor.fetchall()

        return self._execute_with_error_handling(
            "query", _query_operation, sql=sql[:100], database=database  # First 100 chars for safety
        )

    @handle_client_errors(reraise=True)
    def execute(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None) -> int:
        """
        Execute an INSERT, UPDATE, or DELETE query with error handling.

        Args:
            sql: SQL query with %s placeholders
            params: Query parameters
            database: Override default database

        Returns:
            Number of affected rows
        """

        def _execute_operation():
            with self.get_connection(database) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    conn.commit()
                    return cursor.rowcount

        return self._execute_with_error_handling("execute", _execute_operation, sql=sql[:100], database=database)

    @handle_client_errors(reraise=True)
    def bulk_insert(
        self,
        table: str,
        records: List[Dict],
        database: Optional[str] = None,
        batch_size: int = 1000,
        ignore_duplicates: bool = False,
    ) -> int:
        """
        Bulk insert records with error handling.

        Args:
            table: Table name
            records: List of dictionaries to insert
            database: Override default database
            batch_size: Records per batch
            ignore_duplicates: Use INSERT IGNORE

        Returns:
            Total number of inserted rows
        """
        if not records:
            return 0

        def _bulk_insert_operation():
            # Prepare query
            columns = list(records[0].keys())
            placeholders = ", ".join(["%s"] * len(columns))
            columns_str = ", ".join(f"`{col}`" for col in columns)

            insert_cmd = "INSERT IGNORE" if ignore_duplicates else "INSERT"
            sql = f"{insert_cmd} INTO `{table}` ({columns_str}) VALUES ({placeholders})"

            total_inserted = 0

            with self.get_connection(database) as conn:
                with conn.cursor() as cursor:
                    # Process in batches
                    for i in range(0, len(records), batch_size):
                        batch = records[i : i + batch_size]
                        values = [tuple(record.get(col) for col in columns) for record in batch]

                        cursor.executemany(sql, values)
                        total_inserted += cursor.rowcount

                    conn.commit()

            log.info(f"Bulk inserted {total_inserted} rows into {table}")
            return total_inserted

        return self._execute_with_error_handling(
            "bulk_insert", _bulk_insert_operation, table=table, record_count=len(records), database=database
        )

    def _perform_health_check(self):
        """Perform database health check."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    if not result or result.get("1") != 1:
                        raise Exception("Database health check query failed")
        except Exception as e:
            raise Exception(f"Database health check failed: {e}")

    def get_connection_info(self) -> Dict:
        """
        Get database connection information.

        Returns:
            Dictionary with connection details
        """
        return {
            "host": self._connection_credentials["host"],
            "port": self._connection_credentials["port"],
            "database": self._connection_credentials["database"],
            "username": self._connection_credentials["username"],
            "pool_enabled": self.use_pool,
            "pool_size": self.pool_size,
            "pool_recycle_seconds": self.pool_recycle,
        }


class PostgreSQLClient(BaseClient, ServiceHealthMixin):
    """
    PostgreSQL client with connection management.
    """

    def __init__(
        self,
        secret_name: Optional[str] = None,
        use_auth_credentials: bool = True,
        credentials: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Initialize PostgreSQL client.

        Args:
            secret_name: Override secret name
            use_auth_credentials: Use auth-specific credentials
            credentials: Direct credentials dict (keys: host, port, username, password, database),
                bypasses Secrets Manager
            **kwargs: Additional configuration
        """
        if not HAS_POSTGRESQL:
            raise ImportError("psycopg2 is not installed. Install with: pip install psycopg2-binary")

        self.use_auth_credentials = use_auth_credentials
        super().__init__(secret_name=secret_name, credentials=credentials, **kwargs)

    def _get_default_config_prefix(self) -> str:
        """Return configuration prefix for PostgreSQL."""
        return "db"

    def _get_default_secret_name(self) -> str:
        """Return default secret name for PostgreSQL credentials."""
        return "database-credentials"

    def _create_service_client(self) -> None:
        """PostgreSQL client doesn't have a single service client - uses connections."""
        return None

    def _resolve_credentials_from_env(self) -> Optional[Dict[str, Any]]:
        """Resolve PostgreSQL credentials from DB_* env vars (DB_HOST + DB_PASSWORD required)."""
        return _db_credentials_from_env("postgresql")

    def _fetch_credentials_from_sm(self, secret_name: Optional[str]) -> Dict[str, Any]:
        """Fetch from Secrets Manager, preferring the auth_* keys when requested and present."""
        from .secrets_helper import get_secret

        resolved_secret_name = self._resolve_secret_name(secret_name)
        raw_creds = get_secret(resolved_secret_name)

        # Use auth-specific credentials if available and requested
        if self.use_auth_credentials and "auth_host" in raw_creds:
            return {
                "host": raw_creds["auth_host"],
                "port": int(raw_creds.get("auth_port", 5432)),
                "username": raw_creds.get("auth_username"),
                "password": raw_creds.get("auth_password"),
                "database": raw_creds.get("auth_database", "auth-service-db"),
            }
        return _normalise_db_secret(raw_creds, "postgresql")

    @contextmanager
    def get_connection(self, database: Optional[str] = None):
        """
        Context manager for PostgreSQL connections.

        Args:
            database: Override default database

        Yields:
            psycopg2 connection object
        """
        connection = None
        try:
            connect_params = {
                "host": self.credentials["host"],
                "port": self.credentials.get("port", 5432),
                "user": self.credentials["username"],
                "password": self.credentials["password"],
                "database": database or self.credentials.get("database", "postgres"),
                "connect_timeout": 5,
            }

            connection = psycopg2.connect(**connect_params)
            yield connection
        finally:
            if connection:
                connection.close()

    @handle_client_errors(default_return=[])
    def query(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None) -> List[Dict]:
        """
        Execute PostgreSQL SELECT query.

        Args:
            sql: SQL query with %s placeholders
            params: Query parameters
            database: Override default database

        Returns:
            List of result rows as dictionaries
        """

        def _query_operation():
            with self.get_connection(database) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                    cursor.execute(sql, params)
                    return [dict(row) for row in cursor.fetchall()]

        return self._execute_with_error_handling("query", _query_operation, sql=sql[:100], database=database)

    def _perform_health_check(self):
        """Perform PostgreSQL health check."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    if not result or result[0] != 1:
                        raise Exception("PostgreSQL health check query failed")
        except Exception as e:
            raise Exception(f"PostgreSQL health check failed: {e}")
