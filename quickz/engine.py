"""Game engine: one `Game` per room, server-authoritative, state-push to clients.

Every transition pushes a complete role-specific *view* (see view_player /
view_screen / view_host), so clients are stateless renderers and reconnecting
or late-joining just works.
"""

import asyncio
import random
import re
import secrets
import time
from dataclasses import dataclass, field

from .db import Database
from .questions import ValidationError, strip_tags, validate_question

DEFAULT_SETTINGS = {"speed_bonus": True, "auto_reveal": True, "ask_email": False}
BUZZ_WINDOW_S = 0.12
ANSWER_GRACE_MS = 300
MAX_PLAYERS = 500
MAX_BOTS = 200
SEND_TIMEOUT_S = 3
MEDIA_WAIT_MAX_S = 240  # safety net: never let a question wait forever for its media to finish

AVATARS = ["🦊", "🐼", "🐯", "🦁", "🐸", "🐙", "🦄", "🐧", "🐵", "🐨", "🦉", "🐳",
           "🐢", "🦖", "🐝", "🦋", "🌵", "🍕", "🚀", "⚡", "🎸", "👾", "🤖", "🎩"]

BOT_NAMES = [
    "Ada", "Alan", "Grace", "Linus", "Margaret", "Dennis", "Barbara", "Ken", "Radia", "Tim",
    "Hedy", "Claude", "Katherine", "Vint", "Frances", "Guido", "Anita", "Bjarne", "Sophie", "Larry",
]


class GameError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def now_ms() -> float:
    return time.time() * 1000


def clean_nick(value) -> str:
    s = "".join(ch for ch in str(value or "") if ch.isprintable())
    return re.sub(r"\s+", " ", s).strip()[:20]


def clean_avatar(value) -> str:
    return value if value in AVATARS else random.choice(AVATARS)


async def _send(ws, msg) -> None:
    try:
        await asyncio.wait_for(ws.send_json(msg), SEND_TIMEOUT_S)
    except Exception:
        pass  # dead/slow socket; its receive loop will clean up


@dataclass
class Player:
    id: int
    token: str
    nickname: str
    email: str = ""
    is_bot: bool = False
    score: int = 0
    joined_at: float = 0.0
    kicked: bool = False
    avatar: str = ""
    streak: int = 0
    rtt_ms: float = 0.0
    sockets: set = field(default_factory=set)

    @property
    def active(self) -> bool:
        return self.is_bot or bool(self.sockets)


class Game:
    def __init__(self, db: Database, row: dict):
        self.db = db
        self.id = row["id"]
        self.code = row["code"]
        self.owner_id = row.get("owner_id")
        self.quiz_id = row.get("quiz_id")
        self.title = row["title"]
        self.description = row["description"]
        self.settings = {**DEFAULT_SETTINGS, **row["settings"]}
        self.questions: list[dict] = row["questions"]
        state = row.get("state") or {}
        self.phase = state.get("phase", "lobby")
        self.index = state.get("index", -1)
        # a projector opened with this key may tell the server "the media has ended"; plain /screen viewers can't
        self.screen_key = state.get("screen_key") or secrets.token_urlsafe(8)
        if row["status"] == "finished":
            self.phase = "finished"

        self.players: dict[int, Player] = {}
        self.by_token: dict[str, Player] = {}
        self.answers: dict[str, dict[int, dict]] = {}  # qid -> player_id -> answer

        self.hosts: set = set()
        self.screens: set = set()

        self.started_ms = 0.0
        self.ends_ms = 0.0
        self.paused_remaining: float | None = None
        self.holder: int | None = None
        self.lockouts: set[int] = set()
        self.buzz_order: list[int] = []
        self._buzz_pending: list[tuple[float, int]] = []
        self._rank_before: dict[int, int] = {}
        self.media_wait = False       # question is showing media; the answer clock hasn't started yet
        self.media_nonce = 0          # bumped by the host to make every screen replay the clip
        self._media_guard: asyncio.Task | None = None

        self._timer: asyncio.Task | None = None
        self._window: asyncio.Task | None = None
        self._bot_tasks: list[asyncio.Task] = []
        self._hs_push: asyncio.Task | None = None
        self._lobby_push: asyncio.Task | None = None

    # ---- construction / restore --------------------------------------------
    @classmethod
    def restore(cls, db: Database, row: dict) -> "Game":
        game = cls(db, row)
        for r in db.game_players(game.id):
            p = Player(r["id"], r["token"], r["nickname"], r["email"], bool(r["is_bot"]),
                       r["score"], r["joined_at"], bool(r["kicked"]), r["avatar"] or clean_avatar(None))
            game.players[p.id] = p
            game.by_token[p.token] = p
        for a in db.game_answers(game.id):
            game.answers.setdefault(a["qid"], {})[a["player_id"]] = {
                "choice": a["choice"], "correct": bool(a["correct"]),
                "points": a["points"], "elapsed_ms": a["elapsed_ms"], "at": (a.get("submitted_at") or 0) * 1000,
            }
        if game.phase == "question":  # interrupted by a restart: show its result instead
            game.phase = "reveal"
        game._recompute_streaks()
        return game

    def _recompute_streaks(self):
        done = range(0, self.index + (1 if self.phase in ("reveal", "finished") else 0))
        for p in self.players.values():
            p.streak = 0
            for i in done:
                if i >= len(self.questions):
                    break
                q = self.questions[i]
                a = self.answers.get(q["id"], {}).get(p.id)
                if q["type"] == "buzzer":
                    if a:
                        p.streak = p.streak + 1 if a["correct"] else 0
                else:
                    p.streak = p.streak + 1 if (a and a["correct"]) else 0

    # ---- small helpers --------------------------------------------------------
    @property
    def q(self) -> dict | None:
        if self.phase in ("question", "reveal") and 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    def _live_players(self) -> list[Player]:
        return [p for p in self.players.values() if not p.kicked]

    def _save_state(self, with_questions=False, with_settings=False):
        self.db.save_game_state(
            self.id, {"phase": self.phase, "index": self.index, "screen_key": self.screen_key},
            self.questions if with_questions else None,
            self.settings if with_settings else None,
        )

    def _ranks(self) -> dict:
        order = sorted(self._live_players(), key=lambda p: (-p.score, p.joined_at, p.id))
        return {"order": order, "rank": {p.id: i + 1 for i, p in enumerate(order)}}

    def _board(self, ranks: dict, n: int, deltas: bool = False) -> list[dict]:
        out = []
        for i, p in enumerate(ranks["order"][:n]):
            row = {"id": p.id, "nickname": p.nickname, "avatar": p.avatar, "score": p.score, "rank": i + 1}
            if deltas:
                row["delta"] = self._rank_before.get(p.id, i + 1) - (i + 1)
            out.append(row)
        return out

    def _speed_weight(self, q: dict) -> int:
        """Share (0-100) of the question's points that depends on answering quickly."""
        if not self.settings["speed_bonus"] or q["type"] == "buzzer":
            return 0
        return max(0, min(100, int(q.get("speed_weight", 50))))

    def _points(self, q: dict, elapsed_ms: float) -> int:
        """points = base x (1 - weight x elapsed/limit): instant answers earn full points, the last second earns
        (1 - weight) of them. weight 0 = flat scoring, 100 = a last-second answer earns nothing."""
        if q.get("winners") == 1 and q["type"] != "buzzer":
            return q["points"]                       # the one and only winner earns 100% - speed is irrelevant
        w = self._speed_weight(q) / 100
        frac = min(1.0, max(0.0, elapsed_ms / (q["time_limit"] * 1000)))
        return round(q["points"] * (1 - w * frac))

    # ---- joining ----------------------------------------------------------------
    def join(self, nickname, email="", token=None, bot=False, avatar=None) -> Player:
        if token and token in self.by_token:
            p = self.by_token[token]
            if p.kicked:
                raise GameError("You were removed from this game", 403)
            return p
        if self.phase == "finished":
            raise GameError("This game has already finished", 410)
        if len(self._live_players()) >= MAX_PLAYERS:
            raise GameError("This game is full", 403)
        name = clean_nick(nickname)
        if not name:
            raise GameError("Please enter a nickname")
        if any(p.nickname.lower() == name.lower() for p in self._live_players()):
            raise GameError("That nickname is already taken - try another")
        email = str(email or "").strip()[:120] if (self.settings["ask_email"] and not bot) else ""
        if self.settings["ask_email"] and not bot and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise GameError("Please enter a valid email address")
        token = secrets.token_urlsafe(16)
        joined = time.time()
        avatar = clean_avatar(avatar)
        pid = self.db.add_player(self.id, token, name, email, bot, joined, avatar)
        p = Player(pid, token, name, email, bot, 0, joined, False, avatar)
        self.players[pid] = p
        self.by_token[token] = p
        self.schedule_host_screen_push()
        return p

    # ---- sockets / pushing ---------------------------------------------------------
    async def connect(self, role: str, ws, player: Player | None = None):
        if role == "host":
            self.hosts.add(ws)
            await _send(ws, {"t": "state", "v": self.view_host(self._ranks())})
        elif role == "screen":
            self.screens.add(ws)
            await _send(ws, {"t": "state", "v": self.view_screen(self._ranks())})
        else:
            player.sockets.add(ws)
            await _send(ws, {"t": "state", "v": self.view_player(player, self._ranks())})
            self.schedule_host_screen_push()

    def disconnect(self, role: str, ws, player: Player | None = None):
        self.hosts.discard(ws)
        self.screens.discard(ws)
        if player:
            player.sockets.discard(ws)
            self.schedule_host_screen_push()

    def schedule_host_screen_push(self):
        """Coalesce bursts (join storms, answer floods) into one push per 100 ms."""
        self.schedule_lobby_push()
        if self._hs_push and not self._hs_push.done():
            return
        if not (self.hosts or self.screens):
            return

        async def run():
            await asyncio.sleep(0.1)
            await self.push(players=False)

        self._hs_push = asyncio.create_task(run())

    def schedule_lobby_push(self):
        """Keep the 'N players joined' counter on phones fresh without a join storm of pushes."""
        if self.phase != "lobby" or (self._lobby_push and not self._lobby_push.done()):
            return
        if not any(p.sockets for p in self.players.values()):
            return

        async def run():
            await asyncio.sleep(0.8)
            if self.phase == "lobby":
                await self.push(host=False, screen=False)

        self._lobby_push = asyncio.create_task(run())

    async def push(self, host=True, screen=True, players=True):
        ranks = self._ranks()
        jobs = []
        if host and self.hosts:
            v = {"t": "state", "v": self.view_host(ranks)}
            jobs += [_send(ws, v) for ws in list(self.hosts)]
        if screen and self.screens:
            v = {"t": "state", "v": self.view_screen(ranks)}
            jobs += [_send(ws, v) for ws in list(self.screens)]
        if players:
            for p in self.players.values():
                for ws in list(p.sockets):
                    jobs.append(_send(ws, {"t": "state", "v": self.view_player(p, ranks)}))
        if jobs:
            await asyncio.gather(*jobs)

    async def push_player(self, p: Player):
        ranks = self._ranks()
        await asyncio.gather(*[
            _send(ws, {"t": "state", "v": self.view_player(p, ranks)}) for ws in list(p.sockets)
        ])

    # ---- views -----------------------------------------------------------------------
    def _media_view(self, q: dict, role: str) -> dict | None:
        """The media descriptor each role gets. The file URL is only handed to devices that should play it,
        and only while the question is live (so clips stop at the reveal)."""
        m = q.get("media")
        if not m:
            return None
        plays = self.phase == "question" and (
            role == "host" or m["where"] == "both"
            or (m["where"] == "screen" and role == "screen") or (m["where"] == "players" and role == "player"))
        return {"kind": m["kind"], "where": m["where"], "url": m["url"] if plays else None, "timer_after": m["timer_after"]}

    def _public_q(self, role: str = "host") -> dict | None:
        q = self.q
        if not q:
            return None
        return {
            "id": q["id"], "index": self.index, "total": len(self.questions), "type": q["type"],
            "text": q["text"], "image": q["image"], "options": q["options"],
            "time_limit": q["time_limit"], "points": q["points"], "speed": self._speed_weight(q),
            "winners": q.get("winners", 0), "only_players": self._only_players(q),
            "spots_left": max(0, q["winners"] - self._correct_count(q)) if q.get("winners") else None,
            "media": self._media_view(q, role), "awaiting_media": self.media_wait and self.phase == "question",
            "media_nonce": self.media_nonce,
            "started_at": self.started_ms, "ends_at": self.ends_ms,
            "remaining_ms": self.paused_remaining,
        }

    def _allowed(self, q: dict) -> list:
        """Players who may answer this question: everybody, or only the tied players of a tie-breaker."""
        only = q.get("only")
        return [p for p in self._live_players() if not only or p.id in only]

    def _only_players(self, q: dict):
        only = q.get("only")
        if not only:
            return None
        return [{"id": i, "nickname": self.players[i].nickname, "avatar": self.players[i].avatar} for i in only if i in self.players]

    def _tie_groups(self, ranks: dict) -> list[dict]:
        """Players level on points at a podium place (1st-3rd): the cue for a tie-breaker."""
        order, groups, i = ranks["order"], [], 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and order[j + 1].score == order[i].score:
                j += 1
            if j > i and i < 3 and order[i].score > 0 and self.index >= 0:
                groups.append({"rank": i + 1, "score": order[i].score,
                               "players": [{"id": p.id, "nickname": p.nickname, "avatar": p.avatar} for p in order[i:j + 1]]})
            i = j + 1
        return groups

    def _correct_count(self, q: dict) -> int:
        return sum(1 for a in self.answers.get(q["id"], {}).values() if a["correct"])

    def _dist(self, q: dict) -> dict:
        dist = {o["id"]: 0 for o in q["options"]}
        for a in self.answers.get(q["id"], {}).values():
            for c in a["choice"]:
                if c in dist:
                    dist[c] += 1
        return dist

    def _reveal(self) -> dict | None:
        q = self.q
        if not q or self.phase != "reveal":
            return None
        ans = self.answers.get(q["id"], {})
        dist = self._dist(q)
        fastest = None
        if q["type"] != "buzzer":
            good = [(a["elapsed_ms"], pid) for pid, a in ans.items() if a["correct"]]
            if good:
                ms, pid = min(good)
                fastest = {"nickname": self.players[pid].nickname, "avatar": self.players[pid].avatar, "ms": ms}
        winner = next((self.players[pid].nickname for pid, a in ans.items() if a["correct"]), None) \
            if q["type"] == "buzzer" else None
        limit = q.get("winners", 0) if q["type"] != "buzzer" else 0
        claimed = []
        if limit:   # who got the scarce points, in the order their answers arrived, and when
            good = sorted(((a.get("at") or a["elapsed_ms"], pid, a) for pid, a in ans.items() if a["correct"]), key=lambda x: x[0])
            for rank, (_, pid, a) in enumerate(good[:limit], 1):
                claimed.append({"rank": rank, "nickname": self.players[pid].nickname, "avatar": self.players[pid].avatar,
                                "ms": a["elapsed_ms"], "at": a.get("at") or None, "points": a["points"]})
        return {
            "correct": q["correct"], "distribution": dist, "answered": len(ans),
            "correct_count": sum(1 for a in ans.values() if a["correct"]),
            "explanation": q["explanation"], "buzz_winner": winner, "fastest": fastest,
            "winners": limit, "claimed": claimed, "closed_by_limit": bool(limit) and len(claimed) >= limit,
        }

    def _buzz_view(self) -> dict | None:
        q = self.q
        if not q or q["type"] != "buzzer" or self.phase != "question":
            return None
        return {
            "holder": self.holder,
            "holder_name": self.players[self.holder].nickname if self.holder else None,
            "holder_avatar": self.players[self.holder].avatar if self.holder else None,
            "order": [self.players[pid].nickname for pid in self.buzz_order if pid in self.players],
        }

    def _common(self, ranks: dict) -> dict:
        return {
            "phase": self.phase, "title": self.title, "description": self.description,
            "code": self.code, "server_now": now_ms(), "players": len(ranks["order"]),
        }

    def view_player(self, p: Player, ranks: dict) -> dict:
        v = self._common(ranks)
        rank = ranks["rank"].get(p.id)
        v["me"] = {"id": p.id, "nickname": p.nickname, "avatar": p.avatar, "score": p.score, "rank": rank,
                   "streak": p.streak}
        q = self.q
        if q:
            v["q"] = self._public_q("player")
            v["q"]["can_play"] = (not q.get("only")) or p.id in q["only"]
            mine = self.answers.get(q["id"], {}).get(p.id)
            if self.phase == "question":
                v["answered"] = mine is not None and q["type"] != "buzzer"
                v["locked_out"] = p.id in self.lockouts
                b = self._buzz_view()
                if b:
                    b["holder_is_me"] = self.holder == p.id
                    v["buzz"] = b
            else:
                v["reveal"] = self._reveal()
                v["me"]["delta"] = self._rank_before.get(p.id, rank) - rank if rank else 0
                v["leaderboard"] = self._board(ranks, 5, deltas=True)
                v["result"] = {
                    "answered": mine is not None,
                    "correct": bool(mine and mine["correct"]),
                    "points": mine["points"] if mine else 0,
                    "max_points": q["points"],
                    "elapsed_ms": mine["elapsed_ms"] if mine else None,
                    "choice": mine["choice"] if mine else [],
                }
        if self.phase == "finished":
            v["leaderboard"] = self._board(ranks, 10)
            v["stats"] = self._stats(p)
        return v

    def _stats(self, p: Player) -> dict:
        asked = self.questions[: self.index + 1]
        rows = [self.answers.get(q["id"], {}).get(p.id) for q in asked]
        correct = [a for a in rows if a and a["correct"]]
        timed = [a["elapsed_ms"] for a, q in zip(rows, asked) if a and a["correct"] and q["type"] != "buzzer"]
        return {"correct": len(correct), "questions": len(asked), "fastest_ms": min(timed) if timed else None}

    def view_screen(self, ranks: dict) -> dict:
        v = self._common(ranks)
        v["leaderboard"] = self._board(ranks, 10, deltas=self.phase in ("reveal", "finished"))
        if self.phase == "lobby":
            v["names"] = [{"n": p.nickname, "a": p.avatar}
                          for p in sorted(self._live_players(), key=lambda p: p.joined_at)][-120:]
        q = self.q
        if q:
            v["q"] = self._public_q("screen")
            if self.phase == "question":
                eligible = [p for p in self._allowed(q) if p.active]
                v["answered"] = len(self.answers.get(q["id"], {}))
                v["eligible"] = len(eligible)
                v["buzz"] = self._buzz_view()
            else:
                v["reveal"] = self._reveal()
        return v

    def view_host(self, ranks: dict) -> dict:
        v = self.view_screen(ranks)
        v["game_id"] = self.id
        v["settings"] = self.settings
        v["index"] = self.index
        v["outline"] = [{"id": q["id"], "type": q["type"], "text": strip_tags(q["text"])[:90], "tie": bool(q.get("only"))} for q in self.questions]
        v["tie_groups"] = self._tie_groups(ranks)
        v["screen_key"] = self.screen_key
        v["players_list"] = [
            {"id": p.id, "nickname": p.nickname, "avatar": p.avatar, "email": p.email, "score": p.score,
             "rank": ranks["rank"][p.id], "connected": p.active, "is_bot": p.is_bot, "streak": p.streak}
            for p in ranks["order"]
        ]
        q = self.q
        if q:
            v["q"] = self._public_q("host")
            v["q_correct"] = q["correct"]
            v["q_explanation"] = q["explanation"]
            if self.phase == "question":
                v["answered"] = len(self.answers.get(q["id"], {}))
                v["live_dist"] = self._dist(q)
        return v

    # ---- timers ------------------------------------------------------------------------
    def _cancel(self, task: asyncio.Task | None):
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _cancel_bots(self):
        for t in self._bot_tasks:
            self._cancel(t)
        self._bot_tasks = []

    def _start_timer(self):
        self._cancel(self._timer)

        async def run():
            await asyncio.sleep(max(0.0, (self.ends_ms - now_ms()) / 1000))
            await self.end_question()

        self._timer = asyncio.create_task(run())

    # ---- state transitions ----------------------------------------------------------------
    async def next(self):
        if self.phase not in ("lobby", "reveal"):
            return
        if self.index + 1 >= len(self.questions):
            await self.finish()
            return
        self.index += 1
        await self._begin_question()

    async def _begin_question(self):
        self._cancel(self._timer)
        self._cancel(self._window)
        self._cancel(self._media_guard)
        self._cancel_bots()
        q = self.questions[self.index]
        self._rank_before = dict(self._ranks()["rank"])
        self.phase = "question"
        self.started_ms = now_ms()
        self.ends_ms = self.started_ms + q["time_limit"] * 1000
        self.holder = None
        self.lockouts = set()
        self.buzz_order = []
        self._buzz_pending = []
        m = q.get("media")
        # "start the answer timer when the media ends": freeze the clock at full time until the projector
        # (or the host) says the clip is over. Answers are refused meanwhile, so nobody can answer early.
        self.media_wait = bool(m and m.get("timer_after"))
        self.paused_remaining = q["time_limit"] * 1000 if self.media_wait else None
        self._save_state()
        if self.media_wait:
            self._media_guard = asyncio.create_task(self._media_guard_run())
        else:
            self._start_timer()
            self._start_bots(q)
        await self.push()

    async def _media_guard_run(self):
        await asyncio.sleep(MEDIA_WAIT_MAX_S)
        await self.start_after_media()

    async def start_after_media(self):
        """The clip finished (reported by the projector) or the host pressed 'Start timer now'."""
        q = self.q
        if self.phase != "question" or not self.media_wait or not q:
            return
        self._cancel(self._media_guard)
        self.media_wait = False
        self.started_ms = now_ms()                      # speed is measured from here, not from when the clip began
        self.ends_ms = self.started_ms + q["time_limit"] * 1000
        self.paused_remaining = None
        self._start_timer()
        self._start_bots(q)
        await self.push()

    async def replay_media(self):
        if self.phase == "question" and self.q and self.q.get("media"):
            self.media_nonce += 1
            await self.push()

    async def end_question(self):
        if self.phase != "question":
            return
        self._cancel(self._timer)
        self._cancel(self._window)
        self._cancel(self._media_guard)
        self._cancel_bots()
        self.media_wait = False
        q = self.q
        if q and q["type"] != "buzzer":
            for p in self._allowed(q):
                if p.id not in self.answers.get(q["id"], {}):
                    p.streak = 0
        self.phase = "reveal"
        self.holder = None
        self.paused_remaining = None
        self._buzz_pending = []
        self._save_state()
        await self.push()

    async def finish(self):
        if self.phase == "finished":
            return
        self._cancel(self._timer)
        self._cancel(self._window)
        self._cancel(self._media_guard)
        self._cancel_bots()
        self.media_wait = False
        self.phase = "finished"
        self.holder = None
        self._save_state()
        self.db.finish_game(self.id)
        await self.push()

    async def add_time(self, seconds):
        if self.phase != "question" or self.media_wait:
            return
        extra = max(-30, min(120, int(seconds))) * 1000
        if self.holder is not None:
            self.paused_remaining = max(1000.0, (self.paused_remaining or 0) + extra)
        else:
            self.ends_ms = max(now_ms() + 1000, self.ends_ms + extra)
            self._start_timer()
        await self.push()

    # ---- answering ----------------------------------------------------------------------------
    async def submit(self, p: Player, choice):
        q = self.q
        if self.phase != "question" or not q or q["type"] == "buzzer" or p.kicked or self.media_wait:
            return
        if q.get("only") and p.id not in q["only"]:
            return                                     # a tie-breaker: only the tied players may answer
        if p.id in self.answers.get(q["id"], {}):
            return
        raw = choice if isinstance(choice, list) else [choice]
        valid = {o["id"] for o in q["options"]}
        picked = [c for c in dict.fromkeys(raw) if isinstance(c, str) and c in valid]
        if not picked or (q["type"] == "mcq" and len(picked) != 1):
            return
        now = now_ms()
        if now > self.ends_ms + ANSWER_GRACE_MS:
            return
        # the player saw the question about rtt/2 after we sent it
        elapsed = max(0.0, now - self.started_ms - min(p.rtt_ms, 1000) / 2)
        elapsed = min(elapsed, q["time_limit"] * 1000)
        correct = set(picked) == set(q["correct"])
        pts = self._points(q, elapsed) if correct else 0
        if not self.db.add_answer(self.id, p.id, q["id"], self.index, picked, correct, pts, int(elapsed), now / 1000):
            return
        self.answers.setdefault(q["id"], {})[p.id] = {
            "choice": picked, "correct": correct, "points": pts, "elapsed_ms": int(elapsed), "at": now}
        p.streak = p.streak + 1 if correct else 0
        if pts:
            p.score += pts
            self.db.set_player_score(p.id, p.score)

        limit = q.get("winners", 0)
        if correct and limit:
            if self._correct_count(q) >= limit:       # the last scoring place was just taken: close the question
                await self.end_question()
                return
            await self.push()                         # a place is gone but others remain: everyone sees "N left"
            return

        eligible = [x for x in self._allowed(q) if x.active]
        answered = self.answers[q["id"]]
        if self.settings["auto_reveal"] and eligible and all(x.id in answered for x in eligible):
            await self.end_question()
            return
        await self.push_player(p)
        self.schedule_host_screen_push()

    async def buzz(self, p: Player):
        q = self.q
        if self.phase != "question" or not q or q["type"] != "buzzer" or p.kicked:
            return
        if q.get("only") and p.id not in q["only"]:
            return
        if self.holder is not None or p.id in self.lockouts:
            return
        if any(pid == p.id for _, pid in self._buzz_pending):
            return
        self._buzz_pending.append((now_ms() - min(p.rtt_ms, 500) / 2, p.id))
        if self._window is None or self._window.done():
            self._window = asyncio.create_task(self._close_buzz_window())

    async def _close_buzz_window(self):
        await asyncio.sleep(BUZZ_WINDOW_S)
        pending = sorted(self._buzz_pending)
        self._buzz_pending = []
        if self.phase != "question" or self.holder is not None or not pending:
            return
        self.buzz_order = [pid for _, pid in pending]
        self.holder = pending[0][1]
        self.paused_remaining = max(0.0, self.ends_ms - now_ms())
        self._cancel(self._timer)
        await self.push()

    async def resolve_buzz(self, correct: bool):
        q = self.q
        if self.phase != "question" or not q or self.holder is None:
            return
        p = self.players[self.holder]
        elapsed = int(max(0.0, now_ms() - self.started_ms))
        pts = q["points"] if correct else 0
        if self.db.add_answer(self.id, p.id, q["id"], self.index, ["buzz"], correct, pts, elapsed, now_ms() / 1000):
            self.answers.setdefault(q["id"], {})[p.id] = {
                "choice": ["buzz"], "correct": bool(correct), "points": pts, "elapsed_ms": elapsed, "at": now_ms()}
        p.streak = p.streak + 1 if correct else 0
        if correct:
            p.score += pts
            self.db.set_player_score(p.id, p.score)
            await self.end_question()
            return
        self.lockouts.add(p.id)
        self.holder = None
        self.buzz_order = []
        remaining = max(3000.0, self.paused_remaining or 0.0)
        self.paused_remaining = None
        self.ends_ms = now_ms() + remaining
        if not any(x.active and x.id not in self.lockouts for x in self._allowed(q)):
            await self.end_question()
            return
        self._start_timer()
        await self.push()

    # ---- host extras ----------------------------------------------------------------------------
    async def _reopen(self):
        """Bring a finished game back to life so a tie-breaker (or one more question) can be played."""
        if self.phase != "finished":
            return
        self.db.reopen_game(self.id)
        self.phase = "reveal" if self.index >= 0 else "lobby"
        self.index = min(self.index, len(self.questions) - 1)
        self.holder, self.paused_remaining, self.media_wait = None, None, False

    def _save_to_quiz(self, q: dict, after_id, where: str) -> bool:
        """Copy a question added during the game into the quiz it came from (without any tie-breaker restriction)."""
        quiz = self.db.get_quiz(self.quiz_id) if self.quiz_id else None
        if not quiz:
            return False
        clean = {k: v for k, v in q.items() if k != "only"}
        questions = quiz["questions"]
        ids = [x["id"] for x in questions]
        pos = ids.index(after_id) + 1 if (where != "end" and after_id in ids) else len(questions)
        questions.insert(pos, clean)
        self.db.update_quiz(self.quiz_id, {"title": quiz["title"], "description": quiz["description"], "questions": questions})
        return True

    async def add_question(self, raw: dict, only=None, where: str = "next", save: bool = False):
        """Add a question while the game runs: next in line (default) or at the very end, optionally for just a few
        players (a tie-breaker) and optionally pushed into the quiz library as well. Works after the game finished too."""
        reopened = self.phase == "finished"
        try:
            q = validate_question(raw)
        except ValidationError as e:
            raise GameError(str(e)) from None
        if only is not None and only != []:
            ids = [i for i in (only if isinstance(only, list) else []) if isinstance(i, int) and i in self.players and not self.players[i].kicked]
            if not ids:
                raise GameError("Pick at least one player for a restricted question")
            q["only"] = ids
        if reopened:
            await self._reopen()
        prev = self.questions[self.index]["id"] if 0 <= self.index < len(self.questions) else None
        pos = len(self.questions) if where == "end" else min(self.index + 1, len(self.questions))
        self.questions.insert(pos, q)
        self._save_state(with_questions=True)
        await self.push()
        if save and not self._save_to_quiz(q, prev, where):
            raise GameError("Added to this game, but the quiz no longer exists, so it could not be saved there.")

    async def kick(self, player_id):
        p = self.players.get(player_id)
        if not p or p.kicked:
            return
        p.kicked = True
        self.db.kick_player(p.id)
        self.lockouts.discard(p.id)
        for ws in list(p.sockets):
            try:
                await ws.close(code=4403)
            except Exception:
                pass
        p.sockets.clear()
        if self.holder == p.id:
            self.holder = None
            if self.phase == "question":
                self.ends_ms = now_ms() + max(3000.0, self.paused_remaining or 0.0)
                self.paused_remaining = None
                self._start_timer()
        await self.push()

    async def adjust_score(self, player_id, delta):
        p = self.players.get(player_id)
        if not p or p.kicked:
            return
        p.score = max(0, p.score + max(-100000, min(100000, int(delta))))
        self.db.set_player_score(p.id, p.score)
        await self.push()

    async def set_setting(self, key, value):
        if key not in DEFAULT_SETTINGS or not isinstance(value, bool):
            return
        self.settings[key] = value
        self._save_state(with_settings=True)
        await self.push(players=False)

    async def add_bots(self, n):
        n = max(1, min(int(n), 50))
        existing = sum(1 for p in self.players.values() if p.is_bot)
        for _ in range(n):
            if existing >= MAX_BOTS:
                break
            for _try in range(20):
                base = random.choice(BOT_NAMES)
                name = f"{base} (bot)" if _try == 0 else f"{base} {random.randint(2, 99)} (bot)"
                try:
                    self.join(name, bot=True)
                    existing += 1
                    break
                except GameError:
                    continue
        await self.push(players=False)

    def _start_bots(self, q: dict):
        qid = q["id"]
        for bot in [p for p in self._allowed(q) if p.is_bot]:
            self._bot_tasks.append(asyncio.create_task(self._bot_act(bot, qid)))

    async def _bot_act(self, bot: Player, qid: str):
        q = self.q
        if not q or q["id"] != qid:
            return
        if q["type"] == "buzzer":
            if random.random() < 0.15:
                await asyncio.sleep(random.uniform(1.0, max(1.5, q["time_limit"] * 0.6)))
                await self.buzz(bot)
            return
        await asyncio.sleep(random.uniform(1.0, max(1.5, q["time_limit"] * 0.8)))
        q = self.q
        if self.phase != "question" or not q or q["id"] != qid:
            return
        ids = [o["id"] for o in q["options"]]
        if random.random() < 0.55:
            choice = list(q["correct"])
        elif q["type"] == "multi":
            choice = random.sample(ids, random.randint(1, len(ids)))
        else:
            choice = [random.choice(ids)]
        await self.submit(bot, choice)

    # ---- message dispatch --------------------------------------------------------------------------
    async def on_host(self, msg: dict):
        t = msg.get("t")
        if t == "next":
            await self.next()
        elif t == "end_question":
            await self.end_question()
        elif t == "finish":
            await self.finish()
        elif t == "add_time":
            await self.add_time(msg.get("seconds", 10))
        elif t == "start_timer":
            await self.start_after_media()
        elif t == "replay_media":
            await self.replay_media()
        elif t == "resolve_buzz":
            await self.resolve_buzz(bool(msg.get("correct")))
        elif t == "kick":
            await self.kick(msg.get("player_id"))
        elif t == "adjust_score":
            await self.adjust_score(msg.get("player_id"), msg.get("delta", 0))
        elif t == "add_bots":
            await self.add_bots(msg.get("n", 10))
        elif t == "add_question":
            await self.add_question(msg.get("question"), msg.get("only"), "end" if msg.get("where") == "end" else "next", bool(msg.get("save_to_quiz")))
        elif t == "set":
            await self.set_setting(msg.get("key"), msg.get("value"))

    async def on_screen(self, msg: dict, controller: bool):
        """Only a projector opened from the host console (it carries the game's screen key) may drive the game."""
        if controller and msg.get("t") == "media_ended":
            await self.start_after_media()

    async def on_player(self, p: Player, msg: dict):
        t = msg.get("t")
        if t == "answer":
            await self.submit(p, msg.get("choice"))
        elif t == "buzz":
            await self.buzz(p)
        elif t == "rtt":
            try:
                ms = max(0.0, min(2000.0, float(msg.get("ms", 0))))
            except (TypeError, ValueError):
                return
            p.rtt_ms = ms if p.rtt_ms == 0 else 0.7 * p.rtt_ms + 0.3 * ms


class GameManager:
    def __init__(self, db: Database):
        self.db = db
        self.games: dict[str, Game] = {}

    def restore(self):
        for row in self.db.open_games():
            game = Game.restore(self.db, row)
            self.games[game.code] = game

    def get(self, code: str) -> Game | None:
        return self.games.get(str(code or "").strip())

    def create(self, quiz_id: int, settings: dict | None = None, owner_id: int | None = None) -> Game:
        quiz = self.db.get_quiz(quiz_id)
        if not quiz:
            raise GameError("Quiz not found", 404)
        if not quiz["questions"]:
            raise GameError("Add at least one question before starting a game")
        merged = dict(DEFAULT_SETTINGS)
        for k, v in (settings or {}).items():
            if k in merged and isinstance(v, bool):
                merged[k] = v
        while True:
            code = str(secrets.randbelow(90000) + 10000)
            if code not in self.games:
                break
        game_id = self.db.create_game(code, quiz_id, quiz["title"], quiz["description"], merged, quiz["questions"], owner_id)
        game = Game(self.db, self.db.get_game(game_id))
        self.games[code] = game
        return game


def game_results(db: Database, game_id: int) -> dict:
    """Leaderboard + per-question analytics for any (even finished/restarted) game."""
    game = db.get_game(game_id)
    if not game:
        return {}
    players = [p for p in db.game_players(game_id) if not p["kicked"]]
    answers = db.game_answers(game_id)
    by_player: dict[int, list[dict]] = {}
    by_q: dict[str, list[dict]] = {}
    for a in answers:
        by_player.setdefault(a["player_id"], []).append(a)
        by_q.setdefault(a["qid"], []).append(a)
    players.sort(key=lambda p: (-p["score"], p["joined_at"], p["id"]))
    board = [{
        "id": p["id"], "rank": i + 1, "nickname": p["nickname"], "avatar": p["avatar"], "email": p["email"], "score": p["score"],
        "correct": sum(1 for a in by_player.get(p["id"], []) if a["correct"]),
        "answered": len(by_player.get(p["id"], [])), "is_bot": bool(p["is_bot"]),
    } for i, p in enumerate(players)]
    analytics = []
    for i, q in enumerate(game["questions"]):
        rows = by_q.get(q["id"], [])
        correct = [a for a in rows if a["correct"]]
        analytics.append({
            "index": i, "text": strip_tags(q["text"])[:120], "type": q["type"], "answered": len(rows),
            "correct": len(correct),
            "pct_correct": round(100 * len(correct) / len(rows)) if rows else None,
            "avg_seconds": round(sum(a["elapsed_ms"] for a in correct) / len(correct) / 1000, 1) if correct else None,
        })
    return {
        "game": {"id": game["id"], "code": game["code"], "title": game["title"], "quiz_id": game["quiz_id"], "owner_id": game.get("owner_id"),
                 "status": game["status"], "created_at": game["created_at"]},
        "leaderboard": board, "analytics": analytics,
    }


def player_detail(db: Database, game_id: int, player_id: int) -> dict | None:
    """One player's whole game: every question, what they picked, the right answer, how long they took, when they
    answered and the points they earned (with the running total) - for the results page drill-down."""
    game = db.get_game(game_id)
    if not game:
        return None
    players = [p for p in db.game_players(game_id) if not p["kicked"]]
    me = next((p for p in players if p["id"] == player_id), None)
    if not me:
        return None
    order = sorted(players, key=lambda p: (-p["score"], p["joined_at"], p["id"]))
    rank = 1 + next(i for i, p in enumerate(order) if p["id"] == player_id)
    all_answers = db.game_answers(game_id)
    mine = {a["qid"]: a for a in all_answers if a["player_id"] == player_id}
    by_q: dict[str, list[dict]] = {}
    for a in all_answers:
        by_q.setdefault(a["qid"], []).append(a)
    last_index = (game.get("state") or {}).get("index", -1)

    rows, running = [], 0
    for i, q in enumerate(game["questions"]):
        if i > last_index and q["id"] not in by_q:
            continue                                             # never asked (the game ended early)
        a = mine.get(q["id"])
        only = q.get("only")
        earned = a["points"] if a else 0
        running += earned
        chosen = set(a["choice"]) if a else set()
        options = [{"index": k, "text": o["text"], "chosen": o["id"] in chosen, "correct": o["id"] in q["correct"]}
                   for k, o in enumerate(q["options"])]
        claim_rank = None
        if q.get("winners") and q["type"] != "buzzer" and a and a["correct"]:
            good = sorted((x for x in by_q.get(q["id"], []) if x["correct"]), key=lambda x: (x["submitted_at"] or x["elapsed_ms"]))
            claim_rank = 1 + next(k for k, x in enumerate(good) if x["player_id"] == player_id)
        rows.append({
            "index": i, "text": q["text"], "type": q["type"], "options": options, "time_limit": q["time_limit"],
            "max_points": q["points"], "speed_weight": q.get("speed_weight", 0), "winners": q.get("winners", 0),
            "tie_breaker": bool(only), "not_in_round": bool(only) and player_id not in only,
            "answered": a is not None, "correct": bool(a and a["correct"]), "buzzed": bool(a and a["choice"] == ["buzz"]),
            "earned": earned, "running": running, "elapsed_ms": a["elapsed_ms"] if a else None,
            "submitted_at": (a["submitted_at"] * 1000) if a and a["submitted_at"] else None, "claim_rank": claim_rank,
        })
    answered = [r for r in rows if r["answered"]]
    right = [r for r in rows if r["correct"]]
    timed = [r["elapsed_ms"] for r in right if r["type"] != "buzzer" and r["elapsed_ms"] is not None]
    asked = [r for r in rows if not r["not_in_round"]]
    return {
        "player": {"id": me["id"], "nickname": me["nickname"], "avatar": me["avatar"], "email": me["email"], "is_bot": bool(me["is_bot"])},
        "game": {"id": game["id"], "title": game["title"], "code": game["code"]},
        "summary": {
            "rank": rank, "players": len(players), "score": me["score"], "earned": running, "adjustment": me["score"] - running,
            "asked": len(asked), "answered": len(answered), "correct": len(right),
            "accuracy": round(100 * len(right) / len(asked)) if asked else None,
            "avg_correct_ms": round(sum(timed) / len(timed)) if timed else None, "fastest_ms": min(timed) if timed else None,
        },
        "questions": rows,
    }
