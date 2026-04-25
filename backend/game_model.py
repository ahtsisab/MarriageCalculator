"""
Game-level operations: create, list, fetch, share games and their players.
"""

import random
import string
from collections import defaultdict
from database import get_connection, DB_BACKEND, insert_returning


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bool_val(v: bool) -> bool | int:
    """Return the correct boolean representation for the active backend."""
    return v if DB_BACKEND == "postgres" else (1 if v else 0)


def _norm_player(p: dict) -> dict:
    """Normalise a player row: cast is_active to Python bool."""
    p["is_active"] = bool(p.get("is_active", 1))
    return p


def _gen_join_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=5))


def _unique_join_code(cur) -> str:
    for _ in range(10):
        code = _gen_join_code()
        cur.execute("SELECT id FROM games WHERE join_code = %s", (code,))
        if not cur.fetchone():
            return code
    raise RuntimeError("Could not generate a unique join code. Try again.")


# ── Game CRUD ─────────────────────────────────────────────────────────────────

def create_game(name: str, player_names: list[str], user_id: int | None = None,
                stake_per_point: float = 0.25, currency: str = "USD",
                allow_better_game: bool = False, penalty_seen: int = 3,
                penalty_unseen: int = 10) -> dict:
    if not 3 <= len(player_names) <= 6:
        raise ValueError("A game must start with between 3 and 6 players.")
    seen = set()
    for n in player_names:
        key = n.strip().lower()
        if key in seen:
            raise ValueError(f"Duplicate player name: '{n.strip()}'. All player names must be unique.")
        seen.add(key)

    conn = get_connection()
    cur  = conn.cursor()

    join_code = _unique_join_code(cur)
    game = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO games (name, user_id, stake_per_point, currency, allow_better_game, penalty_seen, penalty_unseen, join_code) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, name, created_at, user_id, join_code, stake_per_point, currency, allow_better_game, penalty_seen, penalty_unseen",
        sql_sqlite="INSERT INTO games (name, user_id, stake_per_point, currency, allow_better_game, penalty_seen, penalty_unseen, join_code) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        params=(name, user_id, stake_per_point, currency, _bool_val(allow_better_game), penalty_seen, penalty_unseen, join_code),
    )
    game["players"]  = []
    game["is_owner"] = True

    for i, pname in enumerate(player_names):
        player = insert_returning(
            cur, conn,
            sql_pg="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s) RETURNING id, name, position, is_active",
            sql_sqlite="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s)",
            params=(game["id"], pname.strip(), i),
        )
        game["players"].append(_norm_player(player))

    conn.commit()
    cur.close()
    conn.close()
    return game


def resume_game(game_id: int) -> None:
    """Mark a game as active again (is_active = True)."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("UPDATE games SET is_active = %s WHERE id = %s", (_bool_val(True), game_id))
    conn.commit()
    cur.close(); conn.close()


def end_game(game_id: int) -> None:
    """Mark a game as ended (is_active = False)."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("UPDATE games SET is_active = %s WHERE id = %s", (_bool_val(False), game_id))
    conn.commit()
    cur.close(); conn.close()


def delete_game(game_id: int) -> None:
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    conn.commit()
    cur.close()
    conn.close()


def list_games(user_id: int | None = None) -> list[dict]:
    """Return all games visible to user_id (owned + joined), most recent first."""
    conn = get_connection()
    cur  = conn.cursor()

    if user_id is not None:
        cur.execute("""
            SELECT g.id, g.name, g.created_at, g.is_active, g.user_id, g.join_code,
                   COUNT(h.id) AS hand_count,
                   CASE WHEN g.user_id = %s THEN 1 ELSE 0 END AS is_owner
            FROM games g
            LEFT JOIN game_members gm ON gm.game_id = g.id AND gm.user_id = %s
            LEFT JOIN hands h ON h.game_id = g.id
            WHERE g.user_id = %s OR gm.user_id = %s
            GROUP BY g.id
            ORDER BY g.created_at DESC
        """, (user_id, user_id, user_id, user_id))
    else:
        cur.execute("""
            SELECT g.id, g.name, g.created_at, g.is_active, g.user_id, g.join_code,
                   COUNT(h.id) AS hand_count, 1 AS is_owner
            FROM games g
            LEFT JOIN hands h ON h.game_id = g.id
            GROUP BY g.id
            ORDER BY g.created_at DESC
        """)

    games = []
    for r in cur.fetchall():
        g = dict(r)
        g["is_owner"]  = bool(g["is_owner"])
        g["is_active"] = bool(g.get("is_active", True))
        games.append(g)

    cur.close()
    conn.close()
    return games


def get_game(game_id: int) -> dict | None:
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute(
        "SELECT id, name, created_at, is_active, user_id, join_code, stake_per_point, currency, allow_better_game, penalty_seen, penalty_unseen FROM games WHERE id = %s",
        (game_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return None

    game = dict(row)
    game["is_active"]        = bool(game["is_active"])
    game["allow_better_game"] = bool(game.get("allow_better_game", False))
    game["penalty_seen"]      = int(game.get("penalty_seen", 3))
    game["penalty_unseen"]    = int(game.get("penalty_unseen", 10))

    cur.execute(
        "SELECT id, name, position, is_active FROM players WHERE game_id = %s ORDER BY position",
        (game_id,),
    )
    game["players"] = [_norm_player(dict(r)) for r in cur.fetchall()]

    cur.close()
    conn.close()
    return game


def get_scoreboard(game_id: int) -> dict:
    conn = get_connection()
    cur  = conn.cursor()

    # Single query: join hands → hand_entries → players, ordered for easy grouping
    cur.execute("""
        SELECT h.id AS hand_id, h.hand_number, h.better_game, h.played_at,
               he.player_id, he.status, he.maal, he.points, he.is_winner,
               p.name AS player_name, p.position
        FROM hands h
        LEFT JOIN hand_entries he ON he.hand_id = h.id
        LEFT JOIN players p      ON p.id = he.player_id
        WHERE h.game_id = %s
        ORDER BY h.hand_number, p.position
    """, (game_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    hands_map: dict = {}
    totals: dict    = defaultdict(int)

    for r in rows:
        r = dict(r)
        hid = r["hand_id"]
        if hid not in hands_map:
            hands_map[hid] = {
                "id":          hid,
                "hand_number": r["hand_number"],
                "better_game": bool(r["better_game"]),
                "played_at":   r["played_at"],
                "entries":     [],
            }
        if r["player_id"] is not None:
            entry = {
                "hand_id":     hid,
                "player_id":   r["player_id"],
                "player_name": r["player_name"],
                "position":    r["position"],
                "status":      r["status"],
                "maal":        r["maal"],
                "points":      r["points"],
                "is_winner":   bool(r["is_winner"]),
            }
            hands_map[hid]["entries"].append(entry)
            totals[r["player_id"]] += r["points"]

    hands = sorted(hands_map.values(), key=lambda h: h["hand_number"])
    return {"hands": hands, "totals": dict(totals)}


# ── Sharing ───────────────────────────────────────────────────────────────────

def get_or_create_join_code(game_id: int) -> str:
    """Return the existing join code for a game, or generate and persist one."""
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("SELECT join_code FROM games WHERE id = %s", (game_id,))
    row = cur.fetchone()
    if row and row["join_code"]:
        code = row["join_code"]
        cur.close(); conn.close()
        return code

    code = _unique_join_code(cur)
    cur.execute("UPDATE games SET join_code = %s WHERE id = %s", (code, game_id))
    conn.commit()
    cur.close(); conn.close()
    return code


def join_game_by_code(code: str, user_id: int) -> dict:
    """Add user as a member of the game identified by code."""
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("SELECT id, user_id FROM games WHERE join_code = %s", (code.strip().upper(),))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise ValueError("No game found with that code. Check and try again.")

    if row["user_id"] == user_id:
        cur.close(); conn.close()
        raise ValueError("You already own this game — it's already in your list.")

    # Upsert membership — silently succeed if already a member
    try:
        cur.execute(
            "INSERT INTO game_members (game_id, user_id) VALUES (%s, %s)",
            (row["id"], user_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()  # Already a member — unique constraint violation

    cur.close(); conn.close()
    return get_game(row["id"])


def get_game_members(game_id: int) -> list[dict]:
    """Return users who have joined this game (excludes the owner)."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT u.id, u.name, gm.joined_at
        FROM game_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.game_id = %s
        ORDER BY gm.joined_at
    """, (game_id,))
    members = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    return members


def user_can_access(game: dict, user_id: int) -> bool:
    """True if user owns or is a member of this game."""
    if game.get("user_id") == user_id:
        return True
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "SELECT 1 FROM game_members WHERE game_id = %s AND user_id = %s",
        (game["id"], user_id),
    )
    result = cur.fetchone() is not None
    cur.close(); conn.close()
    return result


# ── Player CRUD ───────────────────────────────────────────────────────────────

def add_player(game_id: int, name: str) -> dict:
    name = name.strip()
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s AND is_active = %s",
        (game_id, _bool_val(True)),
    )
    # New players are added as active — cap active players at 6
    if cur.fetchone()["cnt"] >= 6:
        cur.close(); conn.close()
        raise ValueError("Cannot add player: already 6 active players. Deactivate one first.")

    cur.execute(
        "SELECT 1 FROM players WHERE game_id = %s AND lower(name) = lower(%s)",
        (game_id, name),
    )
    if cur.fetchone():
        cur.close(); conn.close()
        raise ValueError(f"A player named '{name}' is already in this game.")

    cur.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM players WHERE game_id = %s",
        (game_id,),
    )
    next_pos = cur.fetchone()["next_pos"]

    player = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s) RETURNING id, name, position, is_active",
        sql_sqlite="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s)",
        params=(game_id, name, next_pos),
    )
    conn.commit()
    cur.close()
    conn.close()
    return _norm_player(player)


def rename_player(player_id: int, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Player name cannot be empty.")
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("SELECT id, game_id FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise ValueError("Player not found.")

    cur.execute(
        "SELECT 1 FROM players WHERE game_id = %s AND lower(name) = lower(%s) AND id != %s",
        (row["game_id"], name, player_id),
    )
    if cur.fetchone():
        cur.close(); conn.close()
        raise ValueError(f"A player named '{name}' is already in this game.")

    cur.execute("UPDATE players SET name = %s WHERE id = %s", (name, player_id))
    conn.commit()
    cur.execute("SELECT id, name, position, is_active FROM players WHERE id = %s", (player_id,))
    player = dict(cur.fetchone())
    cur.close(); conn.close()
    return _norm_player(player)


def set_player_active(player_id: int, is_active: bool) -> dict:
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("SELECT game_id FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise ValueError("Player not found.")
    game_id = row["game_id"]

    if not is_active:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s AND is_active = %s AND id != %s",
            (game_id, _bool_val(True), player_id),
        )
        if cur.fetchone()["cnt"] < 3:
            cur.close(); conn.close()
            raise ValueError("Cannot deactivate: at least 3 active players required.")
    else:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s AND is_active = %s",
            (game_id, _bool_val(True)),
        )
        if cur.fetchone()["cnt"] >= 6:
            cur.close(); conn.close()
            raise ValueError("Cannot activate: already 6 active players. Deactivate one first.")

    cur.execute("UPDATE players SET is_active = %s WHERE id = %s", (_bool_val(is_active), player_id))
    conn.commit()
    cur.execute("SELECT id, name, position, is_active FROM players WHERE id = %s", (player_id,))
    player = dict(cur.fetchone())
    cur.close(); conn.close()
    return _norm_player(player)


def delete_player(player_id: int) -> None:
    """Delete a player only if they have never appeared in a hand."""
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("SELECT game_id FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise ValueError("Player not found.")
    game_id = row["game_id"]

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM hand_entries WHERE player_id = %s", (player_id,)
    )
    if cur.fetchone()["cnt"] > 0:
        cur.close(); conn.close()
        raise ValueError("Cannot delete a player who has already played hands.")

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s AND id != %s",
        (game_id, player_id),
    )
    if cur.fetchone()["cnt"] < 3:
        cur.close(); conn.close()
        raise ValueError("Cannot delete: game must always have at least 3 players.")

    cur.execute("DELETE FROM players WHERE id = %s", (player_id,))
    conn.commit()
    cur.close(); conn.close()
