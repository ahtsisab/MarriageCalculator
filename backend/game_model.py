"""
Game-level operations: create, list, fetch, share games and their players.
"""

import random
import string
from collections import defaultdict
from database import get_connection, DB_BACKEND, insert_returning, where_in


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

def create_game(name: str, player_names: list[str], user_id: int | None = None) -> dict:
    if not 3 <= len(player_names) <= 6:
        raise ValueError("A game requires between 3 and 6 players.")
    seen = set()
    for n in player_names:
        key = n.strip().lower()
        if key in seen:
            raise ValueError(f"Duplicate player name: '{n.strip()}'. All player names must be unique.")
        seen.add(key)

    conn = get_connection()
    cur  = conn.cursor()

    game = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO games (name, user_id) VALUES (%s, %s) RETURNING id, name, created_at, user_id, join_code",
        sql_sqlite="INSERT INTO games (name, user_id) VALUES (%s, %s)",
        params=(name, user_id),
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
            LEFT JOIN hands h ON h.game_id = g.id
            WHERE g.user_id = %s
               OR g.id IN (SELECT game_id FROM game_members WHERE user_id = %s)
            GROUP BY g.id
            ORDER BY g.created_at DESC
        """, (user_id, user_id, user_id))
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
        "SELECT id, name, created_at, is_active, user_id, join_code FROM games WHERE id = %s",
        (game_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return None

    game = dict(row)
    game["is_active"] = bool(game["is_active"])

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

    cur.execute(
        "SELECT id, hand_number, better_game, played_at FROM hands WHERE game_id = %s ORDER BY hand_number",
        (game_id,),
    )
    hands = [dict(r) for r in cur.fetchall()]
    for h in hands:
        h["better_game"] = bool(h["better_game"])

    entries = []
    if hands:
        hand_ids = [h["id"] for h in hands]
        frag, params = where_in("he.hand_id", hand_ids)
        cur.execute(f"""
            SELECT he.hand_id, he.player_id, p.name AS player_name, p.position,
                   he.status, he.maal, he.points, he.is_winner
            FROM hand_entries he
            JOIN players p ON p.id = he.player_id
            WHERE {frag}
            ORDER BY he.hand_id, p.position
        """, params)
        entries = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    entries_by_hand: dict = defaultdict(list)
    totals: dict = defaultdict(int)
    for e in entries:
        e["is_winner"] = bool(e["is_winner"])
        entries_by_hand[e["hand_id"]].append(e)
        totals[e["player_id"]] += e["points"]

    for h in hands:
        h["entries"] = entries_by_hand[h["id"]]

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

    try:
        cur.execute(
            "INSERT INTO game_members (game_id, user_id) VALUES (%s, %s)",
            (row["id"], user_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()

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

    cur.execute("SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s", (game_id,))
    if cur.fetchone()["cnt"] >= 6:
        cur.close(); conn.close()
        raise ValueError("A game cannot have more than 6 players.")

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
        params=(game_id, name.strip(), next_pos),
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
