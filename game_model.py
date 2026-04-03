"""
Game-level operations: create, list, fetch games and their players.
"""

from collections import defaultdict
from database import get_connection, insert_returning, where_in


def create_game(name: str, player_names: list[str]) -> dict:
    if not 3 <= len(player_names) <= 6:
        raise ValueError("A game requires between 3 and 6 players.")

    conn = get_connection()
    cur = conn.cursor()

    game = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO games (name) VALUES (%s) RETURNING id, name, created_at",
        sql_sqlite="INSERT INTO games (name) VALUES (%s)",
        params=(name,),
    )

    players = []
    for i, pname in enumerate(player_names):
        player = insert_returning(
            cur, conn,
            sql_pg="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s) RETURNING id, name, position, is_active",
            sql_sqlite="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s)",
            params=(game["id"], pname.strip(), i),
        )
        player["is_active"] = bool(player.get("is_active", 1))
        players.append(player)

    conn.commit()
    cur.close()
    conn.close()

    game["players"] = players
    return game


def add_player(game_id: int, name: str) -> dict:
    """Add a new player to an existing game."""
    conn = get_connection()
    cur = conn.cursor()

    # Next position = current max + 1
    cur.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM players WHERE game_id = %s",
        (game_id,),
    )
    next_pos = cur.fetchone()["next_pos"]

    # Total active player count after adding must not exceed 6
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s AND is_active = %s",
        (game_id, True if get_connection().__class__.__name__ != '_SQLiteConn' else 1),
    )
    # Re-query cleanly
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s",
        (game_id,),
    )
    total = cur.fetchone()["cnt"]
    if total >= 6:
        raise ValueError("A game cannot have more than 6 players.")

    player = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s) RETURNING id, name, position, is_active",
        sql_sqlite="INSERT INTO players (game_id, name, position) VALUES (%s, %s, %s)",
        params=(game_id, name.strip(), next_pos),
    )
    player["is_active"] = bool(player.get("is_active", 1))

    conn.commit()
    cur.close()
    conn.close()
    return player


def set_player_active(player_id: int, is_active: bool) -> dict:
    """Toggle a player's active status."""
    conn = get_connection()
    cur = conn.cursor()

    # Validate: can't deactivate if it would leave fewer than 3 active in the game
    cur.execute("SELECT game_id FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError("Player not found.")
    game_id = row["game_id"]

    if not is_active:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM players WHERE game_id = %s AND is_active = %s AND id != %s",
            (game_id, True if DB_BACKEND_IS_PG() else 1, player_id),
        )
        remaining = cur.fetchone()["cnt"]
        if remaining < 3:
            raise ValueError("Cannot deactivate: game must always have at least 3 active players.")

    val = True if DB_BACKEND_IS_PG() else 1
    if not is_active:
        val = False if DB_BACKEND_IS_PG() else 0

    cur.execute(
        "UPDATE players SET is_active = %s WHERE id = %s",
        (is_active if DB_BACKEND_IS_PG() else (1 if is_active else 0), player_id),
    )
    conn.commit()

    cur.execute("SELECT id, name, position, is_active FROM players WHERE id = %s", (player_id,))
    player = dict(cur.fetchone())
    player["is_active"] = bool(player["is_active"])

    cur.close()
    conn.close()
    return player


def DB_BACKEND_IS_PG():
    from database import DB_BACKEND
    return DB_BACKEND == "postgres"


def delete_game(game_id: int) -> None:
    """Permanently delete a game and all its data (cascades to players, hands, entries)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    conn.commit()
    cur.close()
    conn.close()


def list_games() -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT g.id, g.name, g.created_at, g.is_active,
               COUNT(h.id) AS hand_count
        FROM games g
        LEFT JOIN hands h ON h.game_id = g.id
        GROUP BY g.id
        ORDER BY g.created_at DESC
    """)
    games = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return games


def get_game(game_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name, created_at, is_active FROM games WHERE id = %s", (game_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return None
    game = dict(row)
    game["is_active"] = bool(game["is_active"])

    cur.execute(
        "SELECT id, name, position, is_active FROM players WHERE game_id = %s ORDER BY position",
        (game_id,),
    )
    players = [dict(r) for r in cur.fetchall()]
    for p in players:
        p["is_active"] = bool(p["is_active"])
    game["players"] = players

    cur.close()
    conn.close()
    return game


def get_scoreboard(game_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, hand_number, better_game, played_at FROM hands WHERE game_id = %s ORDER BY hand_number",
        (game_id,),
    )
    hands = [dict(r) for r in cur.fetchall()]
    for h in hands:
        h["better_game"] = bool(h["better_game"])

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
    else:
        entries = []

    cur.close()
    conn.close()

    entries_by_hand = defaultdict(list)
    for e in entries:
        e["is_winner"] = bool(e["is_winner"])
        entries_by_hand[e["hand_id"]].append(e)

    for h in hands:
        h["entries"] = entries_by_hand[h["id"]]

    totals = defaultdict(int)
    for e in entries:
        totals[e["player_id"]] += e["points"]

    return {"hands": hands, "totals": dict(totals)}
