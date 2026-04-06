"""
User authentication: name + 4-digit PIN.
PINs are stored as SHA-256 hashes — adequate for a casual game tracker PIN.
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
    cur  = conn.cursor()

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


def change_pin(user_id: int, current_pin: str, new_pin: str) -> None:
    """Verify current PIN then update to new PIN. Raises ValueError on failure."""
    if not new_pin.strip().isdigit() or len(new_pin.strip()) != 4:
        raise ValueError("New PIN must be exactly 4 digits.")
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "SELECT id FROM users WHERE id = %s AND pin_hash = %s",
        (user_id, _hash_pin(current_pin)),
    )
    if not cur.fetchone():
        cur.close(); conn.close()
        raise ValueError("Current PIN is incorrect.")
    cur.execute(
        "UPDATE users SET pin_hash = %s WHERE id = %s",
        (_hash_pin(new_pin), user_id),
    )
    conn.commit()
    cur.close(); conn.close()
    """Verify name+PIN. Returns user dict or raises ValueError."""
    conn = get_connection()
    cur  = conn.cursor()

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
