"""
REST API routes for Marriage Calculator.
"""

from flask import Blueprint, request, jsonify, session
from game_model import (create_game, list_games, get_game, get_scoreboard,
                         add_player, set_player_active, delete_game,
                         get_or_create_join_code, join_game_by_code,
                         get_game_members, user_can_access)
from hand_model import finalize_hand, get_hand
from user_model import register, login

api = Blueprint("api", __name__, url_prefix="/api")


def _err(msg: str, status: int = 400):
    return jsonify({"error": msg}), status

def _uid() -> int | None:
    return session.get("user_id")

def _require_auth():
    if not _uid():
        return _err("Not logged in.", 401)

def _check_access(game):
    """Return error response if current user can't access game, else None."""
    uid = _uid()
    if game.get("user_id") and not user_can_access(game, uid):
        return _err("Access denied.", 403)

def _require_owner(game):
    """Return error response if current user is not the game owner."""
    if game.get("user_id") and game["user_id"] != _uid():
        return _err("Only the owner can modify this game.", 403)


# ── Auth ───────────────────────────────────────────────────────────────────────

@api.post("/auth/register")
def route_register():
    data = request.get_json(force=True)
    try:
        user = register(data.get("name", ""), data.get("pin", ""))
    except ValueError as e:
        return _err(str(e))
    session["user_id"]   = user["id"]
    session["user_name"] = user["name"]
    return jsonify(user), 201

@api.post("/auth/login")
def route_login():
    data = request.get_json(force=True)
    try:
        user = login(data.get("name", ""), data.get("pin", ""))
    except ValueError as e:
        return _err(str(e))
    session["user_id"]   = user["id"]
    session["user_name"] = user["name"]
    return jsonify(user)

@api.post("/auth/logout")
def route_logout():
    session.clear()
    return jsonify({"ok": True})

@api.get("/auth/me")
def route_me():
    uid = _uid()
    if not uid:
        return _err("Not logged in.", 401)
    return jsonify({"id": uid, "name": session.get("user_name")})


# ── Games ──────────────────────────────────────────────────────────────────────

@api.get("/games")
def route_list_games():
    if (e := _require_auth()): return e
    return jsonify(list_games(user_id=_uid()))

@api.post("/games")
def route_create_game():
    if (e := _require_auth()): return e
    data = request.get_json(force=True)
    name    = (data.get("name") or "").strip()
    players = data.get("players", [])
    if not name:
        return _err("Game name is required.")
    if not isinstance(players, list) or not all(isinstance(p, str) for p in players):
        return _err("players must be a list of strings.")
    try:
        game = create_game(name, players, user_id=_uid())
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(game), 201

@api.get("/games/<int:game_id>")
def route_get_game(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _check_access(game)): return e
    game["is_owner"] = (game.get("user_id") == _uid())
    return jsonify(game)

@api.delete("/games/<int:game_id>")
def route_delete_game(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if game.get("user_id") != _uid():
        return _err("Only the owner can delete this game.", 403)
    delete_game(game_id)
    return jsonify({"deleted": game_id})

@api.get("/games/<int:game_id>/scoreboard")
def route_scoreboard(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _check_access(game)): return e
    board = get_scoreboard(game_id)
    board["players"] = game["players"]
    board["game"]    = {"id": game["id"], "name": game["name"]}
    return jsonify(board)


# ── Sharing ────────────────────────────────────────────────────────────────────

@api.post("/games/<int:game_id>/share")
def route_share_game(game_id):
    """Generate (or return existing) join code for a game."""
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _check_access(game)): return e
    code    = get_or_create_join_code(game_id)
    members = get_game_members(game_id)
    return jsonify({"join_code": code, "members": members})

@api.post("/games/join")
def route_join_game():
    """Join a game using a code."""
    if (e := _require_auth()): return e
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip().upper()
    if not code:
        return _err("Join code is required.")
    try:
        game = join_game_by_code(code, _uid())
    except ValueError as exc:
        return _err(str(exc))
    game["is_owner"] = False
    return jsonify(game)

@api.get("/games/<int:game_id>/members")
def route_game_members(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _check_access(game)): return e
    return jsonify(get_game_members(game_id))


# ── Players ────────────────────────────────────────────────────────────────────

@api.post("/games/<int:game_id>/players")
def route_add_player(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name: return _err("Player name is required.")
    try:
        player = add_player(game_id, name)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(player), 201

@api.patch("/players/<int:player_id>")
def route_set_player_active(player_id):
    if (e := _require_auth()): return e
    from database import get_connection
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT game_id FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: return _err("Player not found.", 404)
    game = get_game(row["game_id"])
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e
    data = request.get_json(force=True)
    if "is_active" not in data:
        return _err("is_active field is required.")
    try:
        player = set_player_active(player_id, bool(data["is_active"]))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(player)


# ── Hands ──────────────────────────────────────────────────────────────────────

@api.post("/games/<int:game_id>/hands")
def route_finalize_hand(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e

    data        = request.get_json(force=True)
    raw_entries = data.get("entries", [])
    better_game = bool(data.get("better_game", False))

    if not isinstance(raw_entries, list):
        return _err("entries must be a list.")

    required_keys = {"player_id", "status", "maal", "is_winner"}
    for i, e in enumerate(raw_entries):
        missing = required_keys - set(e.keys())
        if missing:
            return _err(f"Entry {i} is missing fields: {missing}")
        if e["status"] not in ("seen", "unseen", "duplee"):
            return _err(f"Entry {i} has invalid status '{e['status']}'.")
        if not isinstance(e["maal"], int) or e["maal"] < 0:
            return _err(f"Entry {i}: maal must be a non-negative integer.")

    try:
        hand = finalize_hand(game_id, raw_entries, better_game=better_game)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(hand), 201

@api.get("/hands/<int:hand_id>")
def route_get_hand(hand_id):
    if (e := _require_auth()): return e
    hand = get_hand(hand_id)
    if not hand: return _err("Hand not found.", 404)
    return jsonify(hand)
