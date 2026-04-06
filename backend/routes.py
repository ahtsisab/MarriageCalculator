"""
REST API routes for Marriage Calculator.
Token-based auth: server issues a signed token on login; client sends it
as "Authorization: Bearer <token>" — no cookies, works on Safari iOS.
"""

import hmac, hashlib, base64, json, time, os
from flask import Blueprint, request, jsonify, g
from game_model import (create_game, list_games, get_game, get_scoreboard,
                         add_player, set_player_active, delete_game, end_game, resume_game,
                         get_or_create_join_code, join_game_by_code,
                         get_game_members, user_can_access,
                         rename_player, delete_player)
from hand_model import finalize_hand, get_hand
from user_model import register, login
from database import get_connection

api = Blueprint("api", __name__, url_prefix="/api")

TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days


# ── Token helpers ──────────────────────────────────────────────────────────────

def _secret() -> bytes:
    return os.environ.get("SECRET_KEY", "dev-secret-change-in-production").encode()


def _is_admin(user_name: str) -> bool:
    admin = os.environ.get("ADMIN_USERNAME", "")
    return bool(admin) and admin.lower() == user_name.lower()


def _make_token(user_id: int, user_name: str) -> str:
    payload = json.dumps({"uid": user_id, "name": user_name, "exp": int(time.time()) + TOKEN_TTL})
    b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(_secret(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def _verify_token(token: str) -> dict | None:
    try:
        b64, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b64).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def _current_user() -> dict | None:
    """Parse and cache the token for the duration of the request."""
    if "user" not in g:
        auth = request.headers.get("Authorization", "")
        g.user = _verify_token(auth[7:]) if auth.startswith("Bearer ") else None
    return g.user


def _uid() -> int | None:
    u = _current_user()
    return u["uid"] if u else None


# ── Response / auth helpers ────────────────────────────────────────────────────

def _err(msg: str, status: int = 400):
    return jsonify({"error": msg}), status

def _require_auth():
    if not _uid():
        return _err("Not logged in.", 401)

def _check_access(game):
    if game.get("user_id") and not user_can_access(game, _uid()):
        return _err("Access denied.", 403)

def _require_owner(game):
    if game.get("user_id") and game["user_id"] != _uid():
        return _err("Only the owner can modify this game.", 403)


def _get_player_game(player_id: int):
    """
    Look up the game a player belongs to.
    Returns (player_row, game) or raises an error response tuple.
    """
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("SELECT game_id FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None, _err("Player not found.", 404)
    game = get_game(row["game_id"])
    if not game:
        return None, _err("Game not found.", 404)
    return game, None


# ── Auth ───────────────────────────────────────────────────────────────────────

@api.post("/auth/register")
def route_register():
    data = request.get_json(force=True)
    try:
        user = register(data.get("name", ""), data.get("pin", ""))
    except ValueError as e:
        return _err(str(e))
    token = _make_token(user["id"], user["name"])
    return jsonify({"id": user["id"], "name": user["name"], "token": token, "is_admin": _is_admin(user["name"])}), 201


@api.post("/auth/login")
def route_login():
    data = request.get_json(force=True)
    try:
        user = login(data.get("name", ""), data.get("pin", ""))
    except ValueError as e:
        return _err(str(e))
    token = _make_token(user["id"], user["name"])
    return jsonify({"id": user["id"], "name": user["name"], "token": token, "is_admin": _is_admin(user["name"])})


@api.post("/auth/logout")
def route_logout():
    return jsonify({"ok": True})  # stateless — client discards token


@api.get("/auth/me")
def route_me():
    u = _current_user()
    if not u:
        return _err("Not logged in.", 401)
    return jsonify({"id": u["uid"], "name": u["name"], "is_admin": _is_admin(u["name"])})


# ── Games ──────────────────────────────────────────────────────────────────────

@api.get("/games")
def route_list_games():
    if (e := _require_auth()): return e
    return jsonify(list_games(user_id=_uid()))


@api.post("/games")
def route_create_game():
    if (e := _require_auth()): return e
    data    = request.get_json(force=True)
    name    = (data.get("name") or "").strip()
    players = data.get("players", [])
    stake             = float(data.get("stake_per_point", 0.25))
    currency          = (data.get("currency") or "USD").strip()
    allow_better_game = bool(data.get("allow_better_game", False))
    penalty_seen      = int(data.get("penalty_seen", 3))
    penalty_unseen    = int(data.get("penalty_unseen", 10))
    if not name:
        return _err("Game name is required.")
    if not isinstance(players, list) or not all(isinstance(p, str) for p in players):
        return _err("players must be a list of strings.")
    if stake < 0:
        return _err("stake_per_point must be non-negative.")
    if penalty_seen < 0 or penalty_unseen < 0:
        return _err("Penalties must be non-negative.")
    try:
        game = create_game(name, players, user_id=_uid(), stake_per_point=stake, currency=currency,
                           allow_better_game=allow_better_game, penalty_seen=penalty_seen, penalty_unseen=penalty_unseen)
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
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _check_access(game)): return e
    return jsonify({
        "join_code": get_or_create_join_code(game_id),
        "members":   get_game_members(game_id),
    })


@api.delete("/games/<int:game_id>/leave")
def route_leave_game(game_id):
    if (e := _require_auth()): return e
    uid = _uid()
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if game.get("user_id") == uid:
        return _err("You are the owner — delete the game instead.", 400)
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "DELETE FROM game_members WHERE game_id = %s AND user_id = %s",
        (game_id, uid),
    )
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"left": game_id})


@api.post("/games/join")
def route_join_game():
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


@api.get("/players/suggestions")
def route_player_suggestions():
    if (e := _require_auth()): return e
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT p.name
        FROM players p
        JOIN games g ON g.id = p.game_id
        WHERE g.user_id = %s
        ORDER BY p.name
    """, (_uid(),))
    names = [r["name"] for r in cur.fetchall()]
    cur.close(); conn.close()
    return jsonify(names)


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
    game, err = _get_player_game(player_id)
    if err: return err
    if (e := _require_owner(game)): return e
    data = request.get_json(force=True)
    if "is_active" not in data:
        return _err("is_active field is required.")
    try:
        player = set_player_active(player_id, bool(data["is_active"]))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(player)


@api.put("/players/<int:player_id>/name")
def route_rename_player(player_id):
    if (e := _require_auth()): return e
    game, err = _get_player_game(player_id)
    if err: return err
    if (e := _require_owner(game)): return e
    name = (request.get_json(force=True).get("name") or "").strip()
    if not name: return _err("Name is required.")
    try:
        player = rename_player(player_id, name)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(player)


@api.delete("/players/<int:player_id>")
def route_delete_player(player_id):
    if (e := _require_auth()): return e
    game, err = _get_player_game(player_id)
    if err: return err
    if (e := _require_owner(game)): return e
    try:
        delete_player(player_id)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"deleted": player_id})


@api.post("/games/<int:game_id>/resume")
def route_resume_game(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e
    if game.get("is_active", True):
        return _err("Game is already active.", 400)
    resume_game(game_id)
    return jsonify({"resumed": game_id})


@api.post("/games/<int:game_id>/end")
def route_end_game(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e
    if not game.get("is_active", True):
        return _err("Game is already ended.", 400)
    end_game(game_id)
    return jsonify({"ended": game_id})


# ── Hands ──────────────────────────────────────────────────────────────────────

@api.post("/games/<int:game_id>/hands")
def route_finalize_hand(game_id):
    if (e := _require_auth()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e
    if not game.get("is_active", True):
        return _err("This game has ended. No more hands can be added.", 400)

    data        = request.get_json(force=True)
    raw_entries = data.get("entries", [])
    better_game = bool(data.get("better_game", False))

    if not isinstance(raw_entries, list):
        return _err("entries must be a list.")

    required = {"player_id", "status", "maal", "is_winner"}
    for i, e in enumerate(raw_entries):
        if missing := required - set(e.keys()):
            return _err(f"Entry {i} is missing fields: {missing}")
        if e["status"] not in ("seen", "unseen", "duplee"):
            return _err(f"Entry {i} has invalid status '{e['status']}'.")
        if not isinstance(e["maal"], int) or e["maal"] < 0:
            return _err(f"Entry {i}: maal must be a non-negative integer.")

    try:
        hand = finalize_hand(game_id, raw_entries, better_game=better_game,
                            penalty_seen=game.get("penalty_seen", 3),
                            penalty_unseen=game.get("penalty_unseen", 10))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(hand), 201


@api.get("/hands/<int:hand_id>")
def route_get_hand(hand_id):
    if (e := _require_auth()): return e
    hand = get_hand(hand_id)
    if not hand: return _err("Hand not found.", 404)
    return jsonify(hand)


@api.delete("/hands/<int:hand_id>")
def route_delete_hand(hand_id):
    if (e := _require_auth()): return e
    hand = get_hand(hand_id)
    if not hand: return _err("Hand not found.", 404)
    game = get_game(hand["game_id"])
    if not game: return _err("Game not found.", 404)
    if (e := _require_owner(game)): return e
    # Only allow deleting the last hand
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "SELECT id FROM hands WHERE game_id = %s ORDER BY hand_number DESC LIMIT 1",
        (hand["game_id"],),
    )
    last = cur.fetchone()
    if not last or last["id"] != hand_id:
        cur.close(); conn.close()
        return _err("Only the last hand can be deleted.", 400)
    cur.execute("DELETE FROM hands WHERE id = %s", (hand_id,))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"deleted": hand_id})


# ── Admin ──────────────────────────────────────────────────────────────────────

# Simple in-process rate limiter for admin endpoints
import threading
_admin_attempts: dict = {}  # ip -> [timestamp, ...]
_admin_lock = threading.Lock()
_ADMIN_MAX_ATTEMPTS = 10   # per window
_ADMIN_WINDOW_SECS  = 300  # 5 minutes


def _admin_rate_limited() -> bool:
    """Return True if this IP has exceeded the admin attempt limit."""
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _admin_lock:
        attempts = [t for t in _admin_attempts.get(ip, []) if now - t < _ADMIN_WINDOW_SECS]
        attempts.append(now)
        _admin_attempts[ip] = attempts
        return len(attempts) > _ADMIN_MAX_ATTEMPTS


def _require_admin():
    if _admin_rate_limited():
        return _err("Too many attempts. Try again later.", 429)
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pw:
        return _err("Admin access not configured.", 503)
    # Use hmac.compare_digest to prevent timing attacks
    provided = request.headers.get("Authorization", "")
    expected = f"Bearer {admin_pw}"
    if not hmac.compare_digest(provided, expected):
        return _err("Unauthorized.", 401)


@api.get("/admin/game/<int:game_id>")
def route_admin_get_game(game_id):
    if (e := _require_admin()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    game["is_owner"] = False
    return jsonify(game)


@api.get("/admin/game/<int:game_id>/scoreboard")
def route_admin_scoreboard(game_id):
    if (e := _require_admin()): return e
    game = get_game(game_id)
    if not game: return _err("Game not found.", 404)
    board = get_scoreboard(game_id)
    board["players"] = game["players"]
    board["game"]    = {"id": game["id"], "name": game["name"]}
    return jsonify(board)


@api.get("/admin/overview")
def route_admin_overview():
    if (e := _require_admin()): return e

    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("""
        SELECT u.id, u.name, u.created_at,
               COUNT(DISTINCT g.id) AS game_count,
               COUNT(DISTINCT h.id) AS hand_count
        FROM users u
        LEFT JOIN games g ON g.user_id = u.id
        LEFT JOIN hands h ON h.game_id = g.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """)
    users = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT g.id, g.name, g.created_at, g.join_code, g.user_id,
               COUNT(DISTINCT h.id)        AS hand_count,
               COUNT(DISTINCT gm.user_id)  AS member_count
        FROM games g
        LEFT JOIN hands h  ON h.game_id  = g.id
        LEFT JOIN game_members gm ON gm.game_id = g.id
        GROUP BY g.id
        ORDER BY g.created_at DESC
    """)
    games = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    # Nest games under their owner
    games_by_user = {}
    for g in games:
        g["join_code"] = g["join_code"] or None
        games_by_user.setdefault(g["user_id"], []).append(g)

    for u in users:
        u["games"] = games_by_user.get(u["id"], [])

    return jsonify({"users": users, "total_games": len(games)})
