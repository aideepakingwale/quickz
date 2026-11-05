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

from . import grading
from .db import Database
from .questions import ValidationError, strip_tags, validate_question

SKINS = ("classic", "ocean", "sunset", "forest", "royal", "midnight")
SOUND_PACKS = ("gameshow", "arcade", "quiet")
REVEAL_PAUSES = (0, 2, 3, 5)                     # seconds of "final answers locked" suspense before the answer is shown
REACTIONS = ("👏", "😂", "🔥", "😮", "🎉", "💪", "❤️", "😍", "🤩", "🥳", "🤯", "😎", "🙌", "👍", "👎", "🤣", "😅", "😬", "😱", "🥲", "😭", "🤔", "🙈", "👀", "😴", "🙏", "🤞", "🤝", "🫡", "💯", "⭐", "🏆", "🥇", "👑", "🚀", "🧠", "💡", "🎯", "⚡", "🌈", "🍀", "🍿", "☕", "🎸", "🦄")      # what phones may throw at the big screen (keep in step with REACTS in play.js)
CUES = ("drumroll", "applause", "aww", "ding", "wrong", "airhorn", "fanfare", "tick")   # the host's soundboard
REACT_GAP_S = 1.0                                # one reaction per player per second
REACT_BATCH_MAX = 24                             # most reactions shown per 0.4 s; a bigger crowd is sampled
CROWD_MAX = 150                                  # avatars sampled for the "fly into the bars" reveal
DEFAULT_SETTINGS = {"speed_bonus": True, "auto_reveal": True, "ask_email": False, "real_names": False, "jokers": False, "skin": "classic",
                    "reveal_pause": 0, "sound_pack": "gameshow", "reactions": False, "phone_music": True}
SCORED = grading.SCORED_TYPES                 # types with a right answer
LIVE_PHASES = ("question", "reveal", "slide")
BUZZ_WINDOW_S = 0.12
ANSWER_GRACE_MS = 300
MAX_PLAYERS = 500
MAX_BOTS = 200
SEND_TIMEOUT_S = 3
MEDIA_WAIT_MAX_S = 240  # safety net: never let a question wait forever for its media to finish

# The avatar library, in the groups the join screen shows as tabs. Plain emoji only (no skin-tone or joined sequences) so every phone draws them.
AVATAR_GROUPS = [
    ("Animals", ["🦊", "🐼", "🐯", "🦁", "🐸", "🐙", "🦄", "🐧", "🐵", "🐨", "🦉", "🐳", "🐢", "🦖", "🐝", "🦋",
                 "🐶", "🐱", "🐰", "🐻", "🐮", "🐷", "🦅", "🐬"]),
    ("More animals", ["🦈", "🦒", "🦓", "🐘", "🦘", "🦥", "🦦", "🐞", "🦜", "🐊", "🐺", "🦔", "🐔", "🦆", "🦩", "🦚",
                      "🐴", "🐫", "🦀", "🦑", "🐌", "🐭", "🐹", "🦇"]),
    ("Food", ["🍕", "🍔", "🍟", "🌮", "🍩", "🍪", "🍦", "🍉", "🍓", "🍒", "🥑", "🍌", "🍍", "🥝", "🍇", "🥐",
              "🧁", "🍿", "🥨", "☕", "🍫", "🍜", "🥕", "🍰"]),
    ("Sport & games", ["⚽", "🏀", "🏈", "⚾", "🎾", "🏏", "🏑", "🏓", "🥊", "🎯", "🎳", "🏆", "🎮", "🎲", "🧩", "🏄",
                       "🚴", "⛳", "🏸", "🥇", "🎱", "🛹", "🥋", "🏹"]),
    ("Space & nature", ["🚀", "🌵", "⚡", "🌈", "🌞", "🌙", "⭐", "🔥", "🌊", "🌸", "🌻", "🍀", "🌴", "🌋", "🪐", "🌍",
                        "🌟", "🍁", "🌲", "🍄", "🛸", "🔭", "⛅", "🌺"]),
    ("Fun", ["🎸", "👾", "🤖", "🎩", "🎺", "🥁", "🎧", "🎨", "🎬", "📚", "💡", "🔮", "🧲", "🎈", "🎁", "👑",
             "💎", "🧭", "⏰", "🎻", "🎹", "🎠", "🎡", "🎪"]),
    ("Heroes & legends", ["🧙", "🧛", "🧜", "🧚", "🦸", "🦹", "🥷", "🧞", "🐉", "👻", "🤠", "🥸", "👽", "🎃", "🧝", "🤴",
                          "👸", "🎭", "🗿", "💀", "👹", "🧟", "🦾", "🐲"]),
]
AVATARS = list(dict.fromkeys(a for _, items in AVATAR_GROUPS for a in items))
_AVATAR_SET = frozenset(AVATARS)
NAME_MAX = 20                  # nicknames
REAL_NAME_MAX = 40             # a full first-and-last name needs more room
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

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


def clean_nick(value, limit: int = NAME_MAX) -> str:
    s = "".join(ch for ch in str(value or "") if ch.isprintable())
    return re.sub(r"\s+", " ", s).strip()[:limit]


def is_full_name(name: str) -> bool:
    """A first and a last name: at least two words that contain a letter."""
    return sum(1 for w in name.split() if any(c.isalpha() for c in w)) >= 2


def clean_setting(key, value) -> tuple[bool, object]:
    """(accepted, value) for one game setting: a colour theme, a sound pack, a pause length, or an on/off switch."""
    if key == "skin":
        return value in SKINS, value
    if key == "sound_pack":
        return value in SOUND_PACKS, value
    if key == "reveal_pause":
        return isinstance(value, int) and not isinstance(value, bool) and value in REVEAL_PAUSES, value
    if key in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[key], bool):
        return isinstance(value, bool), value
    return False, None


def clean_avatar(value) -> str:
    return value if isinstance(value, str) and value in _AVATAR_SET else random.choice(AVATARS)


async def _send(ws, msg) -> None:
    try:
        if hasattr(asyncio, "timeout"):
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await ws.send_json(msg)
        else:  # Python < 3.11
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
    joker_qid: str | None = None      # the question this player "doubled up" on (one per game)
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
        self._look_at = 0.0
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
        self.runs: dict[str, int] = {}                 # qid -> how many times the host ran it again (phones use it to tell rounds apart)
        self.hints: dict[str, set[int]] = {}           # qid -> players who paid marks to see the finished jigsaw picture

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
        # With hundreds of players the same work used to be repeated per player. These make it once per change / push:
        self._rank_ver = 0            # bumped whenever a score, a join or a kick can change the leaderboard
        self._rank_cache: tuple | None = None
        self._memo: dict | None = None  # per-push scratch (the reveal, the shared part of the question view...); only set while building views
        self._num_cache: tuple | None = None
        self._active_ids: set[int] = set()   # players with an open socket, and bots
        self.media_wait = False       # question is showing media; the answer clock hasn't started yet
        self.media_nonce = 0          # bumped by the host to make every screen replay the clip
        self._media_guard: asyncio.Task | None = None

        self.suspense = False         # "final answers locked": a drum-roll pause between the end of the clock and the reveal
        self.suspense_until = 0.0
        self.buzz_ms: dict[int, int] = {}                  # player id -> reaction time of the buzzers that landed in the photo-finish window
        self._react_buf: list[dict] = []                   # emoji thrown by phones (with who threw them), batched into one message
        self._react_last: dict[int, float] = {}
        self._react_task: asyncio.Task | None = None
        self._cue_last = 0.0

        self._timer: asyncio.Task | None = None
        self._window: asyncio.Task | None = None
        self._suspense_task: asyncio.Task | None = None
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
            if p.is_bot and not p.kicked:
                game._active_ids.add(p.id)
        for a in db.game_answers(game.id):
            game.answers.setdefault(a["qid"], {})[a["player_id"]] = {
                "choice": a["choice"], "correct": bool(a["correct"]),
                "points": a["points"], "elapsed_ms": a["elapsed_ms"], "at": (a.get("submitted_at") or 0) * 1000,
                "joker": bool(a.get("joker")),
            }
            if a.get("joker") and a["player_id"] in game.players:
                game.players[a["player_id"]].joker_qid = a["qid"]
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
                if q["type"] not in SCORED and q["type"] != "buzzer":
                    continue                                   # slides and polls neither build nor break a streak
                a = self.answers.get(q["id"], {}).get(p.id)
                if q["type"] == "buzzer":
                    if a:
                        p.streak = p.streak + 1 if a["correct"] else 0
                else:
                    p.streak = p.streak + 1 if (a and a["correct"]) else 0

    # ---- small helpers --------------------------------------------------------
    @property
    def q(self) -> dict | None:
        if self.phase in LIVE_PHASES and 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    def _numbering(self, index: int) -> tuple[int, int]:
        """(position, total) counting only real questions - a round-title slide is not 'question 5 of 12'."""
        if not self._num_cache or self._num_cache[0] != len(self.questions):
            running, table = 0, []
            for x in self.questions:
                running += x["type"] != "slide"
                table.append(0 if x["type"] == "slide" else running)
            self._num_cache = (len(self.questions), table, running)
        return self._num_cache[1][index], self._num_cache[2]

    def _live_players(self) -> list[Player]:
        return [p for p in self.players.values() if not p.kicked]

    def _save_state(self, with_questions=False, with_settings=False):
        self.db.save_game_state(
            self.id, {"phase": self.phase, "index": self.index, "screen_key": self.screen_key},
            self.questions if with_questions else None,
            self.settings if with_settings else None,
        )

    def _ranks(self, stale_ok: bool = False) -> dict:
        if self._rank_cache and (stale_ok or self._rank_cache[0] == self._rank_ver):
            return self._rank_cache[1]
        order = sorted(self._live_players(), key=lambda p: (-p.score, p.joined_at, p.id))
        ranks = {"order": order, "rank": {p.id: i + 1 for i, p in enumerate(order)}}
        self._rank_cache = (self._rank_ver, ranks)
        return ranks

    def _board(self, ranks: dict, n: int, deltas: bool = False) -> list[dict]:
        memo = self._memo
        if memo is not None and ("board", n, deltas) in memo:
            return memo[("board", n, deltas)]
        out = self._build_board(ranks, n, deltas)
        if memo is not None:
            memo[("board", n, deltas)] = out
        return out

    def _build_board(self, ranks: dict, n: int, deltas: bool) -> list[dict]:
        out = []
        for i, p in enumerate(ranks["order"][:n]):
            row = {"id": p.id, "nickname": p.nickname, "avatar": p.avatar, "score": p.score, "rank": i + 1}
            if deltas:
                row["delta"] = self._rank_before.get(p.id, i + 1) - (i + 1)
            out.append(row)
        return out

    def _speed_weight(self, q: dict) -> int:
        """Share (0-100) of the question's points that depends on answering quickly."""
        if not self.settings["speed_bonus"] or q["type"] not in SCORED:
            return 0
        return max(0, min(100, int(q.get("speed_weight", 50))))

    def _points(self, q: dict, elapsed_ms: float, place: int | None = None) -> int:
        """points = base x (1 - weight x elapsed/limit): instant answers earn full points, the last second earns
        (1 - weight) of them. weight 0 = flat scoring, 100 = a last-second answer earns nothing.
        A question with marks by place (100, 70, 40...) pays the 1st correct answer the first share, the 2nd the second..."""
        weights = q.get("place_weights") or []
        if weights and place:
            return round(q["points"] * weights[min(place, len(weights)) - 1] / 100)
        if q.get("winners") == 1 and q["type"] in SCORED:
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
        real = self.settings["real_names"] and not bot
        name = clean_nick(nickname, REAL_NAME_MAX if real else NAME_MAX)
        if not name:
            raise GameError("Please enter your full name" if real else "Please enter a nickname")
        if real and not is_full_name(name):
            raise GameError("Please enter your first and last name")
        ask = self.settings["ask_email"] and not bot
        email = str(email or "").strip()[:120] if ask else ""
        if ask and not EMAIL_RE.fullmatch(email):
            raise GameError("Please enter a valid email address")
        live = self._live_players()
        if email:
            # One registration per email. Nothing is emailed, so the email is not proof of identity: the same email AND name
            # again (a new phone, cleared browser) simply picks the registration up; the same email under another name is refused.
            for p in live:
                if p.email.lower() == email.lower():
                    if re.fullmatch(re.escape(name.lower()) + r"( \(\d+\))?", p.nickname.lower()):
                        return p
                    raise GameError("That email is already registered in this game under another name")
        if len(live) >= MAX_PLAYERS:
            raise GameError("This game is full", 403)
        taken = {p.nickname.lower() for p in live}
        if name.lower() in taken:
            if not (real and email):
                raise GameError("That name is already taken - try another" if real else "That nickname is already taken - try another")
            n = 2                                       # two different people can share a name: tell them apart on the big screen
            while f"{name} ({n})".lower() in taken:
                n += 1
            name = f"{name} ({n})"
        token = secrets.token_urlsafe(16)
        joined = time.time()
        avatar = clean_avatar(avatar)
        pid = self.db.add_player(self.id, token, name, email, bot, joined, avatar)
        p = Player(pid, token, name, email, bot, 0, joined, False, avatar)
        self.players[pid] = p
        if bot:
            self._active_ids.add(pid)
        self._rank_ver += 1
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
            self._active_ids.add(player.id)
            await _send(ws, {"t": "state", "v": self.view_player(player, self._ranks())})
            self.schedule_host_screen_push()

    def disconnect(self, role: str, ws, player: Player | None = None):
        self.hosts.discard(ws)
        self.screens.discard(ws)
        if player:
            player.sockets.discard(ws)
            if not player.sockets and not player.is_bot:
                self._active_ids.discard(player.id)
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
        self._memo = {}                 # views are built synchronously below, so nothing can change under this scratch space
        try:
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
        finally:
            self._memo = None
        if jobs:
            await asyncio.gather(*jobs)

    async def push_player(self, p: Player):
        ranks = self._ranks(stale_ok=self.phase == "question")   # "3rd of 120" can lag by a few answers until the reveal; saves a full sort per answer
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
        plays = self.phase in ("question", "slide") and (
            role == "host" or m["where"] == "both"
            or (m["where"] == "screen" and role == "screen") or (m["where"] == "players" and role == "player"))
        out = {"kind": m["kind"], "where": m["where"], "url": m["url"] if plays else None, "timer_after": m["timer_after"]}
        if m["kind"] == "embed" and plays:         # which video it is stays secret from devices that should not play it
            out.update(provider=m["provider"], id=m["id"], hash=m.get("hash", ""), start=m.get("start", 0), end=m.get("end", 0), cover=bool(m.get("cover")))
        return out

    def _preload(self) -> list[str]:
        memo = self._memo
        if memo is not None and "preload" in memo:
            return memo["preload"]
        urls = self._compute_preload()
        if memo is not None:
            memo["preload"] = urls
        return urls

    def _compute_preload(self) -> list[str]:
        """Pictures of the NEXT question (and the explanation picture of this one) so phones can fetch them quietly in
        the background instead of all at once when the question appears. Pictures that are part of a puzzle stay hidden."""
        urls: list[str] = []
        cur = self.q
        if cur and self.phase == "question" and cur.get("explain_image"):
            urls.append(cur["explain_image"])
        i = self.index + 1
        if 0 <= i < len(self.questions):
            nxt = self.questions[i]
            if nxt.get("image") and nxt.get("image_effect", "none") == "none":
                urls.append(nxt["image"])
            urls += [o["image"] for o in (nxt.get("options") or []) + (nxt.get("targets") or []) if o.get("image")]
        return list(dict.fromkeys(urls))[:16]

    @staticmethod
    def _item(o: dict) -> dict:
        return {"id": o["id"], "text": o["text"], "image": o.get("image", "")}

    @staticmethod
    def _shuffled(items: list, q: dict, who: int | None) -> list:
        """A stable shuffle per player (and one for the projector), so a reconnect never reshuffles the cards."""
        ids = [o["id"] for o in items]
        out = list(items)
        rng = random.Random(f"{q['id']}:{who or 0}")
        for _ in range(6):
            rng.shuffle(out)
            if len(out) < 2 or [o["id"] for o in out] != ids:
                break
        return out

    def _public_q(self, role: str = "host", player: "Player | None" = None) -> dict | None:
        q = self.q
        if not q:
            return None
        memo = self._memo
        base = memo.get(("pq", role)) if memo is not None else None
        if base is None:
            base = self._public_q_base(q, role)
            if memo is not None:
                memo[("pq", role)] = base
        view = dict(base)                       # shallow copy: callers add per-player keys to it
        if player is not None:                  # each player sees ordering / matching items in their own stable shuffle
            if q["type"] == "order":
                view["options"] = [self._item(o) for o in self._shuffled(q["options"], q, player.id)]
            elif q["type"] == "match":
                view["targets"] = [self._item(o) for o in self._shuffled(q["targets"], q, player.id)]
            elif q["type"] == "jigsaw":
                view["jigsaw"] = {**view["jigsaw"], "hint_used": player.id in self.hints.get(q["id"], ())}
        return view

    def _public_q_base(self, q: dict, role: str) -> dict:
        t = q["type"]
        pos, count = self._numbering(self.index)
        who = None
        if t == "order":
            options = self._shuffled(q["options"], q, who)
        else:
            options = q["options"]
        view = {
            "id": q["id"], "run": self.runs.get(q["id"], 0), "index": self.index, "total": len(self.questions), "n": pos, "of": count, "type": t,
            "text": q["text"], "image": q["image"], "image_effect": q.get("image_effect", "none"), "category": q.get("category", ""),
            "options": [self._item(o) for o in options], "scored": t in SCORED,
            "time_limit": q["time_limit"], "points": q["points"], "speed": self._speed_weight(q),
            "winners": q.get("winners", 0), "only_players": self._only_players(q),
            "spots_left": max(0, q["winners"] - self._correct_count(q)) if q.get("winners") else None,
            "media": self._media_view(q, role), "awaiting_media": self.media_wait and self.phase == "question",
            "media_nonce": self.media_nonce,
            "started_at": self.started_ms, "ends_at": self.ends_ms,
            "remaining_ms": self.paused_remaining,
        }
        if t == "match":
            view["targets"] = [self._item(o) for o in self._shuffled(q["targets"], q, who)]
        elif t == "number":
            spec = q["number"]
            view["number"] = {"min": spec.get("min"), "max": spec.get("max"), "unit": spec.get("unit", ""), "closest": bool(spec.get("closest"))}
        elif t == "pin":
            view["pin"] = {"ar": q["pin"].get("ar", 1.5)}                 # the target stays secret until the reveal
        elif t == "jigsaw":
            j = q["jigsaw"]
            view["jigsaw"] = {"rows": j["rows"], "cols": j["cols"], "hint": int(j.get("hint", 0))}      # the cost, 0 = no hint button
        elif t == "slide":
            view["board"] = bool(q.get("board"))
        view["places"] = list(q.get("place_weights") or [])               # marks by finishing place, shown as "1st 100% · 2nd 70%"
        return view

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
        if q["type"] not in grading.CHOICE_TYPES:
            return {}
        dist = {o["id"]: 0 for o in q["options"]}
        for a in self.answers.get(q["id"], {}).values():
            for c in a["choice"]:
                if c in dist:
                    dist[c] += 1
        return dist

    def _reveal_detail(self, q: dict, ans: dict) -> dict:
        """What each question type shows once it is over (nothing in here is secret any more)."""
        t = q["type"]
        if t == "text":
            groups: dict[str, dict] = {}
            for a in ans.values():
                raw = str(a["choice"][0])
                g = groups.setdefault(grading.normalize_text(raw), {"count": 0, "forms": {}})
                g["count"] += 1
                g["forms"][raw] = g["forms"].get(raw, 0) + 1
            top = sorted(groups.items(), key=lambda kv: -kv[1]["count"])[:8]
            return {"answers": q["answers"], "top": [
                {"key": k, "text": max(g["forms"].items(), key=lambda f: f[1])[0], "count": g["count"],
                 "correct": grading.text_matches(q, max(g["forms"].items(), key=lambda f: f[1])[0])} for k, g in top]}
        if t == "number":
            spec = q["number"]
            guesses = []
            for pid, a in ans.items():
                v = a["choice"][0]
                guesses.append({"nickname": self.players[pid].nickname, "avatar": self.players[pid].avatar, "value": v,
                                "err": abs(v - spec["answer"]), "points": a["points"], "ok": a["correct"]})
            guesses.sort(key=lambda g: g["err"])
            return {"answer": spec["answer"], "tolerance": spec.get("tolerance", 0), "unit": spec.get("unit", ""),
                    "min": spec.get("min"), "max": spec.get("max"), "guesses": guesses[:8], "count": len(guesses)}
        if t == "order":
            return {"order": [self._item(o) for o in q["options"]]}
        if t == "match":
            return {"pairs": [{"left": self._item(o), "right": self._item(r)} for o, r in zip(q["options"], q["targets"])]}
        if t == "pin":
            return {"target": q["pin"], "points": [[a["choice"][0], a["choice"][1], bool(a["correct"])] for a in list(ans.values())[:300]]}
        if t == "jigsaw":
            done = self._finishers(q)
            return {"rows": q["jigsaw"]["rows"], "cols": q["jigsaw"]["cols"], "solved": sum(1 for a in ans.values() if a["correct"]),
                    "attempted": len(ans), "finishers": done[:10]}
        return {}

    def _finishers(self, q: dict) -> list[dict]:
        """Everyone who solved the puzzle, in the order their answers arrived, with what each earned."""
        good = sorted(((a.get("at") or a["elapsed_ms"], pid, a) for pid, a in self.answers.get(q["id"], {}).items() if a["correct"]), key=lambda x: x[0])
        return [{"place": i, "nickname": self.players[pid].nickname, "avatar": self.players[pid].avatar, "ms": a["elapsed_ms"], "points": a["points"]}
                for i, (_, pid, a) in enumerate(good, 1) if pid in self.players]

    def _reveal(self) -> dict | None:
        memo = self._memo
        if memo is not None and "reveal" in memo:
            return memo["reveal"]
        r = self._compute_reveal()
        if memo is not None:
            memo["reveal"] = r
        return r

    def _compute_reveal(self) -> dict | None:
        q = self.q
        if not q or self.phase != "reveal":
            return None
        t = q["type"]
        ans = self.answers.get(q["id"], {})
        dist = self._dist(q)
        fastest = None
        if t in SCORED:
            good = [(a["elapsed_ms"], pid) for pid, a in ans.items() if a["correct"]]
            if good:
                ms, pid = min(good)
                fastest = {"nickname": self.players[pid].nickname, "avatar": self.players[pid].avatar, "ms": ms}
        winner = next((self.players[pid].nickname for pid, a in ans.items() if a["correct"]), None) if t == "buzzer" else None
        limit = q.get("winners", 0) if t in SCORED else 0
        claimed = []
        if limit:   # who got the scarce points, in the order their answers arrived, and when
            good = sorted(((a.get("at") or a["elapsed_ms"], pid, a) for pid, a in ans.items() if a["correct"]), key=lambda x: x[0])
            for rank, (_, pid, a) in enumerate(good[:limit], 1):
                claimed.append({"rank": rank, "nickname": self.players[pid].nickname, "avatar": self.players[pid].avatar,
                                "ms": a["elapsed_ms"], "at": a.get("at") or None, "points": a["points"]})
        return {
            "correct": q["correct"] if t in ("mcq", "multi") else [], "distribution": dist, "answered": len(ans),
            "correct_count": sum(1 for a in ans.values() if a["correct"]),
            "explanation": q["explanation"], "explain_image": q.get("explain_image", ""), "buzz_winner": winner, "fastest": fastest,
            "winners": limit, "claimed": claimed, "closed_by_limit": bool(limit) and len(claimed) >= limit,
            "detail": self._reveal_detail(q, ans),
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
            "times": [self.buzz_ms.get(pid) for pid in self.buzz_order if pid in self.players],     # ms after the question, same order
        }

    LOOK_REFRESH_S = 3.0

    def _follow_quiz_look(self) -> bool:
        """A game is a copy of its quiz, made when the lobby opens. While nobody has started yet, a change to the quiz's title, lobby text,
        background or music (made after the lobby was opened) is picked up, so the host does not have to open a new lobby. Once the quiz
        starts, the copy is frozen."""
        if self.phase != "lobby" or not self.quiz_id:
            return False
        t = time.monotonic()
        if t - self._look_at < self.LOOK_REFRESH_S:
            return False
        self._look_at = t
        look = self.db.quiz_look(self.quiz_id)
        if not look:
            return False
        keys = ("screen_bg", "screen_bg_wash", "lobby_music")
        if look["title"] == self.title and look["description"] == self.description and all(self.settings.get(k) == look[k] for k in keys):
            return False
        self.title, self.description = look["title"], look["description"]
        self.settings.update({k: look[k] for k in keys})
        self.db.set_game_look(self.id, self.title, self.description, self.settings)
        return True

    def _common(self, ranks: dict) -> dict:
        self._follow_quiz_look()
        return {
            "phase": self.phase, "title": self.title, "description": self.description, "bg": self.settings.get("screen_bg", ""), "bg_wash": self.settings.get("screen_bg_wash", 40), "music": self.settings.get("lobby_music", ""),
            "code": self.code, "server_now": now_ms(), "players": len(ranks["order"]),
            "skin": self.settings.get("skin", "classic"), "jokers": bool(self.settings.get("jokers")),
            "pack": self.settings.get("sound_pack", "gameshow"), "reactions": bool(self.settings.get("reactions")),
            "suspense": {"until": self.suspense_until} if self.suspense else None,     # "final answers locked" until then
        }

    def _joker_left(self, p: Player) -> bool:
        return bool(self.settings.get("jokers")) and p.joker_qid is None

    def view_player(self, p: Player, ranks: dict) -> dict:
        v = self._common(ranks)
        # the waiting-screen music plays on phones only while players are waiting, and only if the host left that on
        v["music"] = v["music"] if (self.phase == "lobby" and self.settings.get("phone_music", True)) else ""
        rank = ranks["rank"].get(p.id)
        v["me"] = {"id": p.id, "nickname": p.nickname, "avatar": p.avatar, "score": p.score, "rank": rank,
                   "streak": p.streak, "joker": self._joker_left(p)}
        q = self.q
        if q:
            v["q"] = self._public_q("player", p)
            v["q"]["can_play"] = (not q.get("only")) or p.id in q["only"]
            mine = self.answers.get(q["id"], {}).get(p.id)
            if self.phase == "question":
                v["answered"] = mine is not None and q["type"] != "buzzer"
                v["locked_out"] = p.id in self.lockouts
                v["joker_on"] = bool(mine and mine.get("joker"))
                b = self._buzz_view()
                if b:
                    b["holder_is_me"] = self.holder == p.id
                    v["buzz"] = b
            elif self.phase == "slide":
                if q.get("board"):
                    v["leaderboard"] = self._board(ranks, 5)
            else:
                v["reveal"] = self._reveal()
                v["me"]["delta"] = self._rank_before.get(p.id, rank) - rank if rank else 0
                v["leaderboard"] = self._board(ranks, 5, deltas=True)
                yours = []
                if mine and mine["choice"] != ["buzz"]:
                    try:
                        yours = grading.describe(q, mine["choice"])
                    except Exception:   # a question edited under a running game: never break the reveal for it
                        yours = []
                v["result"] = {
                    "answered": mine is not None,
                    "correct": bool(mine and mine["correct"]),
                    "partial": bool(mine and not mine["correct"] and mine["points"] > 0),
                    "points": mine["points"] if mine else 0,
                    "max_points": q["points"],
                    "elapsed_ms": mine["elapsed_ms"] if mine else None,
                    "choice": mine["choice"] if mine and q["type"] in grading.CHOICE_TYPES else [],
                    "yours": yours, "joker": bool(mine and mine.get("joker")),
                    "pin": mine["choice"] if mine and q["type"] == "pin" else None,
                    "hint_cost": mine.get("hint", 0) if mine else 0,
                }
        if self.phase == "finished":
            v["leaderboard"] = self._board(ranks, 10)
            v["stats"] = self._stats(p)
        else:
            v["preload"] = self._preload()
        return v

    def _stats(self, p: Player) -> dict:
        asked = [q for q in self.questions[: self.index + 1] if q["type"] in SCORED or q["type"] == "buzzer"]
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
                if q["type"] == "poll":
                    v["live_dist"] = self._dist(q)          # nothing secret about a poll: let the room watch it fill up
                elif q["type"] == "jigsaw":
                    v["finishers"] = self._finishers(q)[:8]  # the puzzle is no secret: the room watches the race for the places
            elif self.phase == "reveal":
                v["reveal"] = self._reveal()
                v["crowd"] = self._crowd(q)
        if self.phase != "finished":
            v["preload"] = self._preload()
        return v

    def _crowd(self, q: dict) -> dict:
        """Who picked what, for the avatars-fly-into-the-bars reveal on the projector. Capped (a sample, not everyone)."""
        if q["type"] not in grading.CHOICE_TYPES:
            return {}
        by: dict[str, list[str]] = {o["id"]: [] for o in q["options"]}
        for pid, a in self.answers.get(q["id"], {}).items():
            p = self.players.get(pid)
            if p is None or p.kicked:
                continue
            for c in a["choice"]:
                if c in by:
                    by[c].append(p.avatar)
        total = sum(len(v) for v in by.values())
        if total > CROWD_MAX:
            share = CROWD_MAX / total
            by = {o: v[: max(1, int(len(v) * share))] if v else v for o, v in by.items()}
        return by

    def view_host(self, ranks: dict) -> dict:
        v = self.view_screen(ranks)
        v["game_id"] = self.id
        v["settings"] = self.settings
        v["index"] = self.index
        v["outline"] = [{"id": q["id"], "type": q["type"], "text": strip_tags(q["text"])[:90], "tie": bool(q.get("only")),
                         "category": q.get("category", "")} for q in self.questions]
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
                v["q_secret"] = self._reveal_detail(q, self.answers.get(q["id"], {})) if q["type"] in ("text", "number", "order", "match", "pin") else None
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
        if self.phase not in ("lobby", "reveal", "slide"):
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
        if q["type"] == "slide":                       # a title card / standings break: no timer, nothing to answer
            self.phase = "slide"
            self.started_ms = now_ms()
            self.ends_ms = self.started_ms
            self.holder, self.paused_remaining, self.media_wait = None, None, False
            self._save_state()
            await self.push()
            return
        self.phase = "question"
        self.started_ms = now_ms()
        self.ends_ms = self.started_ms + q["time_limit"] * 1000
        self.holder = None
        self.lockouts = set()
        self.buzz_order = []
        self.buzz_ms = {}
        self.suspense = False
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

    async def skip(self):
        """Move straight on to the next question without revealing this one. Answers already given keep their marks."""
        if self.phase != "question" or self.index + 1 >= len(self.questions):
            return
        for task in (self._timer, self._window, self._media_guard, self._suspense_task):
            self._cancel(task)
        self._cancel_bots()
        self.media_wait = self.suspense = False
        self._buzz_pending = []
        self.index += 1
        await self._begin_question()

    async def rerun(self, seconds=None):
        """Ask the question that just closed again, with a new time limit, when nobody got it right (or nobody answered).
        The first round is wiped: its answers are deleted and any part marks, spent double-ups and streaks are taken back."""
        q = self.q
        if self.phase != "reveal" or not q or q["type"] in ("slide", "poll"):
            return
        old = self.answers.get(q["id"], {})
        if any(a["correct"] for a in old.values()):
            return
        if seconds not in (None, ""):
            try:
                q["time_limit"] = max(5, min(300, int(seconds)))
            except (TypeError, ValueError):
                return
        for pid, a in old.items():
            p = self.players.get(pid)
            if p is None:
                continue
            if a["points"]:
                p.score = max(0, p.score - a["points"])
                self.db.set_player_score(p.id, p.score)
            if a.get("joker") and p.joker_qid == q["id"]:
                p.joker_qid = None                         # the double-up was not used up by a round that did not count
        self.db.delete_answers(self.id, q["id"])
        self.answers.pop(q["id"], None)
        self.hints.pop(q["id"], None)
        self.runs[q["id"]] = self.runs.get(q["id"], 0) + 1
        self._rank_ver += 1
        self.phase = "question"                            # so streaks are worked out as they stood before this question
        self._recompute_streaks()
        self._save_state(with_questions=True)
        await self._begin_question()

    async def replay_media(self):
        if self.phase == "question" and self.q and self.q.get("media"):
            self.media_nonce += 1
            await self.push()

    async def end_question(self):
        """Close the question. With a reveal pause the room first sees 'final answers locked' (answers are refused, the
        projector builds suspense), then the answer; pressing Reveal again during the pause skips the wait."""
        if self.phase != "question":
            return
        if self.suspense:
            self._cancel(self._suspense_task)
            self.suspense = False
            await self._reveal_now()
            return
        pause = float(self.settings.get("reveal_pause") or 0)
        if pause > 0 and self.q and self.q["type"] != "slide":
            self._cancel(self._timer)
            self._cancel(self._window)
            self._cancel(self._media_guard)
            self._cancel_bots()
            self.media_wait = False
            self._buzz_pending = []
            self.suspense = True
            self.suspense_until = now_ms() + pause * 1000
            self.ends_ms = min(self.ends_ms, now_ms())          # clocks everywhere show 0 while the room holds its breath
            self._suspense_task = asyncio.create_task(self._suspense_run(pause))
            await self.push()
            return
        await self._reveal_now()

    async def _suspense_run(self, seconds: float):
        await asyncio.sleep(seconds)
        self.suspense = False
        await self._reveal_now()

    async def _reveal_now(self):
        if self.phase != "question":
            return
        self._cancel(self._timer)
        self._cancel(self._window)
        self._cancel(self._media_guard)
        self._cancel_bots()
        self.media_wait = False
        q = self.q
        if q and q["type"] in SCORED:
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
        self._cancel(self._suspense_task)
        self._cancel_bots()
        self.media_wait = False
        self.suspense = False
        self.phase = "finished"
        self.holder = None
        self._save_state()
        self.db.finish_game(self.id)
        await self.push()

    async def add_time(self, seconds):
        if self.phase != "question" or self.media_wait or self.suspense:
            return
        extra = max(-30, min(120, int(seconds))) * 1000
        if self.holder is not None:
            self.paused_remaining = max(1000.0, (self.paused_remaining or 0) + extra)
        else:
            self.ends_ms = max(now_ms() + 1000, self.ends_ms + extra)
            self._start_timer()
        await self.push()

    # ---- answering ----------------------------------------------------------------------------
    async def submit(self, p: Player, choice, joker: bool = False):
        q = self.q
        if self.phase != "question" or not q or q["type"] in ("buzzer", "slide") or p.kicked or self.media_wait or self.suspense:
            return
        if q.get("only") and p.id not in q["only"]:
            return                                     # a tie-breaker: only the tied players may answer
        if p.id in self.answers.get(q["id"], {}):
            return
        value = grading.clean_answer(q, choice)
        if value is None:
            return
        now = now_ms()
        if now > self.ends_ms + ANSWER_GRACE_MS:
            return
        # the player saw the question about rtt/2 after we sent it
        elapsed = max(0.0, now - self.started_ms - min(p.rtt_ms, 1000) / 2)
        elapsed = min(elapsed, q["time_limit"] * 1000)
        hint_cost = 0
        if q["type"] == "poll":
            correct, pts, use_joker = False, 0, False
        else:
            correct, fraction = grading.grade(q, value)
            place = self._correct_count(q) + 1 if correct else None        # 1st, 2nd, 3rd... correct answer (this one is not stored yet)
            pts = round(self._points(q, elapsed, place) * fraction) if fraction > 0 else 0
            if pts and p.id in self.hints.get(q["id"], ()):
                hint_cost = min(pts, q["jigsaw"].get("hint", 0))          # the hint's price comes off what was earned, never below zero
                pts -= hint_cost
            use_joker = bool(joker) and self._joker_left(p)
            if use_joker:                              # "double up": spent whether or not it pays off
                pts *= 2
        if not self.db.add_answer(self.id, p.id, q["id"], self.index, value, correct, pts, int(elapsed), now / 1000, use_joker):
            return
        self.answers.setdefault(q["id"], {})[p.id] = {
            "choice": value, "correct": correct, "points": pts, "elapsed_ms": int(elapsed), "at": now, "joker": use_joker, "hint": hint_cost}
        if use_joker:
            p.joker_qid = q["id"]
        if q["type"] != "poll":
            p.streak = p.streak + 1 if correct else 0
        if pts:
            p.score += pts
            self._rank_ver += 1
            self.db.set_player_score(p.id, p.score)

        limit = q.get("winners", 0)
        if correct and limit:
            if self._correct_count(q) >= limit:       # the last scoring place was just taken: close the question
                await self.end_question()
                return
            await self.push()                         # a place is gone but others remain: everyone sees "N left"
            return

        answered = self.answers[q["id"]]
        if self.settings["auto_reveal"]:
            waiting_for = self._active_ids & set(q["only"]) if q.get("only") else self._active_ids
            if waiting_for and waiting_for <= answered.keys():       # every connected (eligible) player has answered
                await self.end_question()
                return
        await self.push_player(p)
        self.schedule_host_screen_push()

    async def accept_answer(self, key, accept: bool = True):
        """The host re-judges a typed answer after the reveal: everyone who typed it (however they spelled it) is
        paid, or un-paid, and the game's accepted list learns it for the next question of this kind."""
        q = self.q
        if self.phase != "reveal" or not q or q["type"] != "text" or not isinstance(key, str):
            return
        hit = [(pid, a) for pid, a in self.answers.get(q["id"], {}).items() if grading.normalize_text(a["choice"][0]) == key]
        if not hit:
            return
        sample = hit[0][1]["choice"][0]
        if accept:
            if sample not in q["answers"]:
                q["answers"] = [*q["answers"], sample][:20]
            q["rejected"] = [r for r in q.get("rejected") or [] if grading.normalize_text(r) != key]
        else:
            q["rejected"] = [*(q.get("rejected") or []), sample][:20]
        for pid, a in hit:
            if a["correct"] == accept:
                continue
            pts = self._points(q, a["elapsed_ms"]) if accept else 0
            if accept and a.get("joker"):
                pts *= 2
            p = self.players[pid]
            p.score = max(0, p.score - a["points"] + pts)
            self._rank_ver += 1
            a["correct"], a["points"] = accept, pts
            self.db.update_answer(self.id, pid, q["id"], accept, pts)
            self.db.set_player_score(pid, p.score)
        self._recompute_streaks()
        self._save_state(with_questions=True)
        await self.push()

    async def buzz(self, p: Player):
        q = self.q
        if self.phase != "question" or not q or q["type"] != "buzzer" or p.kicked or self.suspense:
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
        self.buzz_ms = {pid: max(0, int(ts - self.started_ms)) for ts, pid in pending}     # how long after the question each of them reacted
        self.holder = pending[0][1]
        self.paused_remaining = max(0.0, self.ends_ms - now_ms())
        self._cancel(self._timer)
        await self.push()

    async def resolve_buzz(self, correct: bool):
        q = self.q
        if self.phase != "question" or not q or self.holder is None or self.suspense:
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
            self._rank_ver += 1
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
        self.index = min(self.index, len(self.questions) - 1)
        self.phase = "lobby" if self.index < 0 else "slide" if self.questions[self.index]["type"] == "slide" else "reveal"
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
        self.db.update_quiz(self.quiz_id, {"title": quiz["title"], "description": quiz["description"], "cover": quiz.get("cover", ""), "screen_bg": quiz.get("screen_bg", ""), "screen_bg_wash": quiz.get("screen_bg_wash", 40), "lobby_music": quiz.get("lobby_music", ""), "questions": questions})
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
        self._rank_ver += 1
        self._active_ids.discard(p.id)
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
        self._rank_ver += 1
        self.db.set_player_score(p.id, p.score)
        await self.push()

    async def set_setting(self, key, value):
        ok, value = clean_setting(key, value)
        if not ok:
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
        await self.submit(bot, self._bot_answer(q))

    @staticmethod
    def _bot_answer(q: dict):
        """A believable answer: right about half the time, plausibly wrong otherwise."""
        t, good = q["type"], random.random() < 0.55
        ids = [o["id"] for o in q["options"]]
        if t in ("mcq", "tf"):
            return list(q["correct"]) if good else [random.choice(ids)]
        if t == "multi":
            return list(q["correct"]) if good else random.sample(ids, random.randint(1, len(ids)))
        if t == "poll":
            return [random.choice(ids)]
        if t == "text":
            return [random.choice(q["answers"])] if good else [random.choice(["no idea", "Paris", "42", "banana"])]
        if t == "number":
            spec = q["number"]
            spread = spec.get("spread") or max(abs(spec["answer"]) * 0.1, 1)
            return [spec["answer"] if good else spec["answer"] + random.uniform(-2, 2) * spread]
        if t == "order":
            if good:
                return list(q["correct"])
            mixed = list(q["correct"])
            random.shuffle(mixed)
            return mixed
        if t == "match":
            rights = list(q["correct"])
            if not good:
                random.shuffle(rights)
            return [[o["id"], r] for o, r in zip(q["options"], rights)]
        if t == "pin":
            pin = q["pin"]
            jitter = pin["r"] * (0.6 if good else 3.5)
            return [min(100, max(0, pin["x"] + random.uniform(-jitter, jitter))), min(100, max(0, pin["y"] + random.uniform(-jitter, jitter)))]
        if t == "jigsaw":
            n = q["jigsaw"]["rows"] * q["jigsaw"]["cols"]
            mine = list(range(n))
            if not good:                                    # one or two swapped pieces: close, but not solved
                for _ in range(random.randint(1, 2)):
                    a, b = random.sample(range(n), 2)
                    mine[a], mine[b] = mine[b], mine[a]
            return mine
        return None

    # ---- message dispatch --------------------------------------------------------------------------
    async def on_host(self, msg: dict):
        t = msg.get("t")
        if t == "next":
            await self.next()
        elif t == "end_question":
            await self.end_question()
        elif t == "skip":
            await self.skip()
        elif t == "rerun":
            await self.rerun(msg.get("seconds"))
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
        elif t == "accept_answer":
            await self.accept_answer(msg.get("key"), bool(msg.get("accept", True)))
        elif t == "set":
            await self.set_setting(msg.get("key"), msg.get("value"))
        elif t == "cue":
            await self.cue(msg.get("name"))

    # ---- the show: soundboard cues and audience reactions (cues go to the projector; reactions to the projector and the host console) -------------
    async def cue(self, name):
        """A host soundboard button. Anything off the list is ignored; a burst of presses is thinned out."""
        t = time.monotonic()
        if name not in CUES or not self.screens or t - self._cue_last < 0.12:
            return
        self._cue_last = t
        await asyncio.gather(*[_send(ws, {"t": "cue", "name": name}) for ws in list(self.screens)])

    def react(self, p: Player, emoji):
        """A phone throws an emoji at the big screen and the host console. One per player per second, batched so 1,000
        phones cost one message. Each carries the thrower's name and avatar, which float up with the emoji."""
        if not self.settings.get("reactions") or emoji not in REACTIONS or p.kicked or not (self.screens or self.hosts):
            return
        t = time.monotonic()
        if t - self._react_last.get(p.id, -10.0) < REACT_GAP_S:
            return
        self._react_last[p.id] = t
        if len(self._react_buf) < REACT_BATCH_MAX:          # a storm of reactions is thinned out, never queued up
            self._react_buf.append({"e": emoji, "n": p.nickname, "a": p.avatar})
        if self._react_task is None or self._react_task.done():
            self._react_task = asyncio.create_task(self._flush_reactions())

    async def _flush_reactions(self):
        await asyncio.sleep(0.4)
        buf, self._react_buf = self._react_buf, []
        if buf:
            counts: dict[str, int] = {}
            for it in buf:
                counts[it["e"]] = counts.get(it["e"], 0) + 1
            msg = {"t": "react", "items": buf, "e": counts}          # "e" (emoji counts) is what projector pages loaded before names existed read
            await asyncio.gather(*[_send(ws, msg) for ws in [*self.screens, *self.hosts]])

    async def on_screen(self, msg: dict, controller: bool):
        """Only a projector opened from the host console (it carries the game's screen key) may drive the game."""
        if controller and msg.get("t") == "media_ended":
            await self.start_after_media()

    async def use_hint(self, p: Player):
        """A jigsaw hint: this player may now see the finished picture, and pays for it out of the marks they earn."""
        q = self.q
        if (self.phase != "question" or not q or q["type"] != "jigsaw" or p.kicked or self.media_wait or self.suspense
                or not q["jigsaw"].get("hint")):
            return
        if (q.get("only") and p.id not in q["only"]) or p.id in self.answers.get(q["id"], {}) or p.id in self.hints.get(q["id"], ()):
            return
        self.hints.setdefault(q["id"], set()).add(p.id)
        await self.push_player(p)

    async def on_player(self, p: Player, msg: dict):
        t = msg.get("t")
        if t == "answer":
            await self.submit(p, msg.get("choice"), bool(msg.get("joker")))
        elif t == "hint":
            await self.use_hint(p)
        elif t == "buzz":
            await self.buzz(p)
        elif t == "react":
            self.react(p, msg.get("e"))
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

    async def refresh_lobbies(self) -> int:
        """Waiting lobbies that someone is looking at follow changes made to their quiz, and tell the screens and phones at once."""
        changed = 0
        for g in list(self.games.values()):
            if g.phase == "lobby" and g.quiz_id and (g.hosts or g.screens or any(p.sockets for p in g.players.values())):
                if g._follow_quiz_look():
                    changed += 1
                    await g.push()
        return changed

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
            ok, clean = clean_setting(k, v)
            if ok:
                merged[k] = clean
        merged["screen_bg"] = quiz.get("screen_bg", "")        # the big screen's background picture and waiting-screen music are fixed when the game is made
        merged["lobby_music"] = quiz.get("lobby_music", "")
        merged["screen_bg_wash"] = quiz.get("screen_bg_wash", 40)
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
    polls = {q["id"] for q in game["questions"] if q["type"] == "poll"}
    for a in answers:
        if a["qid"] not in polls:
            by_player.setdefault(a["player_id"], []).append(a)
        by_q.setdefault(a["qid"], []).append(a)
    players.sort(key=lambda p: (-p["score"], p["joined_at"], p["id"]))
    board = [{
        "id": p["id"], "rank": i + 1, "nickname": p["nickname"], "avatar": p["avatar"], "email": p["email"], "score": p["score"],
        "correct": sum(1 for a in by_player.get(p["id"], []) if a["correct"]),
        "answered": len(by_player.get(p["id"], [])), "is_bot": bool(p["is_bot"]),
    } for i, p in enumerate(players)]
    analytics, number = [], 0
    for i, q in enumerate(game["questions"]):
        if q["type"] == "slide":
            continue                                           # a title card is not a question
        number += 1
        rows = by_q.get(q["id"], [])
        correct = [a for a in rows if a["correct"]]
        graded = q["type"] != "poll"
        analytics.append({
            "index": i, "n": number, "text": strip_tags(q["text"])[:120], "type": q["type"], "category": q.get("category", ""),
            "answered": len(rows), "correct": len(correct),
            "pct_correct": round(100 * len(correct) / len(rows)) if rows and graded else None,
            "avg_seconds": round(sum(a["elapsed_ms"] for a in correct) / len(correct) / 1000, 1) if correct else None,
        })
    return {
        "game": {"id": game["id"], "code": game["code"], "title": game["title"], "quiz_id": game["quiz_id"], "owner_id": game.get("owner_id"),
                 "status": game["status"], "created_at": game["created_at"]},
        "leaderboard": board, "analytics": analytics,
    }


def player_detail(db: Database, game_id: int, player_id: int) -> dict | None:
    """One player's whole game: every question, what they answered, the right answer, how long they took, when they
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

    rows, running, number = [], 0, 0
    for i, q in enumerate(game["questions"]):
        if q["type"] == "slide":
            continue                                             # a title card is not a question
        number += 1
        if i > last_index and q["id"] not in by_q:
            continue                                             # never asked (the game ended early)
        t = q["type"]
        a = mine.get(q["id"])
        only = q.get("only")
        earned = a["points"] if a else 0
        running += earned
        graded = t != "poll"
        chosen = set(a["choice"]) if a and t in grading.CHOICE_TYPES else set()
        options = [{"index": k, "text": o["text"], "image": o.get("image", ""), "chosen": o["id"] in chosen, "correct": o["id"] in q["correct"]}
                   for k, o in enumerate(q["options"])] if t in grading.CHOICE_TYPES else []
        yours, expected = [], []
        if a and a["choice"] != ["buzz"] and t not in grading.CHOICE_TYPES:
            try:
                yours = grading.describe(q, a["choice"])
            except Exception:
                yours = []
        if t in SCORED and t not in ("mcq", "multi"):
            expected = grading.expected(q)
        claim_rank = None
        if q.get("winners") and t in SCORED and a and a["correct"]:
            good = sorted((x for x in by_q.get(q["id"], []) if x["correct"]), key=lambda x: (x["submitted_at"] or x["elapsed_ms"]))
            claim_rank = 1 + next(k for k, x in enumerate(good) if x["player_id"] == player_id)
        rows.append({
            "index": i, "n": number, "text": q["text"], "type": t, "category": q.get("category", ""), "image": q.get("image", ""),
            "options": options, "yours": yours, "expected": expected, "graded": graded, "time_limit": q["time_limit"],
            "max_points": q["points"], "speed_weight": q.get("speed_weight", 0), "winners": q.get("winners", 0),
            "tie_breaker": bool(only), "not_in_round": bool(only) and player_id not in only,
            "answered": a is not None, "correct": bool(a and a["correct"]), "buzzed": bool(a and a["choice"] == ["buzz"]),
            "partial": bool(a and not a["correct"] and a["points"] > 0), "joker": bool(a and a.get("joker")),
            "earned": earned, "running": running, "elapsed_ms": a["elapsed_ms"] if a else None,
            "submitted_at": (a["submitted_at"] * 1000) if a and a["submitted_at"] else None, "claim_rank": claim_rank,
        })
    graded_rows = [r for r in rows if r["graded"]]
    answered = [r for r in graded_rows if r["answered"]]
    right = [r for r in graded_rows if r["correct"]]
    timed = [r["elapsed_ms"] for r in right if r["type"] != "buzzer" and r["elapsed_ms"] is not None]
    asked = [r for r in graded_rows if not r["not_in_round"]]
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
