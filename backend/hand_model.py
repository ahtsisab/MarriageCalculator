"""
Hand-level operations: finalize a hand, compute points, retrieve history.
"""

from database import get_connection, insert_returning

STATUS_PENALTY = {"seen": 3, "unseen": 10, "duplee": 0}


def compute_points(entries: list[dict], better_game: bool = False) -> list[dict]:
    """
    Compute points for all entries. Only active players are included.

    Formula for non-winners:
        points = -1 * (total_maal + penalty(status) - maal * num_players)

    Winner's points = -1 * sum(non-winner points)
    If better_game is True, all points are doubled.
    Sum must equal 0.
    """
    n = len(entries)
    if n < 3 or n > 6:
        raise ValueError("A hand must have between 3 and 6 active players.")

    winners = [e for e in entries if e.get("is_winner")]
    if len(winners) != 1:
        raise ValueError("Exactly one winner must be designated.")

    winner_entry = winners[0]
    if winner_entry["status"] == "unseen":
        raise ValueError("The winner cannot have Unseen status.")

    # Unseen players have no maal
    for e in entries:
        if e["status"] == "unseen":
            e["maal"] = 0

    total_maal = sum(e["maal"] for e in entries)

    non_winner_total = 0
    for e in entries:
        if e["is_winner"]:
            continue
        pts = -1 * (total_maal + STATUS_PENALTY[e["status"]] - e["maal"] * n)
        e["points"] = pts
        non_winner_total += pts

    winner_entry["points"] = -1 * non_winner_total

    if better_game:
        for e in entries:
            e["points"] *= 2

    total_points = sum(e["points"] for e in entries)
    if total_points != 0:
        raise ValueError(
            f"Points do not sum to zero (got {total_points}). "
            "Please check the entry values."
        )

    return entries


def _fetch_hand_entries(cur, hand_id: int) -> list[dict]:
    cur.execute("""
        SELECT he.player_id, p.name AS player_name, p.position,
               he.status, he.maal, he.points, he.is_winner
        FROM hand_entries he
        JOIN players p ON p.id = he.player_id
        WHERE he.hand_id = %s
        ORDER BY p.position
    """, (hand_id,))
    entries = [dict(r) for r in cur.fetchall()]
    for e in entries:
        e["is_winner"] = bool(e["is_winner"])
    return entries


def finalize_hand(game_id: int, raw_entries: list[dict], better_game: bool = False) -> dict:
    """
    Validate, compute points, and persist a new hand.
    Only entries for active players should be passed in.

    raw_entries: list of dicts: player_id, status, maal, is_winner
    better_game: if True, all points are doubled
    """
    entries = compute_points(raw_entries, better_game=better_game)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT COALESCE(MAX(hand_number), 0) + 1 AS next_num FROM hands WHERE game_id = %s",
        (game_id,),
    )
    hand_number = cur.fetchone()["next_num"]

    from database import DB_BACKEND
    bg_val = better_game if DB_BACKEND == "postgres" else (1 if better_game else 0)

    hand = insert_returning(
        cur, conn,
        sql_pg="INSERT INTO hands (game_id, hand_number, better_game) VALUES (%s, %s, %s) RETURNING id, hand_number, better_game, played_at",
        sql_sqlite="INSERT INTO hands (game_id, hand_number, better_game) VALUES (%s, %s, %s)",
        params=(game_id, hand_number, bg_val),
    )
    hand["better_game"] = bool(hand.get("better_game", better_game))

    for e in entries:
        cur.execute("""
            INSERT INTO hand_entries (hand_id, player_id, status, maal, points, is_winner)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            hand["id"], e["player_id"], e["status"],
            e["maal"], e["points"], e["is_winner"],
        ))

    conn.commit()
    hand["entries"] = _fetch_hand_entries(cur, hand["id"])
    cur.close()
    conn.close()
    return hand


def get_hand(hand_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, game_id, hand_number, better_game, played_at FROM hands WHERE id = %s",
        (hand_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return None

    hand = dict(row)
    hand["better_game"] = bool(hand["better_game"])
    hand["entries"] = _fetch_hand_entries(cur, hand_id)
    cur.close()
    conn.close()
    return hand
