"""
REST API routes for Marriage Calculator.
"""

from flask import Blueprint, request, jsonify
from game_model import create_game, list_games, get_game, get_scoreboard, add_player, set_player_active
from hand_model import finalize_hand, get_hand

api = Blueprint("api", __name__, url_prefix="/api")


def _err(msg: str, status: int = 400):
    return jsonify({"error": msg}), status


# ── Games ──────────────────────────────────────────────────────────────────────

@api.get("/games")
def route_list_games():
    return jsonify(list_games())


@api.post("/games")
def route_create_game():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    players = data.get("players", [])
    if not name:
        return _err("Game name is required.")
    if not isinstance(players, list) or not all(isinstance(p, str) for p in players):
        return _err("players must be a list of strings.")
    try:
        game = create_game(name, players)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(game), 201


@api.get("/games/<int:game_id>")
def route_get_game(game_id):
    game = get_game(game_id)
    if not game:
        return _err("Game not found.", 404)
    return jsonify(game)


@api.get("/games/<int:game_id>/scoreboard")
def route_scoreboard(game_id):
    game = get_game(game_id)
    if not game:
        return _err("Game not found.", 404)
    board = get_scoreboard(game_id)
    board["players"] = game["players"]
    board["game"] = {"id": game["id"], "name": game["name"]}
    return jsonify(board)


# ── Players ────────────────────────────────────────────────────────────────────

@api.post("/games/<int:game_id>/players")
def route_add_player(game_id):
    game = get_game(game_id)
    if not game:
        return _err("Game not found.", 404)
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return _err("Player name is required.")
    try:
        player = add_player(game_id, name)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(player), 201


@api.patch("/players/<int:player_id>")
def route_set_player_active(player_id):
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
    game = get_game(game_id)
    if not game:
        return _err("Game not found.", 404)

    data = request.get_json(force=True)
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
    hand = get_hand(hand_id)
    if not hand:
        return _err("Hand not found.", 404)
    return jsonify(hand)
