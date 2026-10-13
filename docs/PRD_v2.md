# Quickz — Product Requirements Document (v2)

**Status:** Draft for MVP build · supersedes `Quiz_product_specification.md` (PyQuiz Network v1.0.0)
**One-liner:** A quiz you can host from one laptop in under a minute — players scan a QR code and play on their own phones. No accounts, no app, no internet required.

---

## 1. Positioning

| | Cloud quiz tools | Quickz |
|---|---|---|
| Player sign-up | Account or app often needed | **None** — code/QR + nickname |
| Hosting | Vendor cloud | **Your laptop** (LAN) — or optional public tunnel |
| Internet needed | Yes | **No** (all assets bundled, no CDN) |
| Player cap / paywall | Typical on free tiers | **None** |
| Data | Vendor-held | **Local SQLite file**, auto-purge option |
| Game-show feel | Limited | **Buzzer, projector screen, teams** |

**Audiences (both equal in v1):**
- *Social* — pub quiz, meetup, classroom, party: fun scoring, nicknames, no friction.
- *Corporate* — town hall, training, team-building: optional email capture, result export, branding.

Both are served by one product: accounts are never required; "ask for email" is a per-game toggle.

## 2. Three surfaces (all web)

| Surface | URL | Who / where | Auth |
|---|---|---|---|
| **Host console** | `/host` | Organiser's laptop or phone | Username + password (Super Admin or Quiz Host) |
| **Projector screen** | `/screen/<CODE>` | TV / projector browser, fullscreen | None (read-only, never receives answers) |
| **Player** | `/j/<CODE>` | Anyone's phone or laptop, via QR / short URL | Nickname → device token in `localStorage` |

The projector shows the QR code (encoding the LAN URL) during the lobby; players scan it and land on the join form with the code pre-filled.

## 3. Fixes to v1 spec

| v1 issue | v2 resolution |
|---|---|
| Registration with employee ID/company/email/password | Removed. Join = code + nickname (+ optional email). |
| First registered user becomes admin | Random 6-digit host PIN generated at startup (or `QUICKZ_PIN`). |
| `Response` not tied to a game run → replays corrupt results | New `games` table; players/answers keyed by `game_id`. |
| Global in-memory `GAME_STATE` | One `Game` object per room, **snapshotted to SQLite**; open games resume after restart. |
| Correct answer = exact string match | Options have stable IDs; grading by ID. |
| Raw WYSIWYG HTML sent to players (stored XSS) | Plain text + safe Markdown-lite (escaped first); images only from `/uploads/`. |
| Flat 10 points | Speed-weighted scoring, configurable. |
| Buzzer fairness = server receive order | RTT-compensated, 120 ms tie window. |
| Eventlet / Summernote / jQuery / CDN | FastAPI + native WebSockets; vanilla JS; zero external requests. |

## 4. Functional requirements

### 4.1 Host console
- **H1** Accounts: first run creates the Super Admin via a one-time setup token; the Super Admin adds Quiz Hosts (temporary password, forced change at first sign-in). Quiz Hosts see only their own quizzes, games and results; Super Admins see all.
- **H2** Quiz library: create, edit, duplicate-by-export/import, delete.
- **H3** Quiz editor: title, description, ordered questions. Per question: type, text (Markdown-lite), optional image upload, options (2–6) with correct marker(s), time limit (5–300 s), points, optional explanation shown on reveal.
- **H4** Import from pasted text format (§7) or JSON; export JSON.
- **H5** Start game: pick quiz + settings (`speed_bonus`, `auto_reveal`, `ask_email`). Server allocates a 5-digit room code.
- **H6** Live console: lobby with joined players (kick), Start, Next, End question now, +10 s, Finish, manual score adjust, **Add bots** (rehearsal/load test), **Quick-add question** (inserted after the current one).
- **H7** Buzzer resolution: holder name shown; **Correct** (award points, end question) or **Wrong** (holder locked out, timer resumes for others; if everyone locked out the question ends).
- **H8** After finish: leaderboard, per-question analytics (% correct, avg time), CSV export, game history list.

### 4.2 Player
- **P1** Join with code + nickname (≤ 20 chars, unique per game, control chars stripped); email if enabled.
- **P2** Reconnect transparently with the same token (refresh, locked phone, Wi-Fi drop) — score preserved. Late join allowed.
- **P3** Question types: **single choice** (also True/False), **multi-select** (all-or-nothing), **buzzer**.
- **P4** Single choice: tap = submit & lock. Multi: toggle + Submit. Timer bar synced to the server clock.
- **P5** Buzzer: large button, local synthesized sound (Web Audio), locks for everyone when someone wins, shows buzz order; a wrong buzzer is locked out for that question only.
- **P6** Reveal: correct/incorrect, points earned, rank, explanation. Final: rank and top 10.
- **P7** Option shapes + colours (▲ ◆ ● ■ …) — never colour alone.

### 4.3 Projector screen
- **S1** Lobby: giant code, QR, short URL, live list of joined players.
- **S2** Question: text/image, options (no correct marker), countdown, answers-received counter.
- **S3** Buzzer splash overlay with winner name + sound (sound needs one click: "Enable sound").
- **S4** Reveal: correct option highlighted, answer distribution bars, top-5.
- **S5** Final: podium (top 3) + top 10.

## 5. Game rules

**State machine:** `lobby → question ⇄ reveal → … → finished` (`question → reveal` on timer end, all-answered with `auto_reveal`, or host "End now"; `reveal → question` on Next; any → `finished` on Finish or after the last reveal + Next).

**Scoring** (single/multi, correct only):
`points = q.points × (1 − 0.5 × elapsed/duration)` when `speed_bonus` is on (so the fastest ≈ 100 %, the slowest correct answer = 50 %); otherwise `q.points`. Wrong/no answer = 0. Buzzer: `q.points` flat, awarded by the host. Tie-break order: score ↓, then earliest join.

**Server authority:** the server owns the question clock (`ends_at`), validates every message against the current phase, accepts an answer only once per player per question (small 300 ms grace after expiry for network delay), and **never sends correct answers to players or the projector before reveal**.

**Buzzer fairness:**
1. Clients ping every ~2.5 s; server keeps a smoothed RTT per player.
2. A buzz is stamped `recv_time − rtt/2` (clamped).
3. The first buzz opens a 120 ms window; all buzzes in the window are ranked by stamped time; the lowest wins. The ranked list is the "buzz order" feed.
4. Winner pauses the clock (remaining time stored); *Wrong* resumes with ≥ 3 s left.

## 6. Architecture

- **Server:** Python 3.11+, FastAPI + Uvicorn, native WebSockets, stdlib `sqlite3` (WAL), `segno` for QR (pure Python).
- **Client:** vanilla HTML/CSS/JS, no build step, no CDN, mobile-first; one shared CSS + `common.js`.
- **Sync model:** *state-push.* On every transition the server pushes a complete, role-specific **view** (`state` message). Clients are stateless renderers → reconnect/late-join "just work". Small targeted pushes (e.g. answered count) go to host/screen only; the lobby join storm never fans out to players.
- **Run:** `python -m quickz` → prints LAN URL, host PIN, QR (ASCII) and the one-click host link. Packaging (PyInstaller `.exe`, Docker) follows MVP.

### Data model (SQLite)
```
quizzes(id, title, description, questions JSON, created_at, updated_at)
games(id, code, quiz_id, title, settings JSON, questions JSON, state JSON, status, created_at, finished_at)
players(id, game_id, token UNIQUE, nickname, email, is_bot, score, joined_at, kicked)
answers(id, game_id, player_id, qid, qindex, choice JSON, correct, points, elapsed_ms, UNIQUE(game_id, player_id, qid))
```
A running game uses its **own copy** of the questions (`games.questions`), so editing a quiz never disturbs a live game, and quick-added questions affect only that game.

Question object: `{id, type: mcq|multi|buzzer, text, image, options:[{id,text}], correct:[id], time_limit, points, explanation}`.

## 7. Quiz text import format
```
# Quiz title
Optional description lines

Q: Capital of France?
- Berlin
- Paris*          <- trailing * marks a correct option
- Rome
time: 20
points: 1000
? Paris has been the capital since 987.   <- explanation

Q: Which are primes?       <- more than one * => multi-select
- 2*
- 4
- 7*

Q: Name this film from the quote…
type: buzzer
```

## 8. HTTP API (host-authenticated unless noted)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/setup` · `/login` · `/logout` · `/logout-all` · `/password` | first-run setup, sign in/out, change password |
| GET/POST/PATCH | `/api/users[/{id}]`, `/api/users/{id}/reset-password` | Super Admin only |
| GET/POST | `/api/quizzes` | list / create |
| GET/PUT/DELETE | `/api/quizzes/{id}` | |
| POST | `/api/quizzes/import` | `{text}` or `{quiz}` |
| GET | `/api/quizzes/{id}/export` | JSON download |
| POST | `/api/uploads` | raw image body; magic-byte check; ≤ 5 MB |
| POST | `/api/games` | `{quiz_id, settings}` → `{code}` |
| GET | `/api/games` | history |
| GET | `/api/games/{id}/results[.csv]`, `/analytics` | |
| GET | `/api/game/{code}` | **public**: title, ask_email, phase |
| POST | `/api/join` | **public**: `{code, nickname, email?, token?}` |
| GET | `/qr/{code}.svg` | **public** |

## 9. WebSocket protocol

Endpoint: `/ws/{role}/{code}` — `role ∈ host | screen | player` (player passes `?token=`). Host requires the session cookie and same-origin `Origin`.

**Server → client:** `state` (full role view incl. `server_now`), `pong {c, s}`, `error {msg}`.

**Player → server:** `answer {choice:[ids]}`, `buzz`, `ping {c}`, `rtt {ms}`.

**Host → server:** `next`, `end_question`, `add_time {seconds}`, `resolve_buzz {correct}`, `kick {player_id}`, `adjust_score {player_id, delta}`, `add_bots {n}`, `add_question {question}`, `finish`, `set {key, value}`.

Limits: 4 KB message cap, ~25 msgs/s per socket.

## 10. Security & privacy
- Accounts with scrypt-hashed passwords, hashed session tokens (`HttpOnly`/`SameSite=Lax`), login throttling, CSRF header, owner-scoped data, Origin + ownership check on the host WebSocket.
- All user text rendered via `textContent` or escape-then-Markdown-lite; images restricted to `/uploads/*`; uploads validated by magic bytes, random filenames.
- Correct answers only in host view until reveal; per-player token for identity; kicked players cannot rejoin by token.
- Data stays local. Player emails collected only when the host enables it. Roadmap: auto-purge after N days.
- No external requests from any page (no CDN, fonts, analytics).

## 11. Non-functional
- 150+ concurrent players on a laptop (state-push payloads < 2 KB; bots mode lets the host verify before the event).
- Works on current iOS Safari / Android Chrome / desktop browsers; usable at 320 px width.
- Accessibility: shapes + colour, ≥ 4.5:1 contrast, `prefers-reduced-motion` respected, large tap targets.
- Venue Wi-Fi caveat: "client isolation" blocks phone→laptop traffic. Docs: use the host's phone hotspot, or the `--public` tunnel (roadmap).

## 12. Roadmap

| Phase | Scope |
|---|---|
| **MVP (this build)** | Everything in §4 – §9 |
| **v1** | Teams / table mode; type-in & number-guess questions; rounds & interstitial standings; streaks, jokers, final wager, tie-breaker; remote-control host on phone; branding (logo/colours); i18n; auto-pause on host disconnect; profanity filter |
| **Later** | Audio/map/ordering questions; poll & word cloud; async (self-paced) mode; AI question generation (BYO key / local model); `--public` tunnel; PDF certificates; PyInstaller/Docker/Pi images |

## 13. Open questions
1. Should kicked/banned nicknames be remembered across games?
2. Default `auto_reveal` on or off for classroom use? (MVP: on.)
3. Do corporate users need SSO later, or is optional email enough?


## 14. Additions after the MVP

| Area | Behaviour |
|---|---|
| Rich text | Question and answer text is a sanitised HTML subset (b, i, u, s, sub, sup, code, span[colour], p, ul, ol, li, br) produced by a built-in WYSIWYG editor; sanitised server-side on save and client-side on render. Legacy markdown-lite is upgraded once. |
| Media | Optional audio or video per question; played on the projector, phones, or both; optional "timer starts when the media ends" (server freezes the clock, locks answers, resumes on the keyed projector's `media_ended` or the host's *Start timer now*; 4-minute safety net). Uploads are streamed, type-sniffed, size-capped, served with HTTP Range. |
| Scoring | Per-question speed weight `w` (0-100): `points = base x (1 - w x t/T)`. `winners` = 0 (everyone) / 1 (first correct takes 100 % and the question closes) / N (first N correct score). Game-level *speed bonus* switch overrides weights. Answer submit times are stored. |
| Runtime | Host can add a question next or at the end, restrict it to chosen players (tie-breaker), save it into the quiz, and reopen a finished game. Tie alerts for podium places. |
| Results | `GET /api/games/{id}/players/{pid}` returns a player's full report (questions, picks, correct answers, elapsed time, time of day, points, running total, host adjustments). |
| Timer | Analog countdown clock on phones, projector and host: the hand ticks backwards each second and the clock turns red for the last five seconds. |
| Ops | Optional S3-compatible persistence (`QUICKZ_S3_*`); Docker image; `PORT`/proxy awareness; `tools/loadtest.py`. |
