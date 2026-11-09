"""
Tests for db_client module.
"""

import os
import pytest

pytestmark = pytest.mark.unit
from unittest.mock import patch, Mock, MagicMock
import time
import pymysql
from nui_shared_utils.db_client import (
    DatabaseClient,
    _clean_expired_connections,
    get_pool_stats,
    safe_close_connection,
)


def create_mock_cursor_cm(mock_cursor):
    """Create a proper context manager mock for cursor."""
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__.return_value = mock_cursor
    mock_cursor_cm.__exit__.return_value = None
    return mock_cursor_cm


class TestDatabaseClient:
    """Tests for DatabaseClient class."""

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_init_default_values(self, mock_get_creds):
        """Test initialization with default values."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        client = DatabaseClient()

        assert client.credentials == mock_get_creds.return_value
        assert client.use_pool is True
        assert client.pool_size == 5
        assert client.pool_recycle == 3600
        assert client._pool_key == "db.example.com:3306"

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_init_custom_values(self, mock_get_creds):
        """Test initialization with custom values."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 5432,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        client = DatabaseClient(secret_name="custom-secret", use_pool=False, pool_size=10, pool_recycle=1800)

        mock_get_creds.assert_called_once_with("custom-secret")
        assert client.use_pool is False
        assert client.pool_size == 10
        assert client.pool_recycle == 1800


class TestGetConnection:
    """Tests for get_connection context manager."""

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch("nui_shared_utils.db_client.safe_close_connection")
    @patch("pymysql.connect")
    def test_get_connection_success(self, mock_connect, mock_safe_close, mock_get_creds):
        """Test successful connection creation."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }
        mock_conn = Mock()
        mock_connect.return_value = mock_conn

        # Test with pooling disabled to ensure connection is closed safely
        client = DatabaseClient(use_pool=False)

        with client.get_connection() as conn:
            assert conn == mock_conn

        mock_connect.assert_called_once_with(
            host="db.example.com",
            port=3306,
            user="dbuser",
            password="dbpass",
            database="testdb",
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=30,
        )
        mock_safe_close.assert_called_once_with(mock_conn)

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch("pymysql.connect")
    def test_get_connection_custom_database(self, mock_connect, mock_get_creds):
        """Test connection with custom database."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "defaultdb",
        }
        mock_conn = Mock()
        mock_connect.return_value = mock_conn

        client = DatabaseClient()

        with client.get_connection(database="customdb") as conn:
            assert conn == mock_conn

        # Should use custom database instead of default
        assert mock_connect.call_args[1]["database"] == "customdb"

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch("pymysql.connect")
    def test_get_connection_error_handling(self, mock_connect, mock_get_creds):
        """Test connection error handling."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }
        # PyMySQL OperationalError requires an errno and error message
        mock_connect.side_effect = pymysql.OperationalError(2003, "Can't connect to MySQL server")

        client = DatabaseClient()

        with pytest.raises(pymysql.OperationalError):
            with client.get_connection():
                pass  # Should not reach here


class TestQuery:
    """Tests for query method."""

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_query_success(self, mock_get_creds):
        """Test successful query execution."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [{"id": 1, "name": "Test 1"}, {"id": 2, "name": "Test 2"}]

        mock_conn = Mock()
        mock_conn.cursor.return_value = create_mock_cursor_cm(mock_cursor)

        client = DatabaseClient()

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            results = client.query("SELECT * FROM test_table")

        assert len(results) == 2
        assert results[0]["name"] == "Test 1"
        mock_cursor.execute.assert_called_once_with("SELECT * FROM test_table", None)

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_query_with_params(self, mock_get_creds):
        """Test query with parameters."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [{"id": 1, "status": "active"}]

        mock_conn = Mock()
        mock_conn.cursor.return_value = create_mock_cursor_cm(mock_cursor)

        client = DatabaseClient()

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            results = client.query("SELECT * FROM users WHERE status = %s AND age > %s", ("active", 18))

        mock_cursor.execute.assert_called_once_with(
            "SELECT * FROM users WHERE status = %s AND age > %s", ("active", 18)
        )

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_query_error_handling(self, mock_get_creds):
        """Test query error handling."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        client = DatabaseClient()

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.side_effect = Exception("Database error")

            results = client.query("SELECT * FROM test")

        assert results == []  # Should return empty list on error


class TestExecute:
    """Tests for execute method."""

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_execute_success(self, mock_get_creds):
        """Test successful execute operation."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        mock_cursor = Mock()
        mock_cursor.rowcount = 5

        mock_conn = Mock()
        mock_conn.cursor.return_value = create_mock_cursor_cm(mock_cursor)

        client = DatabaseClient()

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            affected = client.execute("UPDATE users SET status = %s", ("active",))

        assert affected == 5
        mock_cursor.execute.assert_called_once_with("UPDATE users SET status = %s", ("active",))
        mock_conn.commit.assert_called_once()

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_execute_error_raises(self, mock_get_creds):
        """Test execute error propagation."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        client = DatabaseClient()

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.side_effect = Exception("Database error")

            with pytest.raises(Exception) as exc_info:
                client.execute("DELETE FROM test")

            assert "Database error" in str(exc_info.value)


class TestBulkInsert:
    """Tests for bulk_insert method."""

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_bulk_insert_success(self, mock_get_creds):
        """Test successful bulk insert."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        mock_cursor = Mock()
        mock_cursor.rowcount = 3

        mock_conn = Mock()
        mock_conn.cursor.return_value = create_mock_cursor_cm(mock_cursor)

        client = DatabaseClient()

        records = [
            {"name": "User 1", "email": "user1@example.com"},
            {"name": "User 2", "email": "user2@example.com"},
            {"name": "User 3", "email": "user3@example.com"},
        ]

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            inserted = client.bulk_insert("users", records)

        assert inserted == 3

        # Verify SQL construction
        expected_sql = "INSERT INTO `users` (`name`, `email`) VALUES (%s, %s)"
        mock_cursor.executemany.assert_called_once()
        actual_sql = mock_cursor.executemany.call_args[0][0]
        assert actual_sql == expected_sql

        # Verify values
        expected_values = [
            ("User 1", "user1@example.com"),
            ("User 2", "user2@example.com"),
            ("User 3", "user3@example.com"),
        ]
        actual_values = mock_cursor.executemany.call_args[0][1]
        assert actual_values == expected_values

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_bulk_insert_batching(self, mock_get_creds):
        """Test bulk insert with batching."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        mock_cursor = Mock()
        mock_cursor.rowcount = 2  # 2 per batch

        mock_conn = Mock()
        mock_conn.cursor.return_value = create_mock_cursor_cm(mock_cursor)

        client = DatabaseClient()

        # Create 5 records to test batching with batch_size=2
        records = [{"id": i, "value": f"val{i}"} for i in range(5)]

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            inserted = client.bulk_insert("test_table", records, batch_size=2)

        # Should have 3 batches: 2, 2, 1
        assert mock_cursor.executemany.call_count == 3
        assert inserted == 6  # 2 + 2 + 2 (mocked rowcount)

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_bulk_insert_empty_records(self, mock_get_creds):
        """Test bulk insert with empty records."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        client = DatabaseClient()

        inserted = client.bulk_insert("users", [])

        assert inserted == 0

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_bulk_insert_ignore_duplicates(self, mock_get_creds):
        """Test bulk insert with ignore duplicates."""
        mock_get_creds.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "username": "dbuser",
            "password": "dbpass",
            "database": "testdb",
        }

        mock_cursor = Mock()
        mock_cursor.rowcount = 1

        mock_conn = Mock()
        mock_conn.cursor.return_value = create_mock_cursor_cm(mock_cursor)

        client = DatabaseClient()

        records = [{"id": 1}, {"id": 2}]

        with patch.object(client, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            client.bulk_insert("test", records, ignore_duplicates=True)

        # Should use INSERT IGNORE
        actual_sql = mock_cursor.executemany.call_args[0][0]
        assert actual_sql.startswith("INSERT IGNORE INTO")


class TestConnectionRecycling:
    """Tests for connection recycling functionality."""

    @patch("nui_shared_utils.db_client._connection_pool", {})
    @patch("nui_shared_utils.db_client.safe_close_connection")
    def test_clean_expired_connections_enabled(self, mock_safe_close):
        """Test that connections are recycled when they exceed pool_recycle time."""
        from nui_shared_utils.db_client import _connection_pool

        pool_key = "test_pool"
        current_time = time.time()

        # Manually add an old connection to the pool
        old_conn = Mock()
        fresh_conn = Mock()

        _connection_pool[pool_key] = [
            {"connection": old_conn, "timestamp": current_time - 3700},  # Old (> 1 hour)
            {"connection": fresh_conn, "timestamp": current_time - 1800},  # Fresh (30 min)
        ]

        # Clean expired connections with 1 hour recycle time
        _clean_expired_connections(pool_key, 3600)

        # Verify old connection was safely closed and removed, fresh one remains
        mock_safe_close.assert_called_once_with(old_conn)
        assert len(_connection_pool[pool_key]) == 1
        assert _connection_pool[pool_key][0]["connection"] == fresh_conn

    @patch("nui_shared_utils.db_client._connection_pool", {})
    def test_clean_expired_connections_disabled(self):
        """Test that recycling is disabled when pool_recycle is 0 or None."""
        from nui_shared_utils.db_client import _connection_pool

        pool_key = "test_pool"
        current_time = time.time()

        # Manually add an old connection to the pool
        old_conn = Mock()
        _connection_pool[pool_key] = [{"connection": old_conn, "timestamp": current_time - 7200}]  # 2 hours old

        # Clean expired connections with recycling disabled
        _clean_expired_connections(pool_key, 0)

        # Verify connection was NOT closed or removed
        old_conn.close.assert_not_called()
        assert len(_connection_pool[pool_key]) == 1

    @patch("nui_shared_utils.db_client._connection_pool", {})
    def test_pool_stats_with_timestamps(self):
        """Test that pool stats work correctly with timestamped connections."""
        from nui_shared_utils.db_client import _connection_pool

        pool_key = "test_pool"
        current_time = time.time()

        # Create mock connections with different ages
        fresh_conn = Mock()
        fresh_conn.ping.return_value = True
        aged_conn = Mock()
        aged_conn.ping.return_value = True

        # Add connections to pool with timestamps
        _connection_pool[pool_key] = [
            {"connection": fresh_conn, "timestamp": current_time - 1800},  # 30 min old
            {"connection": aged_conn, "timestamp": current_time - 5400},  # 90 min old (aged)
        ]

        stats = get_pool_stats()

        # Verify stats
        assert stats["total_pools"] == 1
        assert stats["pools"][pool_key]["active_connections"] == 2
        assert stats["pools"][pool_key]["healthy_connections"] == 2
        assert stats["pools"][pool_key]["aged_connections"] == 1  # One connection > 1 hour


class TestSafeCloseConnection:
    """Tests for safe connection closing functionality."""

    def test_safe_close_connection_success(self):
        """Test successful connection closing."""
        mock_conn = Mock()
        mock_conn._closed = False

        safe_close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_safe_close_connection_already_closed(self):
        """Test that already closed connections are not closed again."""
        mock_conn = Mock()
        mock_conn._closed = True

        safe_close_connection(mock_conn)

        mock_conn.close.assert_not_called()

    def test_safe_close_connection_with_exception(self):
        """Test that exceptions during close are handled gracefully."""
        mock_conn = Mock()
        mock_conn._closed = False
        mock_conn.close.side_effect = pymysql.MySQLError("Connection error")

        # Should not raise an exception
        safe_close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_safe_close_connection_none(self):
        """Test that None connections are handled gracefully."""
        # Should not raise an exception
        safe_close_connection(None)

    def test_safe_close_connection_no_close_method(self):
        """Test that objects without close method are handled gracefully."""
        not_a_connection = "not a connection"

        # Should not raise an exception
        safe_close_connection(not_a_connection)

    def test_safe_close_connection_open_flag_false(self):
        """Test that connections with open=False are not closed."""
        mock_conn = Mock()
        mock_conn._closed = False
        mock_conn.open = False  # Connection reports it's not open

        safe_close_connection(mock_conn)

        # Should not attempt to close if open=False
        mock_conn.close.assert_not_called()

    def test_safe_close_connection_open_flag_true(self):
        """Test that connections with open=True are closed normally."""
        mock_conn = Mock()
        mock_conn._closed = False
        mock_conn.open = True  # Connection reports it's open

        safe_close_connection(mock_conn)

        # Should attempt to close if open=True
        mock_conn.close.assert_called_once()

    def test_safe_close_connection_no_open_attribute(self):
        """Test that connections without open attribute still work."""
        mock_conn = Mock()
        mock_conn._closed = False
        # Don't set open attribute to simulate connections without it
        del mock_conn.open  # Remove the open attribute if it exists

        safe_close_connection(mock_conn)

        # Should still attempt to close
        mock_conn.close.assert_called_once()

    def test_safe_close_connection_oserror(self):
        """Test that OSError during close is handled gracefully."""
        mock_conn = Mock()
        mock_conn._closed = False
        mock_conn.close.side_effect = OSError("Network error")

        # Should not raise an exception
        safe_close_connection(mock_conn)

        mock_conn.close.assert_called_once()


class TestDatabaseCredentialResolution:
    """Tests for DatabaseClient credential resolution precedence."""

    @patch("nui_shared_utils.db_client.get_database_credentials")
    def test_explicit_credentials_bypass_secrets_manager(self, mock_get_creds):
        """Explicit credentials dict should skip SM entirely."""
        creds = {
            "host": "direct-host",
            "port": 3306,
            "username": "direct-user",
            "password": "direct-pass",
            "database": "direct-db",
        }
        client = DatabaseClient(credentials=creds)

        mock_get_creds.assert_not_called()
        assert client.credentials == creds
        assert client._pool_key == "direct-host:3306"

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch.dict("os.environ", {"DB_HOST": "env-host", "DB_PASSWORD": "env-pass"}, clear=False)
    def test_env_vars_bypass_secrets_manager(self, mock_get_creds):
        """DB_HOST + DB_PASSWORD env vars should skip SM."""
        # Clean other DB env vars
        os.environ.pop("DB_PORT", None)
        os.environ.pop("DB_USERNAME", None)
        os.environ.pop("DB_DATABASE", None)

        client = DatabaseClient()

        mock_get_creds.assert_not_called()
        assert client.credentials["host"] == "env-host"
        assert client.credentials["password"] == "env-pass"
        assert client.credentials["port"] == 3306  # default
        assert client.credentials["username"] == "root"  # default
        assert client.credentials["database"] == "app"  # default

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch.dict("os.environ", {
        "DB_HOST": "env-host",
        "DB_PASSWORD": "env-pass",
        "DB_PORT": "3307",
        "DB_USERNAME": "env-user",
        "DB_DATABASE": "env-db",
    }, clear=False)
    def test_env_vars_custom_values(self, _mock_get_creds):
        """All DB env vars should be picked up when set."""
        client = DatabaseClient()

        assert client.credentials["host"] == "env-host"
        assert client.credentials["port"] == 3307
        assert client.credentials["username"] == "env-user"
        assert client.credentials["password"] == "env-pass"
        assert client.credentials["database"] == "env-db"

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch.dict("os.environ", {"DB_HOST": "env-host", "DB_PASSWORD": "env-pass"}, clear=False)
    def test_explicit_credentials_win_over_env_vars(self, mock_get_creds):
        """Explicit credentials should take priority over env vars."""
        creds = {
            "host": "explicit-host",
            "port": 3306,
            "username": "explicit-user",
            "password": "explicit-pass",
            "database": "explicit-db",
        }
        client = DatabaseClient(credentials=creds)

        mock_get_creds.assert_not_called()
        assert client.credentials["host"] == "explicit-host"

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch.dict("os.environ", {}, clear=False)
    def test_falls_through_to_secrets_manager(self, mock_get_creds):
        """Without explicit creds or env vars, should use SM."""
        os.environ.pop("DB_HOST", None)
        os.environ.pop("DB_PASSWORD", None)
        mock_get_creds.return_value = {
            "host": "sm-host",
            "port": 3306,
            "username": "sm-user",
            "password": "sm-pass",
            "database": "sm-db",
        }

        client = DatabaseClient(secret_name="test-db-secret")

        mock_get_creds.assert_called_once_with("test-db-secret")
        assert client.credentials["host"] == "sm-host"

    @patch("nui_shared_utils.db_client.get_database_credentials")
    @patch.dict("os.environ", {"DB_HOST": "env-host"}, clear=False)
    def test_env_vars_require_both_host_and_password(self, mock_get_creds):
        """Only DB_HOST without DB_PASSWORD should fall through to SM."""
        os.environ.pop("DB_PASSWORD", None)
        mock_get_creds.return_value = {
            "host": "sm-host",
            "port": 3306,
            "username": "sm-user",
            "password": "sm-pass",
            "database": "sm-db",
        }

        DatabaseClient(secret_name="test-secret")

        # Should fall through to SM since DB_PASSWORD is missing
        mock_get_creds.assert_called_once()


_DB_ENV_VARS = ["DB_HOST", "DB_PASSWORD", "DB_PORT", "DB_USERNAME", "DB_DATABASE", "DB_CREDENTIALS_SECRET"]


class TestPostgreSQLCredentialResolution:
    """PostgreSQLClient resolves credentials with Postgres defaults on every tier."""

    @pytest.fixture(autouse=True)
    def _clean_db_env(self, monkeypatch):
        for name in _DB_ENV_VARS:
            monkeypatch.delenv(name, raising=False)

    def test_secret_without_port_gets_postgres_defaults(self):
        from nui_shared_utils.db_client import PostgreSQLClient

        secret = {"host": "pg-from-secret", "username": "u", "password": "p"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            client = PostgreSQLClient(secret_name="pg")

        assert client.credentials["host"] == "pg-from-secret"
        assert client.credentials["port"] == 5432
        assert client.credentials["database"] == "postgres"

    def test_env_vars_get_postgres_defaults(self, monkeypatch):
        from nui_shared_utils.db_client import PostgreSQLClient

        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_PASSWORD", "p")
        with patch("nui_shared_utils.secrets_helper.get_secret") as get_secret:
            client = PostgreSQLClient()

        get_secret.assert_not_called()
        assert client.credentials["port"] == 5432
        assert client.credentials["username"] == "postgres"
        assert client.credentials["database"] == "postgres"

    def test_secret_values_win_over_defaults(self):
        from nui_shared_utils.db_client import PostgreSQLClient

        secret = {"endpoint": "pg-host", "user": "u", "password": "p", "port": "6543", "dbname": "reports"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            client = PostgreSQLClient(secret_name="pg")

        assert client.credentials == {
            "host": "pg-host",
            "port": 6543,
            "username": "u",
            "password": "p",
            "database": "reports",
        }

    def test_auth_prefixed_secret_keys_still_preferred(self):
        from nui_shared_utils.db_client import PostgreSQLClient

        secret = {"host": "plain", "password": "p", "auth_host": "auth-host", "auth_password": "ap"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            client = PostgreSQLClient(secret_name="pg")

        assert client.credentials["host"] == "auth-host"
        assert client.credentials["port"] == 5432

    def test_auth_prefixed_keys_ignored_when_not_requested(self):
        from nui_shared_utils.db_client import PostgreSQLClient

        secret = {"host": "plain", "password": "p", "auth_host": "auth-host"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            client = PostgreSQLClient(secret_name="pg", use_auth_credentials=False)

        assert client.credentials["host"] == "plain"
        assert client.credentials["port"] == 5432
        assert client.credentials["database"] == "postgres"

    def test_invalid_db_port_env_raises(self, monkeypatch):
        from nui_shared_utils.db_client import PostgreSQLClient

        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_PASSWORD", "p")
        monkeypatch.setenv("DB_PORT", "not-a-port")
        with pytest.raises(ValueError, match="DB_PORT must be an integer"):
            PostgreSQLClient()

