"""SQLite persistence (single connection guarded by a lock; fine for a LAN event)."""

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
        self._conn.executescript(SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(players)")}
        if "avatar" not in cols:  # databases created by the first MVP
            self._conn.execute("ALTER TABLE players ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        self._upgrade_rich_text()
        if "submitted_at" not in {r[1] for r in self._conn.execute("PRAGMA table_info(answers)")}:
            self._conn.execute("ALTER TABLE answers ADD COLUMN submitted_at REAL NOT NULL DEFAULT 0")
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
            f"""SELECT q.id,q.title,q.description,q.questions,q.updated_at,q.owner_id,
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
            "INSERT INTO quizzes(title,description,questions,created_at,updated_at,owner_id) VALUES(?,?,?,?,?,?)",
            (quiz["title"], quiz["description"], json.dumps(quiz["questions"]), now, now, owner_id),
        )
        return cur.lastrowid

    def update_quiz(self, quiz_id: int, quiz: dict) -> bool:
        cur = self._run(
            "UPDATE quizzes SET title=?,description=?,questions=?,updated_at=? WHERE id=?",
            (quiz["title"], quiz["description"], json.dumps(quiz["questions"]), time.time(), quiz_id),
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

    def add_answer(self, game_id, player_id, qid, qindex, choice, correct, points, elapsed_ms, submitted_at=0.0) -> bool:
        cur = self._run(
            """INSERT OR IGNORE INTO answers(game_id,player_id,qid,qindex,choice,correct,points,elapsed_ms,submitted_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (game_id, player_id, qid, qindex, json.dumps(choice), int(correct), points, elapsed_ms, submitted_at),
        )
        return cur.rowcount > 0

    def game_answers(self, game_id):
        rows = self._all("SELECT * FROM answers WHERE game_id=?", (game_id,))
        for r in rows:
            r["choice"] = json.loads(r["choice"])
        return rows
