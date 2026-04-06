"""
Database connection and schema management for Marriage Calculator.

Auto-detection logic:
  1. If DB_BACKEND env var is set to "postgres" or "sqlite", use that.
  2. Otherwise try Postgres on localhost; fall back to SQLite if unavailable.
"""

import os
import sqlite3
from pathlib import Path

DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_NAME     = os.environ.get("DB_NAME", "marriage_calculator")
DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

SQLITE_PATH = os.environ.get(
    "SQLITE_PATH",
    str(Path(__file__).parent / "marriage_calculator.db"),
)

DB_BACKEND: str = "sqlite"


# ── Backend detection ─────────────────────────────────────────────────────────

def _try_postgres() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname="postgres",
            user=DB_USER, password=DB_PASSWORD, connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


def _detect_backend() -> str:
    forced = os.environ.get("DB_BACKEND", "").lower()
    print(f"DB_BACKEND={forced}")
    if forced in ("postgres", "postgresql"):
        return "postgres"
    if forced == "sqlite":
        return "sqlite"
    return "postgres" if _try_postgres() else "sqlite"


DB_BACKEND = _detect_backend()


# ── SQLite connection wrapper ─────────────────────────────────────────────────

class _SQLiteConn:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.autocommit = False

    def cursor(self):   return _SQLiteCursor(self._conn.cursor())
    def commit(self):   self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self):    self._conn.close()


class _SQLiteCursor:
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        self._cur.execute(sql, params)
        self.lastrowid = self._cur.lastrowid
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    def close(self): self._cur.close()


# ── Public helpers ────────────────────────────────────────────────────────────

def get_connection():
    if DB_BACKEND == "postgres":
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
            cursor_factory=RealDictCursor,
        )
    return _SQLiteConn(SQLITE_PATH)


def insert_returning(cur, conn, sql_pg: str, sql_sqlite: str, params: tuple) -> dict:
    if DB_BACKEND == "postgres":
        cur.execute(sql_pg, params)
        return dict(cur.fetchone())
    cur.execute(sql_sqlite, params)
    rowid = cur.lastrowid
    table = sql_sqlite.strip().split()[2]
    cur.execute(f"SELECT * FROM {table} WHERE rowid = %s", (rowid,))
    return dict(cur.fetchone())


def where_in(column: str, values: list):
    if DB_BACKEND == "postgres":
        return f"{column} = ANY(%s)", (values,)
    ph = ",".join(["%s"] * len(values))
    return f"{column} IN ({ph})", tuple(values)


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_PG = [
    """CREATE TABLE IF NOT EXISTS users (
        id         SERIAL PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        pin_hash   TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS games (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        join_code       TEXT UNIQUE,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        is_active       BOOLEAN DEFAULT TRUE,
        stake_per_point    NUMERIC(10,2) DEFAULT 0.25,
        currency           TEXT DEFAULT 'USD',
        allow_better_game  BOOLEAN DEFAULT FALSE,
        penalty_seen       INTEGER DEFAULT 3,
        penalty_unseen     INTEGER DEFAULT 10
    )""",
    """CREATE TABLE IF NOT EXISTS players (
        id        SERIAL PRIMARY KEY,
        game_id   INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        name      TEXT NOT NULL,
        position  INTEGER NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        UNIQUE(game_id, position)
    )""",
    """CREATE TABLE IF NOT EXISTS hands (
        id          SERIAL PRIMARY KEY,
        game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        hand_number INTEGER NOT NULL,
        better_game BOOLEAN DEFAULT FALSE,
        played_at   TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(game_id, hand_number)
    )""",
    """CREATE TABLE IF NOT EXISTS hand_entries (
        id        SERIAL PRIMARY KEY,
        hand_id   INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
        player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        status    TEXT NOT NULL CHECK (status IN ('seen','unseen','duplee')),
        maal      INTEGER NOT NULL DEFAULT 0,
        points    INTEGER NOT NULL DEFAULT 0,
        is_winner BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE(hand_id, player_id)
    )""",
    """CREATE TABLE IF NOT EXISTS game_members (
        game_id    INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at  TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (game_id, user_id)
    )""",
]

_SCHEMA_SQLITE = [
    """CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL UNIQUE,
        pin_hash   TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS games (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        join_code       TEXT UNIQUE,
        created_at      TEXT DEFAULT (datetime('now')),
        is_active       INTEGER DEFAULT 1,
        stake_per_point    REAL DEFAULT 0.25,
        currency           TEXT DEFAULT 'USD',
        allow_better_game  INTEGER DEFAULT 0,
        penalty_seen       INTEGER DEFAULT 3,
        penalty_unseen     INTEGER DEFAULT 10
    )""",
    """CREATE TABLE IF NOT EXISTS players (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id   INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        name      TEXT NOT NULL,
        position  INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1,
        UNIQUE(game_id, position)
    )""",
    """CREATE TABLE IF NOT EXISTS hands (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        hand_number INTEGER NOT NULL,
        better_game INTEGER DEFAULT 0,
        played_at   TEXT DEFAULT (datetime('now')),
        UNIQUE(game_id, hand_number)
    )""",
    """CREATE TABLE IF NOT EXISTS hand_entries (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        hand_id   INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
        player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        status    TEXT NOT NULL CHECK (status IN ('seen','unseen','duplee')),
        maal      INTEGER NOT NULL DEFAULT 0,
        points    INTEGER NOT NULL DEFAULT 0,
        is_winner INTEGER NOT NULL DEFAULT 0,
        UNIQUE(hand_id, player_id)
    )""",
    """CREATE TABLE IF NOT EXISTS game_members (
        game_id   INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (game_id, user_id)
    )""",
]

_MIGRATIONS_PG = [
    "ALTER TABLE players ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
    "ALTER TABLE hands   ADD COLUMN IF NOT EXISTS better_game BOOLEAN DEFAULT FALSE",
    "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, pin_hash TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS join_code TEXT UNIQUE",
    "CREATE TABLE IF NOT EXISTS game_members (game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY (game_id, user_id))",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS stake_per_point NUMERIC(10,2) DEFAULT 0.25",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'USD'",
    "UPDATE games SET stake_per_point = 0.25 WHERE stake_per_point IS NULL",
    "UPDATE games SET currency = 'USD' WHERE currency IS NULL",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS allow_better_game BOOLEAN DEFAULT FALSE",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS penalty_seen INTEGER DEFAULT 3",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS penalty_unseen INTEGER DEFAULT 10",
    "UPDATE games SET allow_better_game = FALSE WHERE allow_better_game IS NULL",
    "UPDATE games SET penalty_seen = 3 WHERE penalty_seen IS NULL",
    "UPDATE games SET penalty_unseen = 10 WHERE penalty_unseen IS NULL",
]

_MIGRATIONS_SQLITE = [
    "ALTER TABLE players ADD COLUMN is_active INTEGER DEFAULT 1",
    "ALTER TABLE hands   ADD COLUMN better_game INTEGER DEFAULT 0",
    "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, pin_hash TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')))",
    "ALTER TABLE games ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE games ADD COLUMN join_code TEXT UNIQUE",
    "CREATE TABLE IF NOT EXISTS game_members (game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (game_id, user_id))",
    "ALTER TABLE games ADD COLUMN stake_per_point REAL DEFAULT 0.25",
    "ALTER TABLE games ADD COLUMN currency TEXT DEFAULT 'USD'",
    "UPDATE games SET stake_per_point = 0.25 WHERE stake_per_point IS NULL",
    "UPDATE games SET currency = 'USD' WHERE currency IS NULL",
    "ALTER TABLE games ADD COLUMN allow_better_game INTEGER DEFAULT 0",
    "ALTER TABLE games ADD COLUMN penalty_seen INTEGER DEFAULT 3",
    "ALTER TABLE games ADD COLUMN penalty_unseen INTEGER DEFAULT 10",
    "UPDATE games SET allow_better_game = 0 WHERE allow_better_game IS NULL",
    "UPDATE games SET penalty_seen = 3 WHERE penalty_seen IS NULL",
    "UPDATE games SET penalty_unseen = 10 WHERE penalty_unseen IS NULL",
]


def _run_migrations(cur, conn):
    migrations = _MIGRATIONS_PG if DB_BACKEND == "postgres" else _MIGRATIONS_SQLITE
    for stmt in migrations:
        try:
            cur.execute(stmt)
            conn.commit()
        except Exception:
            if DB_BACKEND == "postgres":
                conn.rollback()


def _ensure_postgres_db():
    import psycopg2
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname="postgres",
        user=DB_USER, password=DB_PASSWORD,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{DB_NAME}"')
        print(f"[db] Created Postgres database '{DB_NAME}'.")
    else:
        print(f"[db] Postgres database '{DB_NAME}' already exists.")
    cur.close()
    conn.close()


def init_db():
    """Create DB + schema, then run any pending migrations."""
    schema = _SCHEMA_PG if DB_BACKEND == "postgres" else _SCHEMA_SQLITE

    if DB_BACKEND == "postgres":
        _ensure_postgres_db()
        print("[db] Using Postgres.")
    else:
        print(f"[db] Using SQLite at {SQLITE_PATH}")

    conn = get_connection()
    cur = conn.cursor()
    for stmt in schema:
        cur.execute(stmt)
    conn.commit()

    _run_migrations(cur, conn)

    cur.close()
    conn.close()
    print("[db] Schema ready.")
