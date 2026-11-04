"""SQLite persistence (single connection guarded by a lock; fine for a LAN event)."""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS quizzes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  questions TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS games(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  quiz_id INTEGER,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  settings TEXT NOT NULL DEFAULT '{}',
  questions TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open',
  created_at REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS players(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,
  nickname TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  avatar TEXT NOT NULL DEFAULT '',
  is_bot INTEGER NOT NULL DEFAULT 0,
  score INTEGER NOT NULL DEFAULT 0,
  joined_at REAL NOT NULL,
  kicked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS answers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  qid TEXT NOT NULL,
  qindex INTEGER NOT NULL,
  choice TEXT NOT NULL,
  correct INTEGER NOT NULL,
  points INTEGER NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  submitted_at REAL NOT NULL DEFAULT 0,
  UNIQUE(game_id, player_id, qid)
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'host',
  disabled INTEGER NOT NULL DEFAULT 0,
  must_change INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  user_agent TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  owner_id INTEGER REFERENCES users(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  starts_at REAL NOT NULL,
  duration_min INTEGER NOT NULL DEFAULT 60,
  tz TEXT NOT NULL DEFAULT 'Europe/London',
  quiz_id INTEGER,
  game_code TEXT NOT NULL DEFAULT '',
  capacity INTEGER NOT NULL DEFAULT 1000,
  reg_open INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS registrations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  name TEXT NOT NULL,
  avatar TEXT NOT NULL,
  pin_hash TEXT NOT NULL,
  ticket TEXT NOT NULL UNIQUE,
  consent_at REAL NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(event_id, email)
);
CREATE TABLE IF NOT EXISTS ai_providers(
  name TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  key_enc TEXT NOT NULL DEFAULT '',
  key_hint TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  base_url TEXT NOT NULL DEFAULT '',
  account_id TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 50,
  updated_at REAL NOT NULL DEFAULT 0,
  updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ai_settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  params TEXT NOT NULL DEFAULT '{}',
  message TEXT NOT NULL DEFAULT '',
  requested INTEGER NOT NULL DEFAULT 0,
  returned INTEGER NOT NULL DEFAULT 0,
  accepted INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  ip TEXT NOT NULL DEFAULT '',
  flags TEXT NOT NULL DEFAULT '{}',
  response TEXT NOT NULL DEFAULT '',
  request TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ai_prompts(
  sha TEXT PRIMARY KEY,
  version TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  first_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  hint TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'read',
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  last_used REAL,
  uses INTEGER NOT NULL DEFAULT 0,
  revoked_at REAL
);
CREATE TABLE IF NOT EXISTS mcp_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  token_id INTEGER,
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(ts);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_ai_audit_ts ON ai_audit(ts);
CREATE INDEX IF NOT EXISTS idx_ai_audit_user ON ai_audit(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_players_game ON players(game_id);
CREATE INDEX IF NOT EXISTS idx_answers_game ON answers(game_id);
"""


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL: a commit no longer waits for the disk to confirm (800 answers: ~630 ms -> ~16 ms). The database stays
        # consistent if the process dies; only a power cut / machine crash can lose the last instant of writes.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(players)")}
        if "avatar" not in cols:  # databases created by the first MVP
            self._conn.execute("ALTER TABLE players ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        if "lobby_music" not in {r[1] for r in self._conn.execute("PRAGMA table_info(quizzes)")}:   # music for the waiting screen
            self._conn.execute("ALTER TABLE quizzes ADD COLUMN lobby_music TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        if "screen_bg" not in {r[1] for r in self._conn.execute("PRAGMA table_info(quizzes)")}:   # the projector background picture
            self._conn.execute("ALTER TABLE quizzes ADD COLUMN screen_bg TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        if "cover" not in {r[1] for r in self._conn.execute("PRAGMA table_info(quizzes)")}:      # a cover picture for each quiz
            self._conn.execute("ALTER TABLE quizzes ADD COLUMN cover TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        if "image_model" not in {r[1] for r in self._conn.execute("PRAGMA table_info(ai_providers)")}:   # AI pictures, added later
            self._conn.execute("ALTER TABLE ai_providers ADD COLUMN image_model TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        if "request" not in {r[1] for r in self._conn.execute("PRAGMA table_info(ai_audit)")}:   # the exact prompt, added later
            self._conn.execute("ALTER TABLE ai_audit ADD COLUMN request TEXT NOT NULL DEFAULT '{}'")
            self._conn.commit()
        self._upgrade_rich_text()
        if "submitted_at" not in {r[1] for r in self._conn.execute("PRAGMA table_info(answers)")}:
            self._conn.execute("ALTER TABLE answers ADD COLUMN submitted_at REAL NOT NULL DEFAULT 0")
            self._conn.commit()
        if "joker" not in {r[1] for r in self._conn.execute("PRAGMA table_info(answers)")}:   # the "double up" power-up
            self._conn.execute("ALTER TABLE answers ADD COLUMN joker INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()
        for table in ("quizzes", "games"):  # ownership arrived with accounts
            tcols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if "owner_id" not in tcols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER REFERENCES users(id)")
                self._conn.commit()

    def _upgrade_rich_text(self):
        """One-time, idempotent: questions saved before the WYSIWYG editor hold markdown-lite text."""
        from .questions import upgrade_question
        for table in ("quizzes", "games"):
            for row in self._conn.execute(f"SELECT id, questions FROM {table}").fetchall():
                qs = json.loads(row["questions"])
                if any(not q.get("rich") for q in qs):
                    self._conn.execute(f"UPDATE {table} SET questions=? WHERE id=?",
                                       (json.dumps([upgrade_question(q) for q in qs]), row["id"]))
        self._conn.commit()

    def change_count(self) -> int:
        """Rows modified through this connection since it opened - a cheap 'has anything changed' signal."""
        return self._conn.total_changes

    def snapshot_bytes(self) -> bytes:
        """A transactionally consistent copy of the whole database (SQLite online-backup API)."""
        with self._lock:
            dst = sqlite3.connect(":memory:")
            try:
                self._conn.backup(dst)
                return dst.serialize()
            finally:
                dst.close()

    def _run(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ---- quizzes -----------------------------------------------------
    @staticmethod
    def _quiz(row):
        if row:
            row["questions"] = json.loads(row["questions"])
        return row

    def list_quizzes(self, owner_id=None):
        """owner_id=None lists everything (super admin); otherwise only that owner's quizzes."""
        where, args = ("WHERE q.owner_id=?", (owner_id,)) if owner_id is not None else ("", ())
        rows = self._all(
            f"""SELECT q.id,q.title,q.description,q.cover,q.questions,q.updated_at,q.owner_id,
                      COALESCE(u.display_name,'') AS owner_name,
                      (SELECT COUNT(*) FROM games g WHERE g.quiz_id=q.id) AS plays,
                      (SELECT MAX(created_at) FROM games g WHERE g.quiz_id=q.id) AS last_played
               FROM quizzes q LEFT JOIN users u ON u.id=q.owner_id {where} ORDER BY q.updated_at DESC""", args)
        for r in rows:
            qs = json.loads(r.pop("questions"))
            r["question_count"] = len(qs)
            r["duration"] = sum(q.get("time_limit", 30) for q in qs)
            r["has_buzzer"] = any(q.get("type") == "buzzer" for q in qs)
        return rows

    def get_quiz(self, quiz_id: int):
        return self._quiz(self._one("SELECT * FROM quizzes WHERE id=?", (quiz_id,)))

    def create_quiz(self, quiz: dict, owner_id=None) -> int:
        now = time.time()
        cur = self._run(
            "INSERT INTO quizzes(title,description,cover,screen_bg,lobby_music,questions,created_at,updated_at,owner_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (quiz["title"], quiz["description"], quiz.get("cover", ""), quiz.get("screen_bg", ""), quiz.get("lobby_music", ""), json.dumps(quiz["questions"]), now, now, owner_id),
        )
        return cur.lastrowid

    def update_quiz(self, quiz_id: int, quiz: dict) -> bool:
        cur = self._run(
            "UPDATE quizzes SET title=?,description=?,cover=?,screen_bg=?,lobby_music=?,questions=?,updated_at=? WHERE id=?",
            (quiz["title"], quiz["description"], quiz.get("cover", ""), quiz.get("screen_bg", ""), quiz.get("lobby_music", ""), json.dumps(quiz["questions"]), time.time(), quiz_id),
        )
        return cur.rowcount > 0

    def delete_quiz(self, quiz_id: int) -> bool:
        return self._run("DELETE FROM quizzes WHERE id=?", (quiz_id,)).rowcount > 0

    # ---- games -------------------------------------------------------
    def create_game(self, code, quiz_id, title, description, settings, questions, owner_id=None) -> int:
        cur = self._run(
            "INSERT INTO games(code,quiz_id,title,description,settings,questions,created_at,owner_id) VALUES(?,?,?,?,?,?,?,?)",
            (code, quiz_id, title, description, json.dumps(settings), json.dumps(questions), time.time(), owner_id),
        )
        return cur.lastrowid

    def get_game(self, game_id: int):
        row = self._one("SELECT * FROM games WHERE id=?", (game_id,))
        if row:
            for k in ("settings", "questions", "state"):
                row[k] = json.loads(row[k])
        return row

    def open_games(self):
        ids = [r["id"] for r in self._all("SELECT id FROM games WHERE status='open'")]
        return [self.get_game(i) for i in ids]

    def list_games(self, limit=50, owner_id=None):
        where, args = ("WHERE g.owner_id=?", (owner_id, limit)) if owner_id is not None else ("", (limit,))
        return self._all(
            f"""SELECT g.id,g.code,g.title,g.status,g.created_at,g.finished_at,g.owner_id,
                      COALESCE(u.display_name,'') AS owner_name,
                      (SELECT COUNT(*) FROM players p WHERE p.game_id=g.id AND p.kicked=0) AS players
               FROM games g LEFT JOIN users u ON u.id=g.owner_id {where} ORDER BY g.id DESC LIMIT ?""",
            args,
        )

    def save_game_state(self, game_id: int, state: dict, questions: list | None = None, settings: dict | None = None):
        sets, args = ["state=?"], [json.dumps(state)]
        if questions is not None:
            sets.append("questions=?")
            args.append(json.dumps(questions))
        if settings is not None:
            sets.append("settings=?")
            args.append(json.dumps(settings))
        self._run(f"UPDATE games SET {','.join(sets)} WHERE id=?", (*args, game_id))

    def reopen_game(self, game_id: int):
        """A finished game goes live again (the host needs a tie-breaker)."""
        self._run("UPDATE games SET status='open', finished_at=NULL WHERE id=?", (game_id,))

    def finish_game(self, game_id: int):
        self._run("UPDATE games SET status='finished', finished_at=? WHERE id=?", (time.time(), game_id))

    # ---- users ---------------------------------------------------------
    def count_users(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM users")["n"]

    def count_active_admins(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0")["n"]

    def create_user(self, username, display_name, password_hash, role="host", must_change=False) -> int:
        cur = self._run(
            "INSERT INTO users(username,display_name,password_hash,role,must_change,created_at) VALUES(?,?,?,?,?,?)",
            (username, display_name, password_hash, role, int(must_change), time.time()),
        )
        return cur.lastrowid

    def get_user(self, user_id: int):
        return self._one("SELECT * FROM users WHERE id=?", (user_id,))

    def get_user_by_username(self, username: str):
        return self._one("SELECT * FROM users WHERE username=?", (username,))

    def list_users(self):
        return self._all(
            """SELECT u.id,u.username,u.display_name,u.role,u.disabled,u.must_change,u.created_at,u.last_login,
                      (SELECT COUNT(*) FROM quizzes q WHERE q.owner_id=u.id) AS quizzes,
                      (SELECT COUNT(*) FROM games g WHERE g.owner_id=u.id) AS games
               FROM users u ORDER BY u.role='admin' DESC, u.display_name COLLATE NOCASE""")

    def update_user(self, user_id: int, **fields):
        allowed = {"display_name", "role", "disabled", "must_change", "password_hash", "last_login"}
        sets = [k for k in fields if k in allowed]
        if sets:
            self._run(f"UPDATE users SET {','.join(k + '=?' for k in sets)} WHERE id=?", (*[fields[k] for k in sets], user_id))

    def claim_orphans(self, owner_id: int):
        """Quizzes/games created before accounts existed belong to the first Super Admin."""
        self._run("UPDATE quizzes SET owner_id=? WHERE owner_id IS NULL", (owner_id,))
        self._run("UPDATE games SET owner_id=? WHERE owner_id IS NULL", (owner_id,))

    # ---- sessions ------------------------------------------------------
    def create_session(self, token_hash: str, user_id: int, expires_at: float, user_agent: str = ""):
        self._run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,user_agent) VALUES(?,?,?,?,?)",
                  (token_hash, user_id, time.time(), expires_at, user_agent[:200]))

    def get_session_user(self, token_hash: str):
        """The (enabled) user behind a valid, unexpired session - or None."""
        return self._one(
            """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0""", (token_hash, time.time()))

    def delete_session(self, token_hash: str):
        self._run("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep_hash: str | None = None):
        if keep_hash:
            self._run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user_id, keep_hash))
        else:
            self._run("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def purge_sessions(self):
        self._run("DELETE FROM sessions WHERE expires_at<=?", (time.time(),))

    # ---- events + registrations ----------------------------------------
    EVENT_FIELDS = ("title", "description", "starts_at", "duration_min", "tz", "quiz_id", "game_code", "capacity", "reg_open")

    def create_event(self, slug, owner_id, title, description, starts_at, duration_min, tz, quiz_id, capacity) -> int:
        cur = self._run(
            """INSERT INTO events(slug,owner_id,title,description,starts_at,duration_min,tz,quiz_id,capacity,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (slug, owner_id, title, description, starts_at, duration_min, tz, quiz_id, capacity, time.time()))
        return cur.lastrowid

    def get_event(self, event_id: int):
        return self._one("SELECT * FROM events WHERE id=?", (event_id,))

    def get_event_by_slug(self, slug: str):
        return self._one("SELECT * FROM events WHERE slug=?", (slug,))

    def list_events(self, owner_id=None):
        where, args = ("WHERE e.owner_id=?", (owner_id,)) if owner_id is not None else ("", ())
        return self._all(
            f"""SELECT e.*, COALESCE(u.display_name,'') AS owner_name,
                       (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id) AS registered
                FROM events e LEFT JOIN users u ON u.id=e.owner_id {where} ORDER BY e.starts_at DESC""", args)

    def update_event(self, event_id: int, **fields):
        sets = [k for k in fields if k in self.EVENT_FIELDS]
        if sets:
            self._run(f"UPDATE events SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [fields[k] for k in sets] + [event_id])

    def delete_event(self, event_id: int):
        self._run("DELETE FROM events WHERE id=?", (event_id,))        # registrations (personal data) go with it

    def count_registrations(self, event_id: int) -> int:
        return self._one("SELECT COUNT(*) AS n FROM registrations WHERE event_id=?", (event_id,))["n"]

    def add_registration(self, event_id, email, name, avatar, pin_hash, ticket) -> int | None:
        """None if that email is already registered for the event."""
        now = time.time()
        with self._lock:
            try:
                cur = self._conn.execute(
                    """INSERT INTO registrations(event_id,email,name,avatar,pin_hash,ticket,consent_at,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""", (event_id, email, name, avatar, pin_hash, ticket, now, now))
                self._conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return None

    def registration_by_ticket(self, event_id: int, ticket: str):
        return self._one("SELECT * FROM registrations WHERE event_id=? AND ticket=?", (event_id, ticket))

    def registration_by_email(self, event_id: int, email: str):
        return self._one("SELECT * FROM registrations WHERE event_id=? AND email=?", (event_id, email))

    def event_registrations(self, event_id: int):
        return self._all("SELECT * FROM registrations WHERE event_id=? ORDER BY id", (event_id,))

    def set_registration_pin(self, reg_id: int, pin_hash: str):
        self._run("UPDATE registrations SET pin_hash=? WHERE id=?", (pin_hash, reg_id))

    def delete_registration(self, event_id: int, reg_id: int) -> bool:
        return self._run("DELETE FROM registrations WHERE event_id=? AND id=?", (event_id, reg_id)).rowcount > 0

    # ---- players / answers --------------------------------------------
    def add_player(self, game_id, token, nickname, email, is_bot, joined_at, avatar="") -> int:
        cur = self._run(
            "INSERT INTO players(game_id,token,nickname,email,avatar,is_bot,joined_at) VALUES(?,?,?,?,?,?,?)",
            (game_id, token, nickname, email, avatar, int(is_bot), joined_at),
        )
        return cur.lastrowid

    def game_players(self, game_id):
        return self._all("SELECT * FROM players WHERE game_id=? ORDER BY id", (game_id,))

    def set_player_score(self, player_id, score):
        self._run("UPDATE players SET score=? WHERE id=?", (score, player_id))

    def kick_player(self, player_id):
        self._run("UPDATE players SET kicked=1 WHERE id=?", (player_id,))

    def add_answer(self, game_id, player_id, qid, qindex, choice, correct, points, elapsed_ms, submitted_at=0.0, joker=False) -> bool:
        cur = self._run(
            """INSERT OR IGNORE INTO answers(game_id,player_id,qid,qindex,choice,correct,points,elapsed_ms,submitted_at,joker)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (game_id, player_id, qid, qindex, json.dumps(choice), int(correct), points, elapsed_ms, submitted_at, int(bool(joker))),
        )
        return cur.rowcount > 0

    def update_answer(self, game_id, player_id, qid, correct, points):
        """The host re-judged an answer (accepted or rejected a typed answer)."""
        self._run("UPDATE answers SET correct=?, points=? WHERE game_id=? AND player_id=? AND qid=?",
                  (int(correct), points, game_id, player_id, qid))

    def delete_answers(self, game_id, qid):
        """The host ran a question again: the first round's answers are wiped so everyone can answer afresh."""
        self._run("DELETE FROM answers WHERE game_id=? AND qid=?", (game_id, qid))

    def game_answers(self, game_id):
        rows = self._all("SELECT * FROM answers WHERE game_id=?", (game_id,))
        for r in rows:
            r["choice"] = json.loads(r["choice"])
        return rows

    # ---- AI drafting: services, settings, audit ---------------------------------
    def ai_settings(self) -> dict:
        from .ai import DEFAULT_SETTINGS
        out = dict(DEFAULT_SETTINGS)
        for r in self._all("SELECT key,value FROM ai_settings"):
            if r["key"] in out:
                try:
                    out[r["key"]] = json.loads(r["value"])
                except ValueError:
                    pass
        return out

    def ai_set_settings(self, values: dict):
        for k, v in values.items():
            self._run("INSERT INTO ai_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))

    def ai_providers(self, with_keys: bool = False) -> dict:
        """name -> config. The encrypted key is only included for the server's own use, never for the browser."""
        from .ai import PROVIDERS
        rows = {r["name"]: r for r in self._all("SELECT * FROM ai_providers")}
        out = {}
        for name in PROVIDERS:
            r = rows.get(name, {})
            cfg = {"enabled": bool(r.get("enabled")), "has_key": bool(r.get("key_enc")), "key_hint": r.get("key_hint", ""),
                   "model": r.get("model", ""), "image_model": r.get("image_model", ""), "base_url": r.get("base_url", ""), "account_id": r.get("account_id", ""),
                   "priority": r.get("priority", 50), "updated_at": r.get("updated_at", 0), "updated_by": r.get("updated_by", "")}
            if with_keys:
                cfg["key_enc"] = r.get("key_enc", "")
            out[name] = cfg
        return out

    def ai_save_provider(self, name: str, fields: dict, updated_by: str):
        self._run("INSERT OR IGNORE INTO ai_providers(name) VALUES(?)", (name,))
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?, updated_by=?"
        self._run(f"UPDATE ai_providers SET {sets} WHERE name=?", (*fields.values(), time.time(), updated_by, name))

    def ai_audit_add(self, row: dict) -> int:
        request = dict(row.get("request") or {})
        system = request.pop("system", None)          # the fixed system prompt is stored once, not once per request
        if system:
            sha = hashlib.sha256(system.encode()).hexdigest()[:16]
            self._run("INSERT OR IGNORE INTO ai_prompts(sha,version,text,first_seen) VALUES(?,?,?,?)", (sha, str(request.get("prompt_version", "")), system, time.time()))
            request["system_sha"] = sha
        cur = self._run(
            """INSERT INTO ai_audit(ts,user_id,username,action,status,provider,model,params,message,requested,returned,
                                    tokens_in,tokens_out,latency_ms,ip,flags,response,request) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (time.time(), row.get("user_id"), row.get("username", ""), row["action"], row["status"], row.get("provider", ""),
             row.get("model", ""), json.dumps(row.get("params") or {}), str(row.get("message", ""))[:1000], row.get("requested", 0),
             row.get("returned", 0), row.get("tokens_in", 0), row.get("tokens_out", 0), row.get("latency_ms", 0), row.get("ip", ""),
             json.dumps(row.get("flags") or {}), row.get("response", ""), json.dumps(request)))
        return cur.lastrowid

    def ai_audit_count(self, user_id, since: float, statuses: tuple, action: str = "generate") -> int:
        marks = ",".join("?" * len(statuses))
        where, args = f"action=? AND ts>=? AND status IN ({marks})", [action, since, *statuses]
        if user_id is not None:
            where += " AND user_id=?"
            args.append(user_id)
        return self._one(f"SELECT COUNT(*) n FROM ai_audit WHERE {where}", tuple(args))["n"]

    def ai_audit_accept(self, audit_id: int, user_id: int, count: int) -> bool:
        cur = self._run("UPDATE ai_audit SET accepted=MIN(returned, accepted+?) WHERE id=? AND user_id=? AND action IN ('generate','image')",
                        (max(0, count), audit_id, user_id))
        return cur.rowcount > 0

    def ai_audit_list(self, status="", user="", action="", limit=100, offset=0, since=0.0, detail=False) -> dict:
        where, args = ["ts>=?"], [since]
        for col, val in (("status", status), ("username", user), ("action", action)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        w = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) n FROM ai_audit WHERE {w}", tuple(args))["n"]
        cols = "*" if detail else "id,ts,username,action,status,provider,model,params,message,requested,returned,accepted,tokens_in,tokens_out,latency_ms,ip,flags"
        rows = self._all(f"SELECT {cols} FROM ai_audit WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset))
        for r in rows:
            r["params"], r["flags"] = json.loads(r["params"]), json.loads(r["flags"])
        return {"total": total, "rows": rows}

    def ai_audit_one(self, audit_id: int):
        r = self._one("SELECT * FROM ai_audit WHERE id=?", (audit_id,))
        if r:
            r["params"], r["flags"], r["request"] = json.loads(r["params"]), json.loads(r["flags"]), json.loads(r.get("request") or "{}")
            sha = r["request"].get("system_sha")
            if sha:
                prompt = self._one("SELECT text FROM ai_prompts WHERE sha=?", (sha,))
                r["request"]["system"] = prompt["text"] if prompt else ""
        return r

    def ai_stats(self, since: float) -> dict:
        by_status = {r["status"]: r["n"] for r in self._all("SELECT status, COUNT(*) n FROM ai_audit WHERE action='generate' AND ts>=? GROUP BY status", (since,))}
        by_provider = self._all("""SELECT provider, COUNT(*) calls, SUM(status IN ('ok','partial')) good, SUM(tokens_in) tin, SUM(tokens_out) tout,
                                         CAST(AVG(latency_ms) AS INTEGER) avg_ms FROM ai_audit
                                  WHERE action='generate' AND provider!='' AND ts>=? GROUP BY provider""", (since,))
        by_user = self._all("""SELECT username, COUNT(*) requests, SUM(returned) returned, SUM(accepted) accepted FROM ai_audit
                               WHERE action='generate' AND ts>=? GROUP BY username ORDER BY requests DESC LIMIT 10""", (since,))
        tot = self._one("SELECT COALESCE(SUM(returned),0) returned, COALESCE(SUM(accepted),0) accepted FROM ai_audit WHERE action='generate' AND ts>=?", (since,))
        return {"by_status": by_status, "by_provider": by_provider, "by_user": by_user, **tot}

    def ai_audit_purge(self, before: float) -> int:
        return self._run("DELETE FROM ai_audit WHERE ts<?", (before,)).rowcount

    # ---- personal access tokens (for the MCP server) and their audit log ---------
    def create_api_token(self, user_id: int, name: str, token_hash: str, hint: str, scope: str, expires_at: float) -> int:
        return self._run("INSERT INTO api_tokens(user_id,name,token_hash,hint,scope,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                         (user_id, name, token_hash, hint, scope, time.time(), expires_at)).lastrowid

    def find_api_token(self, token_hash: str):
        """The token plus its owner, only if it is live (not revoked, not expired, owner enabled). `id` is the owner's id."""
        return self._one(
            """SELECT t.id AS token_id, t.scope, u.id, u.username, u.display_name, u.role, u.must_change
               FROM api_tokens t JOIN users u ON u.id=t.user_id
               WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.expires_at>? AND u.disabled=0""", (token_hash, time.time()))

    def touch_api_token(self, token_id: int, now: float):
        self._run("UPDATE api_tokens SET last_used=?, uses=uses+1 WHERE id=?", (now, token_id))

    def list_api_tokens(self, user_id: int):
        return self._all("""SELECT id,name,hint,scope,created_at,expires_at,last_used,uses,revoked_at FROM api_tokens
                            WHERE user_id=? ORDER BY id DESC LIMIT 50""", (user_id,))

    def active_token_count(self, user_id: int) -> int:
        return self._one("SELECT COUNT(*) n FROM api_tokens WHERE user_id=? AND revoked_at IS NULL AND expires_at>?", (user_id, time.time()))["n"]

    def revoke_api_token(self, token_id: int, user_id: int) -> bool:
        return self._run("UPDATE api_tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (time.time(), token_id, user_id)).rowcount > 0

    def revoke_user_tokens(self, user_id: int):
        self._run("UPDATE api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (time.time(), user_id))

    def mcp_audit_add(self, row: dict):
        self._run("INSERT INTO mcp_audit(ts,token_id,user_id,username,tool,status,message,ms) VALUES(?,?,?,?,?,?,?,?)",
                  (time.time(), row.get("token_id"), row.get("user_id"), row.get("username", ""), row["tool"], row["status"], str(row.get("message", ""))[:400], row.get("ms", 0)))

    def mcp_count(self, token_id: int, since: float, tools: tuple, status: str) -> int:
        marks = ",".join("?" * len(tools))
        return self._one(f"SELECT COUNT(*) n FROM mcp_audit WHERE token_id=? AND ts>=? AND status=? AND tool IN ({marks})", (token_id, since, status, *tools))["n"]

    def mcp_audit_list(self, limit=50, offset=0, user="", status="", since=0.0) -> dict:
        where, args = ["ts>=?"], [since]
        for col, val in (("username", user), ("status", status)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        w = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) n FROM mcp_audit WHERE {w}", tuple(args))["n"]
        return {"total": total, "rows": self._all(f"SELECT * FROM mcp_audit WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset))}

    def mcp_audit_purge(self, before: float) -> int:
        return self._run("DELETE FROM mcp_audit WHERE ts<?", (before,)).rowcount
