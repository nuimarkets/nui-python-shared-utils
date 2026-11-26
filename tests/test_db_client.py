"""
Tests for db_client module.
"""

import os
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("clear_database_env")]
from unittest.mock import patch, Mock, MagicMock
import time
import pymysql
from nui_shared_utils.db_client import (
    DatabaseClient,
    PostgreSQLClient,
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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
        assert client._pool_key.startswith("mysql:")
        assert "dbpass" not in client._pool_key

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

            with pytest.raises(Exception, match="Database error") as exc_info:
                client.query("SELECT * FROM test")

        assert exc_info.value is mock_get_conn.side_effect


class TestExecute:
    """Tests for execute method."""

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @pytest.mark.parametrize("expired_age", [3600, 3700], ids=["at-recycle-limit", "past-recycle-limit"])
    @patch("nui_shared_utils.db_client.time.monotonic", return_value=10000)
    @patch("nui_shared_utils.db_client._connection_pool", {})
    @patch("nui_shared_utils.db_client.safe_close_connection")
    def test_clean_expired_connections_enabled(self, mock_safe_close, mock_time, expired_age):
        """Recycle connections at or beyond the limit, retaining younger entries."""
        from nui_shared_utils.db_client import _connection_pool

        client = DatabaseClient(credentials={"host": "db.example.com", "port": 3306}, pool_recycle=3600)
        pool_key = "test_pool"
        old_conn = Mock()
        fresh_conn = Mock()
        fresh_entry = {"connection": fresh_conn, "created_at": mock_time.return_value - 1800}
        _connection_pool[pool_key] = [
            {"connection": old_conn, "created_at": mock_time.return_value - expired_age},
            fresh_entry,
        ]

        client._clean_expired_connections(pool_key)

        mock_safe_close.assert_called_once_with(old_conn)
        assert _connection_pool[pool_key] == [fresh_entry]

    @patch("nui_shared_utils.db_client._connection_pool", {})
    def test_clean_expired_connections_disabled(self):
        """A zero recycle interval leaves old connections in the pool."""
        from nui_shared_utils.db_client import _connection_pool

        client = DatabaseClient(credentials={"host": "db.example.com", "port": 3306}, pool_recycle=0)
        pool_key = "test_pool"
        old_conn = Mock()
        old_entry = {"connection": old_conn, "created_at": time.monotonic() - 7200}
        _connection_pool[pool_key] = [old_entry]

        client._clean_expired_connections(pool_key)

        old_conn.close.assert_not_called()
        assert _connection_pool[pool_key] == [old_entry]

    @patch("nui_shared_utils.db_client._connection_pool", {})
    def test_pool_stats_with_timestamps(self):
        """Test that pool stats work correctly with timestamped connections."""
        from nui_shared_utils.db_client import _connection_pool

        pool_key = "test_pool"
        current_time = time.monotonic()

        # Create mock connections with different ages
        fresh_conn = Mock()
        fresh_conn.ping.return_value = True
        aged_conn = Mock()
        aged_conn.ping.return_value = True

        # Add connections to pool with timestamps
        _connection_pool[pool_key] = [
            {"connection": fresh_conn, "created_at": current_time - 1800},  # 30 min old
            {"connection": aged_conn, "created_at": current_time - 5400},  # 90 min old (aged)
        ]

        stats = get_pool_stats()

        # Verify stats
        assert stats["total_pools"] == 1
        assert stats["pools"][pool_key]["active_connections"] == 2
        assert stats["pools"][pool_key]["healthy_connections"] == 2
        assert stats["pools"][pool_key]["aged_connections"] == 1  # One connection > 1 hour


class _TransactionStore:
    """Minimal transactional driver double with shared committed rows."""

    def __init__(self):
        self.committed = []
        self.connections = []

    def connect(self, **settings):
        connection = _TransactionConnection(self, settings)
        self.connections.append(connection)
        return connection


class _TransactionConnection:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.pending = []
        self.snapshot = None
        self.events = []
        self.open = True
        self.rollback_error = None
        self.ping_error = None
        self.batch_calls = 0
        self.fail_batch = None

    def cursor(self):
        return _TransactionCursor(self)

    def commit(self):
        self.events.append("commit")
        self.store.committed.extend(self.pending)
        self.pending.clear()
        self.snapshot = None

    def rollback(self):
        self.events.append("rollback")
        if self.rollback_error:
            raise self.rollback_error
        self.pending.clear()
        self.snapshot = None

    def ping(self, reconnect=False):
        assert reconnect is False
        self.events.append("ping")
        if self.ping_error:
            raise self.ping_error
        if not self.open:
            raise pymysql.OperationalError(2006, "Connection closed")

    def close(self):
        self.events.append("close")
        self.open = False
        self.pending.clear()


class _TransactionCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("SELECT"):
            if self.connection.snapshot is None:
                self.connection.snapshot = list(self.connection.store.committed)
            self.rows = list(self.connection.snapshot)
        else:
            self.connection.pending.append(params[0])
        self.rowcount = 1

    def executemany(self, sql, values):
        self.connection.batch_calls += 1
        if self.connection.batch_calls == self.connection.fail_batch:
            raise pymysql.IntegrityError(1062, "Duplicate synthetic key")
        self.connection.pending.extend(row[0] for row in values)
        self.rowcount = len(values)

    def fetchall(self):
        return self.rows


class TestPoolSafety:
    @pytest.fixture(autouse=True)
    def isolated_pool(self, monkeypatch):
        import nui_shared_utils.db_client as module

        self.clock = 0.0
        self.store = _TransactionStore()
        self.credentials = {
            "host": "pool.example.com",
            "port": 3306,
            "username": "pool-user",
            "password": "synthetic-password",
            "database": "pool-db",
        }
        monkeypatch.setattr(module, "_connection_pool", {})
        monkeypatch.setattr(module.time, "monotonic", lambda: self.clock)
        monkeypatch.setattr(module.pymysql, "connect", self.store.connect)

    @pytest.mark.parametrize("borrower_fails", [False, True], ids=["normal-return", "exceptional-return"])
    def test_uncommitted_work_cannot_leak_into_next_commit(self, borrower_fails):
        client = DatabaseClient(credentials=self.credentials)
        original = pymysql.OperationalError(1213, "Synthetic borrower failure")

        def borrow():
            with client.get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE synthetic", ("abandoned",))
                if borrower_fails:
                    raise original

        if borrower_fails:
            with pytest.raises(pymysql.OperationalError) as caught:
                borrow()
            assert caught.value is original
        else:
            borrow()

        assert client.execute("UPDATE synthetic", ("intended",)) == 1
        assert self.store.committed == ["intended"]
        assert len(self.store.connections) == 1
        assert self.store.connections[0].events[:2] == ["rollback", "ping"]

    def test_select_only_borrow_ends_transaction_snapshot(self):
        client = DatabaseClient(credentials=self.credentials)
        self.store.committed.append({"value": "before"})
        assert client.query("SELECT synthetic") == [{"value": "before"}]
        self.store.committed.append({"value": "after"})
        assert client.query("SELECT synthetic") == [{"value": "before"}, {"value": "after"}]
        assert len(self.store.connections) == 1
        assert self.store.connections[0].events.count("rollback") == 2

    def test_partial_bulk_failure_cannot_be_committed_later(self):
        client = DatabaseClient(credentials=self.credentials)
        with client.get_connection() as connection:
            connection.fail_batch = 2
        with pytest.raises(pymysql.IntegrityError):
            client.bulk_insert("synthetic", [{"value": 1}, {"value": 2}], batch_size=1)
        assert client.execute("UPDATE synthetic", (3,)) == 1
        assert self.store.committed == [3]
        assert len(self.store.connections) == 1

    def test_explicit_commits_survive_pool_cleanup(self):
        client = DatabaseClient(credentials=self.credentials)
        assert client.execute("UPDATE synthetic", (1,)) == 1
        assert client.bulk_insert("synthetic", [{"value": 2}, {"value": 3}], batch_size=1) == 2
        assert self.store.committed == [1, 2, 3]
        assert len(self.store.connections) == 1

    def test_same_identity_clients_reuse_a_connection(self):
        first = DatabaseClient(credentials=self.credentials)
        second = DatabaseClient(credentials=dict(self.credentials))
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass
        assert one is two
        assert len(self.store.connections) == 1

    @pytest.mark.parametrize(
        ("field", "other"),
        [
            ("username", "other-user"),
            ("password", "rotated-password"),
            ("database", "other-db"),
            ("host", "other.example.com"),
            ("port", 3307),
        ],
    )
    def test_different_connection_settings_never_share(self, field, other):
        first = DatabaseClient(credentials=self.credentials)
        other_credentials = dict(self.credentials, **{field: other})
        second = DatabaseClient(credentials=other_credentials)
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass
        assert one is not two
        assert one.settings.get({"username": "user"}.get(field, field)) == self.credentials[field]
        assert two.settings.get({"username": "user"}.get(field, field)) == other

    def test_omitted_default_settings_share_with_explicit_defaults(self):
        self.credentials.pop("port")
        self.credentials.pop("database")
        first = DatabaseClient(credentials=self.credentials)
        second = DatabaseClient(credentials=dict(self.credentials, port=3306, database="app"))
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass
        assert one is two
        assert two.settings["port"] == 3306
        assert two.settings["database"] == "app"

    def test_caller_mutation_cannot_change_identity_or_connection_settings(self):
        original = dict(self.credentials)
        first = DatabaseClient(credentials=self.credentials)
        self.credentials.update(
            host="changed.example.com",
            port=3307,
            username="other-user",
            password="rotated-password",
            database="other-db",
        )
        second = DatabaseClient(credentials=original)
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass
        assert one is two
        assert one.settings["host"] == original["host"]
        assert one.settings["port"] == original["port"]
        assert one.settings["user"] == original["username"]
        assert one.settings["password"] == original["password"]
        assert one.settings["database"] == original["database"]

    @pytest.mark.parametrize("field", ["host", "username", "password", "database"])
    @pytest.mark.parametrize(
        "value_type", [str, bytes, bytearray], ids=["str-subclass", "bytes-subclass", "bytearray-subclass"]
    )
    def test_masked_credentials_use_immutable_underlying_values(self, field, value_type):
        class MaskedString(str):
            def __repr__(self):
                return "'<masked>'"

            def __str__(self):
                return "<masked>"

            def encode(self, *args, **kwargs):
                return b"<masked>"

        class MaskedBytes(bytes):
            def __repr__(self):
                return "b'<masked>'"

            def __bytes__(self):
                return b"<masked>"

            def __buffer__(self, flags):
                return memoryview(b"<masked>")

            def __getitem__(self, key):
                return b"<masked>"

        class MaskedBytearray(bytearray):
            def __repr__(self):
                return "bytearray(b'<masked>')"

            def __bytes__(self):
                return b"<masked>"

            def __buffer__(self, flags):
                return memoryview(b"<masked>")

            def __getitem__(self, key):
                return b"<masked>"

        masked_type = {str: MaskedString, bytes: MaskedBytes, bytearray: MaskedBytearray}[value_type]
        original = "first-value" if value_type is str else b"first-value"
        other = "second-value" if value_type is str else b"second-value"
        first = DatabaseClient(credentials=dict(self.credentials, **{field: masked_type(original)}))
        second = DatabaseClient(credentials=dict(self.credentials, **{field: masked_type(other)}))
        equivalent = DatabaseClient(credentials=dict(self.credentials, **{field: original}))
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass
        with equivalent.get_connection() as same:
            pass

        assert one is not two
        assert same is one
        driver_field = "user" if field == "username" else field
        assert type(one.settings[driver_field]) is (str if value_type is str else bytes)
        assert one.settings[driver_field] == original
        assert two.settings[driver_field] == other

    def test_masked_port_uses_immutable_underlying_integer(self):
        class MaskedPort(int):
            def __repr__(self):
                return "3306"

            def __int__(self):
                return 1

        first = DatabaseClient(credentials=dict(self.credentials, port=MaskedPort(3306)))
        second = DatabaseClient(credentials=dict(self.credentials, port=MaskedPort(3307)))
        equivalent = DatabaseClient(credentials=dict(self.credentials, port=3306))
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass
        with equivalent.get_connection() as same:
            pass

        assert one is not two
        assert same is one
        assert type(one.settings["port"]) is int
        assert one.settings["port"] == 3306
        assert two.settings["port"] == 3307

    @pytest.mark.parametrize(
        ("first_values", "second_values"),
        [
            ({"username": "user|part", "password": "rest"}, {"username": "user", "password": "part|rest"}),
            ({"password": bytes([1])}, {"password": "01"}),
        ],
        ids=["field-boundaries", "binary-versus-text"],
    )
    def test_serialized_identity_keeps_type_and_field_boundaries(self, first_values, second_values):
        first = DatabaseClient(credentials=dict(self.credentials, **first_values))
        second = DatabaseClient(credentials=dict(self.credentials, **second_values))
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            pass

        assert one is not two

    def test_mutable_password_is_frozen_before_identity_and_first_borrow(self):
        password = bytearray(b"original-password")
        first = DatabaseClient(credentials=dict(self.credentials, password=password))
        equivalent = DatabaseClient(credentials=dict(self.credentials, password=b"original-password"))
        password[:] = b"rotated-password"
        rotated = DatabaseClient(credentials=dict(self.credentials, password=password))
        with first.get_connection() as one:
            pass
        with equivalent.get_connection() as same:
            pass
        with rotated.get_connection() as two:
            pass

        assert one.settings["password"] == b"original-password"
        assert type(one.settings["password"]) is bytes
        assert same is one
        assert two is not one
        assert two.settings["password"] == b"rotated-password"

    def test_pool_diagnostics_do_not_expose_credentials(self, caplog):
        import logging

        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.db_client")
        client = DatabaseClient(credentials=self.credentials)
        with client.get_connection():
            pass
        diagnostics = repr(get_pool_stats()) + caplog.text + repr(client._pool_key)
        for private_value in (self.credentials["username"], self.credentials["password"]):
            assert private_value not in diagnostics

    def test_recycle_uses_creation_age_across_repeated_borrows(self):
        client = DatabaseClient(credentials=self.credentials, pool_recycle=3600)
        with client.get_connection() as first:
            pass
        self.clock = 3500
        with client.get_connection() as still_first:
            pass
        self.clock = 7000
        with client.get_connection() as replacement:
            pass
        assert first is still_first
        assert replacement is not first
        assert first.open is False
        assert len(self.store.connections) == 2

    @pytest.mark.parametrize("age_after_ping", [9, 10, 11])
    def test_checkout_rechecks_creation_age_after_health_ping(self, age_after_ping):
        client = DatabaseClient(credentials=self.credentials, pool_recycle=10)
        with client.get_connection() as first:
            pass
        self.clock = 9
        original_ping = first.ping

        def slow_ping(reconnect=False):
            original_ping(reconnect=reconnect)
            self.clock = age_after_ping

        first.ping = slow_ping
        with client.get_connection() as borrowed:
            if age_after_ping < 10:
                assert borrowed is first
            else:
                assert borrowed is not first
                assert first.open is False

    def test_connection_expiring_during_borrow_is_not_returned(self):
        client = DatabaseClient(credentials=self.credentials, pool_recycle=10)
        with client.get_connection() as first:
            self.clock = 10
        assert first.open is False
        with client.get_connection() as replacement:
            pass
        assert replacement is not first

    @pytest.mark.parametrize("borrower_fails", [False, True])
    def test_rollback_failure_discards_connection_and_preserves_borrower_error(self, borrower_fails):
        client = DatabaseClient(credentials=self.credentials)
        original = pymysql.OperationalError(1213, "Synthetic borrower failure")

        def borrow():
            with client.get_connection() as connection:
                connection.rollback_error = pymysql.OperationalError(2006, "Synthetic rollback failure")
                if borrower_fails:
                    raise original

        if borrower_fails:
            with pytest.raises(pymysql.OperationalError) as caught:
                borrow()
            assert caught.value is original
        else:
            borrow()
        assert self.store.connections[0].open is False
        with client.get_connection() as replacement:
            pass
        assert replacement is self.store.connections[1]

    def test_pool_limit_closes_excess_connection_after_rollback(self):
        client = DatabaseClient(credentials=self.credentials, pool_size=1)
        with client.get_connection() as outer:
            with client.get_connection() as inner:
                pass
        assert outer.open is False
        assert inner.open is True
        assert outer.events[:2] == ["rollback", "ping"]
        with client.get_connection() as reused:
            assert reused is inner

    def test_dead_connection_is_discarded_and_replaced(self):
        client = DatabaseClient(credentials=self.credentials)
        with client.get_connection() as first:
            pass
        first.ping_error = pymysql.OperationalError(2006, "Synthetic lost connection")
        with client.get_connection() as replacement:
            pass
        assert replacement is not first
        assert first.open is False

    def test_failed_health_check_on_return_discards_connection(self):
        client = DatabaseClient(credentials=self.credentials)
        with client.get_connection() as first:
            first.ping_error = pymysql.OperationalError(2006, "Synthetic lost connection")
        assert first.events == ["rollback", "ping", "close"]
        with client.get_connection() as replacement:
            pass
        assert replacement is not first

    @pytest.mark.parametrize("pool_enabled", [False, True])
    def test_database_override_bypasses_pool(self, pool_enabled):
        client = DatabaseClient(credentials=self.credentials, use_pool=pool_enabled)
        with client.get_connection(database="override-db") as first:
            pass
        with client.get_connection(database="override-db") as second:
            pass
        assert first is not second
        assert first.open is False
        assert second.open is False
        assert first.settings["database"] == "override-db"
        assert first.events == ["close"]


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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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
        assert client._pool_key.startswith("mysql:")
        assert "direct-pass" not in client._pool_key

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
    @patch.dict(
        "os.environ",
        {
            "DB_HOST": "env-host",
            "DB_PASSWORD": "env-pass",
            "DB_PORT": "3307",
            "DB_USERNAME": "env-user",
            "DB_DATABASE": "env-db",
        },
        clear=False,
    )
    def test_env_vars_custom_values(self, _mock_get_creds):
        """All DB env vars should be picked up when set."""
        client = DatabaseClient()

        assert client.credentials["host"] == "env-host"
        assert client.credentials["port"] == 3307
        assert client.credentials["username"] == "env-user"
        assert client.credentials["password"] == "env-pass"
        assert client.credentials["database"] == "env-db"

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @patch("nui_shared_utils.secrets_helper.get_secret")
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

    @pytest.mark.parametrize("host_key", ["host", "endpoint", "hostname"])
    def test_secrets_manager_hook_does_not_resolve_env_again(self, monkeypatch, host_key):
        from nui_shared_utils.secrets_helper import get_database_credentials

        client = DatabaseClient(credentials={"host": "initial-host", "port": 3306})
        monkeypatch.setenv("DB_HOST", "env-host")
        monkeypatch.setenv("DB_PASSWORD", "env-password")
        secret = {host_key: "secret-host", "user": "secret-user", "password": "secret-password"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=secret) as get_secret:
            result = client._fetch_credentials_from_sm("database-secret")
            assert result == {
                "host": "secret-host",
                "port": 3306,
                "username": "secret-user",
                "password": "secret-password",
                "database": "app",
            }
            get_secret.assert_called_once_with("database-secret")

            assert get_database_credentials("other-secret") == {
                "host": "env-host",
                "port": 3306,
                "username": "root",
                "password": "env-password",
                "database": "app",
            }
            get_secret.assert_called_once_with("database-secret")

    @patch("nui_shared_utils.secrets_helper.get_secret")
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


@pytest.fixture(autouse=True)
def isolated_connection_pool(monkeypatch):
    """Each test owns its pool without disturbing another test's connections."""
    pool = {}
    monkeypatch.setattr("nui_shared_utils.db_client._connection_pool", pool)
    return pool


@pytest.fixture
def db_credentials():
    return {"host": "db.example.com", "port": 3306, "username": "reader", "password": "test", "database": "app"}


@pytest.fixture(params=["mysql", "postgresql"])
def query_driver(request, db_credentials):
    """Patch the driver boundary while exercising actual connection context managers."""
    if request.param == "mysql":
        client = DatabaseClient(credentials=db_credentials, use_pool=False)
        target = "nui_shared_utils.db_client.pymysql.connect"
        errors = pymysql
    else:
        import psycopg2

        client = PostgreSQLClient(credentials={**db_credentials, "port": 5432})
        target = "nui_shared_utils.db_client.psycopg2.connect"
        errors = psycopg2
    conn = MagicMock()
    conn._closed = False
    cursor = conn.cursor.return_value.__enter__.return_value
    with patch(target, return_value=conn) as connect:
        yield client, connect, conn, cursor, errors


@pytest.mark.parametrize("failure_stage", ["connect", "execute", "fetchall"])
@pytest.mark.parametrize("error_name", ["OperationalError", "ProgrammingError", "InterfaceError"])
def test_query_propagates_original_driver_error(query_driver, failure_stage, error_name):
    client, connect, conn, cursor, errors = query_driver
    error = getattr(errors, error_name)("query failed")
    if failure_stage == "connect":
        connect.side_effect = error
    else:
        getattr(cursor, failure_stage).side_effect = error
    with pytest.raises(type(error)) as raised:
        client.query("SELECT id FROM users WHERE id = %s", (7,))
    assert raised.value is error
    if failure_stage == "connect":
        conn.close.assert_not_called()
    else:
        conn.cursor.return_value.__exit__.assert_called_once()
        conn.close.assert_called_once()


@pytest.mark.parametrize("rows", [[], [{"id": 7}]])
def test_query_preserves_rows_and_forwards_parameters(query_driver, rows):
    client, connect, conn, cursor, _ = query_driver
    cursor.fetchall.return_value = rows
    assert client.query("SELECT id FROM users WHERE id = %s", (7,), database="reports") == rows
    cursor.execute.assert_called_once_with("SELECT id FROM users WHERE id = %s", (7,))
    assert connect.call_args.kwargs["database"] == "reports"
    conn.close.assert_called_once()
    if isinstance(client, PostgreSQLClient):
        import psycopg2.extras

        conn.cursor.assert_called_once_with(cursor_factory=psycopg2.extras.DictCursor)


@pytest.mark.parametrize("rows, expected", [((), ()), (((7,),), [{"id": 7}])])
def test_mysql_query_preserves_real_dictcursor_results(db_credentials, rows, expected):
    """Process a stub server result through the real driver's row conversion."""
    from types import SimpleNamespace

    connection = pymysql.connections.Connection(defer_connect=True)
    connection._result = SimpleNamespace(
        affected_rows=len(rows),
        warning_count=0,
        description=(("id",),),
        insert_id=None,
        rows=rows,
        fields=[SimpleNamespace(name="id", table_name="users")],
        has_next=False,
    )
    cursor = connection.cursor(pymysql.cursors.DictCursor)
    cursor._do_get_result()
    cursor._executed = "SELECT id FROM users"
    client = DatabaseClient(credentials=db_credentials, use_pool=False)
    with patch("nui_shared_utils.db_client.pymysql.connect", return_value=connection):
        with patch.object(connection, "cursor", return_value=cursor), patch.object(cursor, "execute"):
            result = client.query("SELECT id FROM users")
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize("connect_timeout, read_timeout", [(10, 30), (0.25, 1.5), (31536000, None)])
def test_mysql_timeout_arguments(db_credentials, connect_timeout, read_timeout):
    client = DatabaseClient(
        credentials=db_credentials, use_pool=False, connect_timeout=connect_timeout, read_timeout=read_timeout
    )
    with patch("nui_shared_utils.db_client.pymysql.connect") as connect:
        with client.get_connection():
            pass
    assert connect.call_args.kwargs["connect_timeout"] == connect_timeout
    assert connect.call_args.kwargs["read_timeout"] == read_timeout


@pytest.mark.parametrize("setting", ["connect_timeout", "read_timeout"])
@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), -float("inf"), True, False, "3", object(), 10**400]
)
def test_mysql_invalid_timeouts_fail_before_external_work(setting, value):
    with patch("nui_shared_utils.secrets_helper.get_secret") as secret, patch("pymysql.connect") as connect:
        with pytest.raises(ValueError, match=setting):
            DatabaseClient(**{setting: value})
    secret.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize("value", [None, 31536001])
def test_mysql_invalid_connect_timeout_bounds(value):
    with patch("nui_shared_utils.secrets_helper.get_secret") as secret:
        with pytest.raises(ValueError, match="connect_timeout"):
            DatabaseClient(connect_timeout=value)
    secret.assert_not_called()


@pytest.mark.parametrize("value", [31536001, 1e10, 1e20])
def test_mysql_read_timeout_upper_bound(value):
    """Values PyMySQL would hand to socket.settimeout and overflow on fail at construction."""
    with patch("nui_shared_utils.secrets_helper.get_secret") as secret:
        with pytest.raises(ValueError, match="read_timeout must be <= 31536000"):
            DatabaseClient(read_timeout=value)
    secret.assert_not_called()


@pytest.mark.parametrize(
    "value", [None, 0, -1, True, False, "3", 1.0, 1.5, float("nan"), float("inf"), 2147483648, 10**400]
)
def test_postgresql_invalid_timeout_fails_before_external_work(value):
    with patch("nui_shared_utils.secrets_helper.get_secret") as secret, patch("psycopg2.connect") as connect:
        with pytest.raises(ValueError, match="connect_timeout"):
            PostgreSQLClient(connect_timeout=value)
    secret.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize(
    "timeout_kwargs, expected",
    [
        ({}, 5),
        ({"connect_timeout": 1}, 1),
        ({"connect_timeout": 12}, 12),
        ({"connect_timeout": 2147483647}, 2147483647),
    ],
)
def test_postgresql_timeout_arguments(db_credentials, timeout_kwargs, expected):
    client = PostgreSQLClient(credentials={**db_credentials, "port": 5432}, **timeout_kwargs)
    with patch("psycopg2.connect") as connect:
        with client.get_connection(database="reports"):
            pass
    assert connect.call_args.kwargs["connect_timeout"] == expected
    assert connect.call_args.kwargs["database"] == "reports"
    assert "read_timeout" not in connect.call_args.kwargs
    connect.return_value.close.assert_called_once()


@pytest.mark.parametrize("settings", [{"connect_timeout": 2}, {"read_timeout": 3}, {"read_timeout": None}])
def test_mysql_pool_separates_different_timeout_settings(db_credentials, isolated_connection_pool, settings):
    first = DatabaseClient(credentials=db_credentials)
    second = DatabaseClient(credentials=db_credentials, **settings)
    connections = [MagicMock(), MagicMock()]
    with patch("pymysql.connect", side_effect=connections) as connect:
        with first.get_connection() as one:
            assert one is connections[0]
        with second.get_connection() as two:
            assert two is connections[1]
        with first.get_connection() as reused:
            assert reused is one
        with second.get_connection() as reused:
            assert reused is two
    assert connect.call_count == 2
    assert len(isolated_connection_pool) == 2
    stats = get_pool_stats()
    assert all(isinstance(key, str) for key in stats["pools"])
    assert all(value["healthy_connections"] == 1 for value in stats["pools"].values())


def test_mysql_pool_reuses_equivalent_timeouts(db_credentials, isolated_connection_pool):
    first = DatabaseClient(credentials=db_credentials, connect_timeout=2, read_timeout=3)
    second = DatabaseClient(credentials=db_credentials, connect_timeout=2.0, read_timeout=3.0)
    with patch("pymysql.connect") as connect:
        with first.get_connection() as one:
            pass
        with second.get_connection() as two:
            assert two is one
    connect.assert_called_once()
    assert len(isolated_connection_pool) == 1
    one.ping.assert_any_call(reconnect=False)


def test_mysql_database_override_does_not_use_pool(db_credentials, isolated_connection_pool):
    client = DatabaseClient(credentials=db_credentials, connect_timeout=2, read_timeout=None)
    connections = [MagicMock(), MagicMock()]
    connections[1]._closed = False
    with patch("pymysql.connect", side_effect=connections) as connect:
        with client.get_connection() as pooled:
            pass
        with client.get_connection(database="reports") as override:
            assert override is not pooled
        with client.get_connection() as reused:
            assert reused is pooled
    assert connect.call_count == 2
    assert connect.call_args.kwargs["database"] == "reports"
    assert connect.call_args.kwargs["connect_timeout"] == 2
    assert connect.call_args.kwargs["read_timeout"] is None
    override.close.assert_called_once()
    assert len(isolated_connection_pool) == 1


@pytest.mark.parametrize("discard", ["expired", "dead"])
def test_mysql_timeout_pool_replaces_unusable_connection(db_credentials, discard):
    client = DatabaseClient(credentials=db_credentials, connect_timeout=2, read_timeout=3, pool_recycle=10)
    old, fresh = MagicMock(), MagicMock()
    old._closed = False
    with (
        patch("pymysql.connect", side_effect=[old, fresh]) as connect,
        patch("nui_shared_utils.db_client.time.monotonic") as clock,
    ):
        clock.return_value = 100
        with client.get_connection():
            pass
        if discard == "expired":
            clock.return_value = 110
        else:
            old.ping.side_effect = pymysql.OperationalError("connection lost")
        with client.get_connection() as replacement:
            assert replacement is fresh
    old.close.assert_called_once()
    assert connect.call_count == 2
    assert connect.call_args.kwargs["connect_timeout"] == 2
    assert connect.call_args.kwargs["read_timeout"] == 3


@pytest.mark.parametrize("unhealthy", [False, True])
def test_mysql_pool_closes_unreturned_connection(db_credentials, isolated_connection_pool, unhealthy):
    client = DatabaseClient(credentials=db_credentials, pool_size=1 if unhealthy else 0, read_timeout=None)
    conn = MagicMock()
    conn._closed = False
    if unhealthy:
        conn.ping.side_effect = pymysql.OperationalError("connection lost")
    with patch("pymysql.connect", return_value=conn):
        with client.get_connection():
            pass
    conn.close.assert_called_once()
    assert not any(isolated_connection_pool.values())


def test_mysql_query_failure_still_returns_healthy_connection_to_pool(db_credentials, isolated_connection_pool):
    client = DatabaseClient(credentials=db_credentials, connect_timeout=2, read_timeout=3)
    conn = MagicMock()
    conn._closed = False
    error = pymysql.ProgrammingError("invalid SQL")
    conn.cursor.return_value.__enter__.return_value.execute.side_effect = error
    with patch("pymysql.connect", return_value=conn):
        with pytest.raises(pymysql.ProgrammingError) as raised:
            client.query("invalid SQL")
    assert raised.value is error
    conn.cursor.return_value.__exit__.assert_called_once()
    conn.close.assert_not_called()
    assert next(iter(isolated_connection_pool.values()))[0]["connection"] is conn


def test_timeout_options_preserve_positional_credentials(db_credentials):
    mysql = DatabaseClient(None, False, 4, 90, db_credentials, connect_timeout=1, read_timeout=None)
    postgres = PostgreSQLClient(None, False, db_credentials, connect_timeout=1)
    # The MySQL client snapshots credentials; equality shows the positional mapping.
    assert mysql.credentials == db_credentials
    assert (mysql.use_pool, mysql.pool_size, mysql.pool_recycle) == (False, 4, 90)
    assert postgres.credentials is db_credentials
    assert postgres.use_auth_credentials is False


@pytest.mark.parametrize("connect_timeout, read_timeout", [(0.25, 1.5), (31536000, None)])
def test_mysql_timeout_values_are_accepted_by_real_driver(db_credentials, connect_timeout, read_timeout):
    client = DatabaseClient(credentials=db_credentials, connect_timeout=connect_timeout, read_timeout=read_timeout)
    connection = pymysql.connections.Connection(
        defer_connect=True,
        connect_timeout=client.connect_timeout,
        read_timeout=client.read_timeout,
    )
    assert connection.connect_timeout == connect_timeout
    assert connection._read_timeout == read_timeout
