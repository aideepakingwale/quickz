# Quickz

A quiz you can host from one laptop in under a minute. Players scan a QR code and play on their own phones — no accounts, no app, no internet needed.

* **Host console** (`/host`) – build quizzes, run the game, judge buzzers, export results.
* **Projector screen** (`/screen/<code>`) – lobby with QR code, questions, live leaderboard, podium.
* **Players** (`/j/<code>`) – join with a nickname; MCQ, multi-select and buzzer questions.

Specs: [docs/PRD_v2.md](docs/PRD_v2.md) (supersedes the original `Quiz_product_specification.md`).

## Run it

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Windows  (Linux/macOS: .venv/bin/pip …)
.venv\Scripts\python -m quickz
```

**First run:** the terminal prints a one-time **setup link** (and token). Open it on this computer to create the **Super Admin** account. Setup can only be done once, and only with that token. After that, everyone signs in at `/host` with a username and password.

Then:

1. Sign in, create a quiz (or **Import** the text format below, or try the sample quiz) and press **Host**.
2. Click **Open projector** and put that window on the TV/projector. It shows the join QR code.
3. Players scan the QR (or go to the shown address and type the 5-digit code).
4. Press **Start quiz**. Use **Add 10 test bots** beforehand to rehearse.

## Writing questions

* **WYSIWYG text** for questions *and* answers: bold, italic, underline, strikethrough, sub/superscript (H₂O, x²), bullet and numbered lists, text colours, clear-formatting, undo/redo. Pasted content is cleaned. Everything is sanitised on the server and again in the browser, so players only ever receive a small safe subset of HTML.
* **Audio and video.** Attach an MP3/M4A/WAV/OGG/FLAC clip or an MP4/WebM video (size cap `QUICKZ_MAX_MEDIA_MB`, default 50). Choose where it plays: the **projector** (default), **players' phones**, or both. Tick *Start the answer timer when the media ends* and answers stay locked, and the clock frozen, until the clip finishes on the projector (or the host presses **Start timer now**). Speed is measured from the end of the clip. The host can replay it at any time.
* **Speed weighting per question** (0-100 %): `points = base x (1 - weight x elapsed / time limit)`. 0 % = flat scoring; 100 % = a last-second answer earns nothing. The editor shows a live example. The default (50 %) matches the original behaviour; the host can switch speed scoring off for a whole game.
* **Who can score** per question: everyone, **only the first correct answer** (takes 100 % of the points and the question closes at once), or the **first N** correct answers. Everyone else sees who answered, how fast, and at what time.

## On the fly

* **Add a question mid-game**: next in line or at the end, optionally *saved into the quiz* for next time.
* **Tie-breakers**: when players are level at the podium the host sees a tie alert. One click opens a pre-filled "break the tie" question that **only the tied players** can answer (everyone else watches). It works after the game has finished too: the game reopens for the decider.
* **Per-player report**: on the results page click any player to see every question, what they picked, the correct answer, how long they took, the time of day they answered, their points (with the speed/winner rules applied) and a running score, including manual host adjustments.

## Accounts and roles

| Role | Can do |
|---|---|
| **Super Admin** | Add/disable hosts, reset their passwords, promote other admins, and see every quiz, game and result. |
| **Quiz Host** | Build quizzes and run games. Sees **only their own** quizzes, games and results (other hosts' items return "not found"). |
| Player / projector | No sign-in. Join by code + nickname. |

* The Super Admin adds a host on the **Team** page and gets a **temporary password** (shown once). The host must choose their own password at first sign-in.
* Passwords are stored as salted **scrypt** hashes, minimum 10 characters, with a common-password check.
* Sessions are random tokens in an HttpOnly, SameSite=Lax cookie (7 days by default). Only a **hash** of each token is stored. Changing a password signs out the other devices; "Sign out of all devices" and disabling an account end sessions immediately.
* Sign-in is throttled: 5 failures lock a username for 5 minutes, and 25 failures lock an IP. Error messages never reveal whether a username exists.
* State-changing requests require an `X-Quickz` header (CSRF defence), and the host WebSocket checks the session, the `Origin` and that the game belongs to you (or you're a Super Admin).
* **Forgot the Super Admin password?** `python -m quickz reset-password USERNAME` prints a one-time temporary password.
* Upgrading from the first PIN-based version? Existing quizzes and games are assigned to the first Super Admin you create.

Everything must be on the same network. If phones can't reach the laptop, the venue Wi-Fi probably has "client isolation" — use your phone's hotspot instead.

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `QUICKZ_PORT` | `8000` | Port |
| `QUICKZ_HOST` | `0.0.0.0` | Bind address (use `127.0.0.1` for local-only) |
| `QUICKZ_SETUP_TOKEN` | random each start | Fixed token for the first Super Admin setup (scripted installs) |
| `QUICKZ_SESSION_DAYS` | `7` | How long a sign-in lasts |
| `QUICKZ_DATA` | `./data` | SQLite database + uploaded images |
| `QUICKZ_PUBLIC_URL` | `http://<LAN-IP>:<port>` | Address encoded in the QR when the projector opens the page via `localhost` |

## What the three screens give you

* **Host console** - quiz library with search, covers and a one-click sample quiz; an editor with a live phone preview, drag-to-reorder and autosave; a live console built around one big primary button (Space) with live answer bars, buzzer judging, leaderboard, player management, and quick-add questions; results with accuracy and per-question difficulty. Light and dark themes.
* **Players** - pick an avatar and nickname (or roll a random one), thumb-zone answer tiles with shapes and colours, haptics and sounds, a celebratory reveal with streaks and rank movement, and a personal results card.
* **Projector** - scales from laptop to 4K, QR + code + avatar wall in the lobby, answer distribution on reveal, rank-change leaderboard, buzzer splash, podium. Keys: `F` fullscreen, `M` mute, `D` light/dark.

Everything is served locally: fonts, icons and sounds are bundled or synthesised, so it works with no internet.

## Quiz text format

```
# Quiz title
Optional description

Q: Capital of France?
- Berlin
- Paris*                 <- trailing * marks the correct option
- Rome
time: 20                 <- seconds (5-300)
points: 1000
? Explanation shown after the answer

Q: Which are primes?     <- more than one * = multi-select
- 2*
- 4
- 7*

Q: Name this film from the quote
type: buzzer             <- host judges the spoken answer
```

## Hosting it for free

Run it on **Render's free web service** with a free **S3-compatible bucket** (Cloudflare R2 or Backblaze B2) for persistence: Quickz snapshots its database and copies uploads there, and restores them when Render restarts with an empty disk. A load test with 100 simulated players showed a question reaching every phone in under 20 ms. Full guide, free-tier caveats and alternatives: **[docs/DEPLOY.md](docs/DEPLOY.md)**. A `Dockerfile` and `render.yaml` are included.

```bash
python tools/loadtest.py --players 100 --storm     # try it on your own machine
```

## Develop

```bash
.venv\Scripts\python -m pytest
```

Layout: `quickz/engine.py` (game state machine, scoring, buzzer), `quickz/server.py` (REST + WebSocket), `quickz/db.py` (SQLite), `quickz/questions.py` (validation/import), `quickz/static/` (vanilla JS front end, no build step, no CDN).

## Security notes

* Host actions need a signed-in account; every quiz, game and result is owner-scoped (see Accounts and roles).
* Correct answers are only sent to the host until the reveal.
* All user text is rendered via text nodes or escaped markdown-lite; images come only from validated uploads.
* Meant for a trusted LAN/event. If you expose it to the internet, put it behind HTTPS (the session cookie becomes `Secure` automatically) and consider a reverse proxy rate limit.
