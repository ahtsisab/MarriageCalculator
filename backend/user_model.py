"""
User authentication: name + 4-digit PIN.
PINs are stored as SHA-256 hashes — not cryptographically ideal for
passwords but perfectly adequate for a casual game tracker PIN.
"""

import hashlib
from database import get_connection, insert_returning


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode()).hexdigest()


def register(name: str, pin: str) -> dict:
    """Create a new user. Raises ValueError if name is taken."""
    name = name.strip()
    if not name:
        raise ValueError("Name is required.")
    if not pin.strip().isdigit() or len(pin.strip()) != 4:
        raise ValueError("PIN must be exactly 4 digits.")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE lower(name) = lower(%s)", (name,))
    if cur.fetchone():
        cur.close(); conn.close()
        raise ValueError(f"The name '{name}' is already taken. Choose another.")

    user = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO users (name, pin_hash) VALUES (%s, %s) RETURNING id, name, created_at",
        sql_sqlite="INSERT INTO users (name, pin_hash) VALUES (%s, %s)",
        params=(name, _hash_pin(pin)),
    )

    conn.commit()
    cur.close()
    conn.close()
    return {"id": user["id"], "name": user["name"]}


def login(name: str, pin: str) -> dict:
    """Verify name+PIN. Returns user dict or raises ValueError."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name FROM users WHERE lower(name) = lower(%s) AND pin_hash = %s",
        (name.strip(), _hash_pin(pin)),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise ValueError("Incorrect name or PIN.")
    return {"id": row["id"], "name": row["name"]}


def get_user(user_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None
