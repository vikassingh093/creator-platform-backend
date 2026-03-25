import pymysql
import pymysql.cursors
from app.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_NAME,
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": False,
    "connect_timeout": 10,
}

def get_connection():
    return pymysql.connect(**DB_CONFIG)

@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB Error: {e}")
        raise
    finally:
        conn.close()

def execute_query(
    query: str,
    params: tuple = None,
    fetch_one: bool = False,
    fetch_all: bool = False,
    last_row_id: bool = False
):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            if fetch_one:
                return cursor.fetchone()
            if fetch_all:
                return cursor.fetchall()
            if last_row_id:
                return cursor.lastrowid
            return True

def execute_many(query: str, params_list: list):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(query, params_list)
            return cursor.rowcount