"""Opt-in checks against a disposable localhost MySQL 8 runtime.

Set NUI_TEST_MYSQL_PORT to its mapped port. Start the runtime with:

    docker run --rm -d -p 127.0.0.1:33306:3306 \
        -e MYSQL_ROOT_PASSWORD=audit-pool-synthetic \
        -e MYSQL_DATABASE=audit_pool_test mysql:8.0

then, once the server accepts connections, run
NUI_TEST_MYSQL_PORT=33306 pytest tests/test_db_pool_mysql.py.
Install the dev extra first (`pip install -e .[dev]`): MySQL 8's default
caching_sha2_password authentication needs the `cryptography` package it provides.
No AWS or remote database credentials are used.
"""

import os
import uuid
from contextlib import contextmanager

import pymysql
import pytest

from nui_shared_utils import db_client

pytestmark = pytest.mark.integration


@pytest.fixture
def mysql_pool(monkeypatch):
    port = os.environ.get("NUI_TEST_MYSQL_PORT")
    if port is None:
        pytest.skip("Set NUI_TEST_MYSQL_PORT for the disposable localhost MySQL runtime")

    monkeypatch.setattr(db_client, "_connection_pool", {})
    client = db_client.DatabaseClient(
        credentials={
            "host": "127.0.0.1",
            "port": int(port),
            "username": "root",
            "password": "audit-pool-synthetic",
            "database": "audit_pool_test",
        }
    )
    table = "pool_lifecycle_" + uuid.uuid4().hex
    client.execute(f"CREATE TABLE `{table}` (value INT PRIMARY KEY) ENGINE=InnoDB")
    session_ids = []
    original_get_connection = client.get_connection

    @contextmanager
    def record_session(*args, **kwargs):
        with original_get_connection(*args, **kwargs) as connection:
            session_ids.append(connection.thread_id())
            yield connection

    monkeypatch.setattr(client, "get_connection", record_session)
    try:
        yield client, table, session_ids
    finally:
        try:
            client.execute(f"DROP TABLE `{table}`")
        finally:
            for entries in db_client._connection_pool.values():
                for entry in entries:
                    entry["connection"].close()
            db_client._connection_pool.clear()


@pytest.mark.parametrize("borrower_fails", [False, True])
def test_real_mysql_discards_uncommitted_work(mysql_pool, borrower_fails):
    client, table, session_ids = mysql_pool
    original = pymysql.OperationalError(1213, "Synthetic borrower failure")

    def borrow():
        with client.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO `{table}` (value) VALUES (%s)", (1,))
            if borrower_fails:
                raise original

    if borrower_fails:
        with pytest.raises(pymysql.OperationalError) as caught:
            borrow()
        assert caught.value is original
    else:
        borrow()

    initial_session = session_ids[-1]
    assert client.execute(f"INSERT INTO `{table}` (value) VALUES (%s)", (2,)) == 1
    assert session_ids[-1] == initial_session
    assert client.query(f"SELECT value FROM `{table}` ORDER BY value") == [{"value": 2}]


def test_real_mysql_partial_bulk_failure_rolls_back(mysql_pool):
    client, table, session_ids = mysql_pool
    with pytest.raises(pymysql.IntegrityError):
        client.bulk_insert(table, [{"value": 1}, {"value": 1}], batch_size=1)
    initial_session = session_ids[-1]
    assert client.execute(f"INSERT INTO `{table}` (value) VALUES (%s)", (2,)) == 1
    assert session_ids[-1] == initial_session
    assert client.query(f"SELECT value FROM `{table}` ORDER BY value") == [{"value": 2}]
