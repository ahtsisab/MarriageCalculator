"""
Marriage Calculator — backend test suite.
Uses only stdlib unittest + Flask test client (no pytest, no network).
Run with:  python3 tests.py
"""

import os, sys, unittest, json, tempfile

# ── Setup: use a fresh SQLite DB for every test run ──────────────────────────
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_PASSWORD"] = "adminpass"
os.environ["ADMIN_USERNAME"] = "admin"

_tmp = tempfile.mktemp(suffix=".db")
os.environ["SQLITE_PATH"] = _tmp

sys.path.insert(0, os.path.dirname(__file__))


# ════════════════════════════════════════════════════════════════════════════
# 1. DATABASE — schema + migration integrity
# ════════════════════════════════════════════════════════════════════════════
class TestDatabase(unittest.TestCase):

    def test_migrations_pg_is_list_of_strings(self):
        from database import _MIGRATIONS_PG
        self.assertIsInstance(_MIGRATIONS_PG, list)
        for m in _MIGRATIONS_PG:
            self.assertIsInstance(m, str, f"Migration entry is not a string: {m!r}")

    def test_migrations_sqlite_is_list_of_strings(self):
        from database import _MIGRATIONS_SQLITE
        self.assertIsInstance(_MIGRATIONS_SQLITE, list)
        for m in _MIGRATIONS_SQLITE:
            self.assertIsInstance(m, str, f"Migration entry is not a string: {m!r}")

    def test_no_if_not_exists_in_sqlite_alter(self):
        """SQLite ALTER TABLE does not support IF NOT EXISTS."""
        from database import _MIGRATIONS_SQLITE
        for m in _MIGRATIONS_SQLITE:
            if m.strip().upper().startswith("ALTER TABLE"):
                self.assertNotIn("IF NOT EXISTS", m.upper(),
                    f"SQLite ALTER TABLE cannot use IF NOT EXISTS: {m}")

    def test_init_db_creates_tables(self):
        import database
        database.init_db()
        conn = database.get_connection()
        cur  = conn.cursor()
        for table in ["users", "games", "players", "hands", "hand_entries", "game_members"]:
            cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            self.assertIsNotNone(cur.fetchone(), f"Table '{table}' not created")
        cur.close(); conn.close()

    def test_games_table_has_new_columns(self):
        """Regression: stake, currency, allow_better_game, penalty_seen/unseen columns must exist."""
        import database
        database.init_db()
        conn = database.get_connection()
        cur  = conn.cursor()
        cur.execute("PRAGMA table_info(games)")
        cols = {r["name"] for r in cur.fetchall()}
        cur.close(); conn.close()
        for col in ["stake_per_point", "currency", "allow_better_game", "penalty_seen", "penalty_unseen"]:
            self.assertIn(col, cols, f"Column '{col}' missing from games table")


# ════════════════════════════════════════════════════════════════════════════
# 2. SCORING — compute_points correctness
# ════════════════════════════════════════════════════════════════════════════
class TestScoring(unittest.TestCase):

    def _make_entries(self, specs):
        """specs: list of (status, maal, is_winner)"""
        return [
            {"player_id": i+1, "status": s, "maal": m, "is_winner": w}
            for i, (s, m, w) in enumerate(specs)
        ]

    def test_points_sum_to_zero(self):
        from hand_model import compute_points
        entries = self._make_entries([
            ("seen", 15, True),
            ("seen", 10, False),
            ("seen",  5, False),
        ])
        compute_points(entries)
        self.assertEqual(sum(e["points"] for e in entries), 0)

    def test_unseen_maal_forced_to_zero(self):
        from hand_model import compute_points
        entries = self._make_entries([
            ("seen",   20, True),
            ("unseen", 99, False),  # maal should be zeroed
            ("seen",   10, False),
        ])
        compute_points(entries)
        self.assertEqual(entries[1]["maal"], 0)
        self.assertEqual(sum(e["points"] for e in entries), 0)

    def test_better_game_doubles_points(self):
        from hand_model import compute_points
        entries_normal = self._make_entries([("seen",10,True),("seen",5,False),("seen",0,False)])
        entries_better = self._make_entries([("seen",10,True),("seen",5,False),("seen",0,False)])
        compute_points(entries_normal, better_game=False)
        compute_points(entries_better, better_game=True)
        for n, b in zip(entries_normal, entries_better):
            self.assertEqual(b["points"], n["points"] * 2)

    def test_winner_cannot_be_unseen(self):
        from hand_model import compute_points
        entries = self._make_entries([
            ("unseen", 0, True),
            ("seen",  10, False),
            ("seen",   5, False),
        ])
        with self.assertRaises(ValueError):
            compute_points(entries)

    def test_exactly_one_winner_required(self):
        from hand_model import compute_points
        entries = self._make_entries([("seen",5,False),("seen",5,False),("seen",5,False)])
        with self.assertRaises(ValueError):
            compute_points(entries)

    def test_custom_penalties(self):
        from hand_model import compute_points
        entries = self._make_entries([
            ("seen",  10, True),
            ("seen",   5, False),
            ("unseen", 0, False),
        ])
        compute_points(entries, penalty_seen=5, penalty_unseen=20)
        self.assertEqual(sum(e["points"] for e in entries), 0)

    def test_duplee_zero_penalty(self):
        from hand_model import compute_points
        entries = self._make_entries([
            ("seen",   10, True),
            ("duplee",  5, False),
            ("seen",    0, False),
        ])
        compute_points(entries)
        self.assertEqual(sum(e["points"] for e in entries), 0)


# ════════════════════════════════════════════════════════════════════════════
# 3. GAME MODEL — business logic
# ════════════════════════════════════════════════════════════════════════════
class TestGameModel(unittest.TestCase):

    def setUp(self):
        import database
        database.init_db()

    def _register_user(self, name="testuser"):
        from user_model import register
        try:
            return register(name, "1234")
        except ValueError:
            from user_model import login
            return login(name, "1234")

    def test_create_game_requires_3_to_6_players(self):
        from game_model import create_game
        u = self._register_user("gm_user1")
        with self.assertRaises(ValueError):
            create_game("Bad", ["A", "B"], user_id=u["id"])
        with self.assertRaises(ValueError):
            create_game("Bad", ["A","B","C","D","E","F","G"], user_id=u["id"])

    def test_create_game_rejects_duplicate_player_names(self):
        from game_model import create_game
        u = self._register_user("gm_user2")
        with self.assertRaises(ValueError):
            create_game("Dup", ["Ram", "Shyam", "ram"], user_id=u["id"])

    def test_add_player_rejects_duplicate_name(self):
        from game_model import create_game, add_player
        u = self._register_user("gm_user3")
        g = create_game("G", ["A","B","C"], user_id=u["id"])
        with self.assertRaises(ValueError):
            add_player(g["id"], "a")  # case-insensitive

    def test_add_player_cap_at_6_active(self):
        from game_model import create_game, add_player
        u = self._register_user("gm_user4")
        g = create_game("G", ["A","B","C","D","E","F"], user_id=u["id"])
        with self.assertRaises(ValueError):
            add_player(g["id"], "G")

    def test_cannot_deactivate_below_3(self):
        from game_model import create_game, set_player_active
        u = self._register_user("gm_user5")
        g = create_game("G", ["A","B","C"], user_id=u["id"])
        with self.assertRaises(ValueError):
            set_player_active(g["players"][0]["id"], False)

    def test_get_game_returns_new_columns(self):
        from game_model import create_game, get_game
        u = self._register_user("gm_user6")
        g = create_game("G", ["A","B","C"], user_id=u["id"],
                        stake_per_point=0.5, currency="NPR",
                        allow_better_game=True, penalty_seen=5, penalty_unseen=15)
        fetched = get_game(g["id"])
        self.assertEqual(fetched["stake_per_point"], 0.5)
        self.assertEqual(fetched["currency"], "NPR")
        self.assertTrue(fetched["allow_better_game"])
        self.assertEqual(fetched["penalty_seen"], 5)
        self.assertEqual(fetched["penalty_unseen"], 15)

    def test_end_game_prevents_further_hands(self):
        from game_model import create_game, get_game, end_game
        u = self._register_user("gm_user7")
        g = create_game("G", ["A","B","C"], user_id=u["id"])
        end_game(g["id"])
        fetched = get_game(g["id"])
        self.assertFalse(fetched["is_active"])

    def test_resume_game_marks_active(self):
        from game_model import create_game, get_game, end_game, resume_game
        u = self._register_user("gm_user8")
        g = create_game("G", ["A","B","C"], user_id=u["id"])
        end_game(g["id"])
        self.assertFalse(get_game(g["id"])["is_active"])
        resume_game(g["id"])
        self.assertTrue(get_game(g["id"])["is_active"])


# ════════════════════════════════════════════════════════════════════════════
# 4. API ROUTES — Flask test client
# ════════════════════════════════════════════════════════════════════════════
class TestRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import database
        database.init_db()
        # Mock flask_cors if not installed (no network in test env)
        try:
            from app import create_app
        except ModuleNotFoundError:
            import unittest
            raise unittest.SkipTest("flask_cors not installed — skipping route tests")
        cls.app = create_app()
        cls.client = cls.app.test_client()
        # Register a user and get token
        r = cls.client.post("/api/auth/register",
            json={"name": "routeuser", "pin": "9999"},
            content_type="application/json")
        cls.token = r.get_json()["token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    def _create_game(self, players=None):
        r = self.client.post("/api/games",
            json={"name": "Test", "players": players or ["A","B","C"]},
            headers=self.headers)
        return r.get_json()

    def test_register_returns_token(self):
        r = self.client.post("/api/auth/register",
            json={"name": "newuser_tok", "pin": "1234"})
        data = r.get_json()
        self.assertEqual(r.status_code, 201)
        self.assertIn("token", data)
        self.assertIn("is_admin", data)

    def test_login_returns_token(self):
        self.client.post("/api/auth/register", json={"name": "logintest", "pin": "5678"})
        r = self.client.post("/api/auth/login", json={"name": "logintest", "pin": "5678"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("token", r.get_json())

    def test_auth_me(self):
        r = self.client.get("/api/auth/me", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["name"], "routeuser")

    def test_create_game(self):
        g = self._create_game()
        self.assertIn("id", g)
        self.assertEqual(len(g["players"]), 3)

    def test_create_game_stores_options(self):
        r = self.client.post("/api/games", headers=self.headers, json={
            "name": "Opts", "players": ["X","Y","Z"],
            "stake_per_point": 1.0, "currency": "NPR",
            "allow_better_game": True, "penalty_seen": 4, "penalty_unseen": 12,
        })
        g = r.get_json()
        self.assertEqual(r.status_code, 201)
        fetched = self.client.get(f"/api/games/{g['id']}", headers=self.headers).get_json()
        self.assertEqual(fetched["currency"], "NPR")
        self.assertTrue(fetched["allow_better_game"])
        self.assertEqual(fetched["penalty_seen"], 4)

    def test_ended_game_rejects_new_hands(self):
        g = self._create_game()
        # End the game
        self.client.post(f"/api/games/{g['id']}/end", headers=self.headers)
        # Try to add a hand
        r = self.client.post(f"/api/games/{g['id']}/hands", headers=self.headers, json={
            "better_game": False,
            "entries": [
                {"player_id": g["players"][0]["id"], "status":"seen","maal":10,"is_winner":True},
                {"player_id": g["players"][1]["id"], "status":"seen","maal":5,"is_winner":False},
                {"player_id": g["players"][2]["id"], "status":"seen","maal":0,"is_winner":False},
            ]
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("ended", r.get_json()["error"].lower())

    def test_resume_game_allows_hands_again(self):
        g = self._create_game()
        pid = [p["id"] for p in g["players"]]
        # End then resume
        self.client.post(f"/api/games/{g['id']}/end", headers=self.headers)
        r = self.client.post(f"/api/games/{g['id']}/resume", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["resumed"], g["id"])
        # Verify is_active is True
        fetched = self.client.get(f"/api/games/{g['id']}", headers=self.headers).get_json()
        self.assertTrue(fetched["is_active"])
        # Can now add a hand
        r = self.client.post(f"/api/games/{g['id']}/hands", headers=self.headers, json={
            "better_game": False,
            "entries": [
                {"player_id":pid[0],"status":"seen","maal":10,"is_winner":True},
                {"player_id":pid[1],"status":"seen","maal":5,"is_winner":False},
                {"player_id":pid[2],"status":"seen","maal":0,"is_winner":False},
            ]
        })
        self.assertEqual(r.status_code, 201)

    def test_resume_active_game_rejected(self):
        g = self._create_game()
        r = self.client.post(f"/api/games/{g['id']}/resume", headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_delete_only_last_hand(self):
        g = self._create_game()
        pid = [p["id"] for p in g["players"]]
        def add_hand():
            return self.client.post(f"/api/games/{g['id']}/hands", headers=self.headers, json={
                "better_game": False,
                "entries": [
                    {"player_id":pid[0],"status":"seen","maal":10,"is_winner":True},
                    {"player_id":pid[1],"status":"seen","maal":5,"is_winner":False},
                    {"player_id":pid[2],"status":"seen","maal":0,"is_winner":False},
                ]
            }).get_json()
        h1 = add_hand()
        h2 = add_hand()
        # Can't delete first hand
        r = self.client.delete(f"/api/hands/{h1['id']}", headers=self.headers)
        self.assertEqual(r.status_code, 400)
        # Can delete last hand
        r = self.client.delete(f"/api/hands/{h2['id']}", headers=self.headers)
        self.assertEqual(r.status_code, 200)

    def test_unauthenticated_requests_rejected(self):
        r = self.client.get("/api/games")
        self.assertEqual(r.status_code, 401)

    def test_admin_overview_requires_password(self):
        r = self.client.get("/api/admin/overview",
            headers={"Authorization": "Bearer wrongpassword"})
        self.assertEqual(r.status_code, 401)

    def test_admin_overview_works(self):
        r = self.client.get("/api/admin/overview",
            headers={"Authorization": "Bearer adminpass"})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("users", data)
        self.assertIn("total_games", data)
        # Users have nested games
        for u in data["users"]:
            self.assertIn("games", u)

    def test_player_suggestions(self):
        self._create_game(["SuggestA", "SuggestB", "SuggestC"])
        r = self.client.get("/api/players/suggestions", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        names = r.get_json()
        self.assertIn("SuggestA", names)


# ════════════════════════════════════════════════════════════════════════════
# 5. FRONTEND — HTML structure and JS integrity
# ════════════════════════════════════════════════════════════════════════════
class TestFrontend(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "../frontend/index.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()
        # Extract JS
        import re
        m = re.search(r'<script>(.*?)</script>', cls.html, re.DOTALL)
        cls.js = m.group(1) if m else ""

    def test_js_syntax(self):
        """Regression: missing function headers cause SyntaxError that silently kills all JS."""
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(self.js); fname = f.name
        r = subprocess.run(["node", "--check", fname], capture_output=True, text=True)
        os.unlink(fname)
        self.assertEqual(r.returncode, 0, f"JS syntax error:\n{r.stderr}")

    def test_required_dom_ids_present(self):
        """Regression: refactors have dropped required element IDs."""
        required_ids = [
            # Auth
            "screen-auth", "login-name", "login-pin", "login-error",
            "reg-name", "reg-pin", "reg-pin2", "reg-error",
            "tab-login", "tab-register",
            # Home
            "screen-home", "game-list",
            # New game
            "screen-new-game", "new-game-name",
            "player-names-section", "player-inputs",
            "suggestion-pills", "suggestion-pill-list",
            "new-game-error",
            # Game options
            "currency-select", "stake-input",
            "penalty-seen-input", "penalty-unseen-input",
            "allow-better-game-switch", "allow-better-game-label",
            # Game screen
            "screen-game", "game-breadcrumb-bar", "game-title-text",
            "player-roster", "add-player-section", "new-player-name",
            "add-hand-btn", "end-game-btn", "resume-game-header-btn", "share-btn",
            "scoreboard-full", "scoreboard-condensed",
            "score-thead", "score-tbody", "score-tfoot",
            "scoreboard-empty", "settlement-panel",
            # Add hand
            "screen-add-hand", "hand-entries",
            "better-game-toggle", "add-hand-error", "add-hand-success",
            # Dialogs
            "loading-overlay", "dialog-overlay",
            "end-game-confirm-overlay",
            "admin-dialog-overlay", "admin-pw-input",
            "join-dialog-overlay", "join-code-input",
            "share-dialog-overlay",
            # Admin
            "screen-admin", "admin-user-list",
            # Theme / user
            "user-badge-wrap", "theme-btn", "change-pin-overlay", "change-pin-current", "change-pin-new", "change-pin-confirm", "change-pin-error",
        ]
        import re
        found_ids = set(re.findall(r'id="([^"]+)"', self.html))
        for eid in required_ids:
            self.assertIn(eid, found_ids, f'Missing required id="{eid}"')

    def test_no_duplicate_ids(self):
        """Duplicate IDs break getElementById."""
        import re
        ids = re.findall(r'id="([^"]+)"', self.html)
        # Exclude JS template literal IDs like pname-${i}
        ids = [eid for eid in ids if '${' not in eid]
        seen = {}
        for eid in ids:
            seen[eid] = seen.get(eid, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        self.assertEqual(dupes, {}, f"Duplicate IDs found: {dupes}")

    def test_required_js_functions_present(self):
        """Regression: refactors have renamed or deleted functions called from HTML."""
        required_fns = [
            # Auth
            "doLogin", "doRegister", "doLogout", "switchAuthTab",
            # Navigation
            "showScreen", "openGame", "goToCurrentGame",
            # New game
            "setPlayerCount", "createGame", "setCurrency",
            "toggleGameOptions", "toggleAllowBetterGame",
            "pickSuggestionPill", "loadPlayerSuggestions",
            # Game
            "renderPlayerRoster", "renderScoreboard",
            "refreshScoreboard", "_refreshGameView",
            "togglePlayerActive", "startRenamePlayer",
            "doDeletePlayer", "addPlayer",
            # Hand
            "showAddHand", "finalizeHand", "setWinner", "setStatus", "setMaal",
            "toggleBetterGame", "editLastHand",
            # Share/join
            "showShareDialog", "closeShareDialog", "copyJoinCode",
            "showJoinDialog", "closeJoinDialog", "doJoinGame",
            # End/resume game
            "showEndGame", "closeEndGameConfirm", "confirmEndGame",
            "renderSettlementPanel", "doResumeGame",
            # Admin
            "openAdminDialog", "submitAdminPassword", "loadAdminScreen",
            "renderAdminScreen", "toggleAdminUser", "adminOpenGame",
            # Scoreboard
            "toggleScoreboardCollapse", "renderCondensedScoreboard",
            # Utilities
            "loading", "showError", "showSuccess", "apiFetch",
            "esc", "fmtDate", "ptsClass", "applyTheme", "toggleTheme",
            # Delete/leave
            "promptDelete", "confirmDelete", "promptLeave",
            "showChangePinDialog", "closeChangePinDialog", "doChangePin",
            "toggleUserMenu", "closeUserMenu",
        ]
        import re
        defined = set(re.findall(r'function\s+(\w+)\s*\(', self.js))
        for fn in required_fns:
            self.assertIn(fn, defined, f'Required function "{fn}" not defined in JS')

    def test_all_onclick_functions_defined(self):
        """Every function called via onclick/onchange must be defined."""
        import re
        called = set(re.findall(r'on(?:click|change|input|keydown)="([a-zA-Z_]\w*)\(', self.html))
        defined = set(re.findall(r'function\s+(\w+)\s*\(', self.js))
        missing = called - defined
        self.assertEqual(missing, set(), f"onclick/onchange calls to undefined functions: {missing}")

    def test_loading_overlay_starts_hidden(self):
        self.assertIn('id="loading-overlay"', self.html)
        # Should not have 'visible' class on the loading overlay element
        import re
        m = re.search(r'id="loading-overlay"[^>]*class="([^"]*)"', self.html)
        if m:
            self.assertNotIn("visible", m.group(1))

    def test_screens_defined(self):
        for screen in ["screen-auth", "screen-home", "screen-new-game",
                       "screen-game", "screen-add-hand", "screen-admin"]:
            self.assertIn(f'id="{screen}"', self.html, f'Screen "{screen}" missing')

    def test_api_url_configured(self):
        self.assertIn("marriagecalculator-production.up.railway.app", self.js)


# ════════════════════════════════════════════════════════════════════════════
# Run
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import atexit
    atexit.register(lambda: os.path.exists(_tmp) and os.unlink(_tmp))
    unittest.main(verbosity=2)
