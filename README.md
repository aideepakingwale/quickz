# DiQuizBuzz

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

## Question types

Press **Add question** in the editor and pick a type (or hit **Showcase quiz** on the dashboard for a ready-made quiz with one of everything, pictures included).

| Type | What players do | Scoring |
| --- | --- | --- |
| **Single choice**, **Multiple answers**, **True / False** | Tap the answer(s). Every option can have text, a **picture**, or both (flags, logos, diagrams). | All or nothing, with speed weighting |
| **Poll** | Vote. Results fill up live on the projector. | No points |
| **Type the answer** | Type it. Capitals, punctuation, accents and "the/a/an" never matter, and small typos are forgiven (switchable). After the reveal the host can **Accept** a variant they didn't expect (or mark one **Not correct**): scores update at once. | All or nothing |
| **Closest number** | Type a number or drag a slider. Optional tolerance, unit, and **partial points** that fade with distance (a real "closest wins"). | Full inside the tolerance, partial beyond |
| **Put in order** | Drag items (or use the arrows) into sequence. Items can be text or pictures; each player sees them shuffled. | All right = full; nearly right = partial |
| **Match pairs** | Tap a card, tap its partner. Pairs are colour-coded. | Per correct pair |
| **Pin on a picture** | Tap the right spot on a map, photo or diagram. The reveal shows the target and everybody's pins. | Inside the circle = full; just outside = fading share |
| **Jigsaw** | Upload a picture; DiQuizBuzz cuts it into a grid (2×2 up to 48 pieces) and scrambles it differently on every phone. Players tap two pieces to swap them, or drag one onto another, then **Submit**. The finished picture is never shown on phones; optionally add a **hint button** that reveals it for a set number of marks taken off what the player earns. The big screen shows the race live: who has finished, how fast, and what they earned. | A race: all-or-nothing by default (optional part marks, never more than half). Set **how many** finishers score and the **marks for each place** (e.g. 100%, 60%, 30%) |
| **Buzzer** | Race to buzz; the host judges the spoken answer. Reaction times are shown, with a photo-finish note, and every player's buzzer has its own sound on the big screen. | Host decides |
| **Slide** | Nothing: a round title, rule or "halfway" standings screen. No timer; the host presses Continue. | n/a |

**Media**: upload an MP3/M4A/WAV/OGG/FLAC clip or MP4/WebM video, **or paste a YouTube / Vimeo link**. Embeds take a *start* and *stop* time and an **Audio only** switch that hides the picture, title and thumbnails for "name that tune". Both can trigger "start the answer timer when the media ends". They need internet on the devices that play them; only YouTube and Vimeo are ever embedded.

**Pictures** can be uploaded or linked from Giphy, Tenor, Wikimedia, Imgur, Unsplash, Pexels, Pixabay or Flickr (extend with `QUICKZ_IMAGE_HOSTS`).

Extras on every question: a **category** tag (coloured chip), a **picture effect** for "guess the picture" rounds (*blur to clear* or *zoom out* as the clock runs down, so quick guesses earn more with speed weighting), an **explanation picture**, audio/video, and the speed and "who can score" rules below.

**Pictures** are shrunk in the browser before upload (large photos become a few hundred KB) so phones on slow Wi-Fi stay fast. GIFs stay animated.

**Game options** (chosen when you open the lobby, or changed live where it makes sense):

* **Double up** - every player may double the points of *one* question, chosen before answering. Spent whether or not it pays off.
* **Colour theme** - Classic, Ocean, Sunset, Forest, Royal or Midnight (a dark theme for dim rooms) for the projector and every phone.

## Writing questions

* **WYSIWYG text** for questions *and* answers: bold, italic, underline, strikethrough, sub/superscript (H₂O, x²), bullet and numbered lists, text colours, clear-formatting, undo/redo. Pasted content is cleaned. Everything is sanitised on the server and again in the browser, so players only ever receive a small safe subset of HTML.
* **Audio and video.** Attach an MP3/M4A/WAV/OGG/FLAC clip or an MP4/WebM video (size cap `QUICKZ_MAX_MEDIA_MB`, default 50). Choose where it plays: the **projector** (default), **players' phones**, or both. Tick *Start the answer timer when the media ends* and answers stay locked, and the clock frozen, until the clip finishes on the projector (or the host presses **Start timer now**). Speed is measured from the end of the clip. The host can replay it at any time.
* **Speed weighting per question** (0-100 %): `points = base x (1 - weight x elapsed / time limit)`. 0 % = flat scoring; 100 % = a last-second answer earns nothing. The editor shows a live example. The default (50 %) matches the original behaviour; the host can switch speed scoring off for a whole game.
* **Who can score** per question: everyone, **only the first correct answer** (takes 100 % of the points and the question closes at once), or the **first N** correct answers. Everyone else sees who answered, how fast, and at what time.
* **Marks by finishing place** (any question where answers race): the 1st correct answer earns, say, 100 % of the points, the 2nd 60 %, the 3rd 30 % (`places: 100, 60, 30` in the text format). Replaces speed weighting for that question.

## The show (big-screen atmosphere)

DiQuizBuzz plays like a game show. **The projector is the room's sound system** and phones are silent by default (tap the speaker on a phone to hear it there too; vibration still works).

* **Drum-roll before the answer** (game option: instant, 2, 3 or 5 s). When the clock ends, answers lock, the room dims to "Final answers locked" with a suspense beat, then the answer appears. Pressing Reveal during the pause skips it.
* **Sound packs**: *Game show*, *Arcade* and *Quiet*, all synthesised in the browser (no audio files to license): a lobby loop, a "thinking" heartbeat that speeds up as time runs out, stingers, applause when most people got it right and a sympathetic "aww" when few did. The projector has a volume slider; **R** reads each question aloud (text-to-speech, off by default).
* **Avatars fly into the bars** at the reveal, and a badge says *"Only 12% got it"* or *"Everyone got it!"*.
* **Host soundboard** in the live console (keys **1-7**): drumroll, applause, aww, ding, wrong, airhorn, fanfare. They play on the projector.
* **Emoji reactions** (game option): phones can throw 👏 😂 🔥 😮 🎉 (and 40 more behind a ＋ button: ❤️ 🤩 🥳 🤯 🚀 🏆 …) at the big screen between questions (one per second per phone). The emoji float up on the projector **and** the host console, each with the sender's avatar and name.
* **Buzzer**: reaction time to the millisecond, "photo finish" gaps, and a unique buzzer sound per player.

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
| `QUICKZ_MAX_PLAYERS` | 500 | Players allowed in one game (tested to 800 on one small server; see docs/DEPLOY_AWS.md for sizing) |
| `QUICKZ_SESSION_DAYS` | `7` | How long a sign-in lasts |
| `QUICKZ_DATA` | `./data` | SQLite database + uploaded images |
| `QUICKZ_PUBLIC_URL` | `http://<LAN-IP>:<port>` | Address encoded in the QR when the projector opens the page via `localhost` |

## What the three screens give you

* **Host console** - quiz library with search, covers and a one-click sample quiz; an editor with a live phone preview, drag-to-reorder and autosave; a live console built around one big primary button (Space), with **Skip** to jump to the next question and **Run again** (new timer) when nobody answered or nobody got it right with live answer bars, buzzer judging, leaderboard, player management, and quick-add questions; results with accuracy and per-question difficulty. Light and dark themes.
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

Q: Capital of Spain?
type: text               <- players type it
= Madrid                 <- accepted answers (as many as you like)
category: Geography      <- coloured tag on the question

Q: Year of the Moon landing?
type: number
answer: 1969
tolerance: 1             <- optional; also: closest: yes, spread: 30, min: 1900, max: 2020, unit: km

Q: Planets from the Sun
type: order              <- list the items in the CORRECT order
- Mercury
- Venus
- Earth

Q: Match the animal to its sound
type: match              <- "left = right" pairs
- Dog = Woof
- Cat = Meow

Q: Tea or coffee?
type: poll
- Tea
- Coffee

Q: Round 2: Music
type: slide              <- also board: yes to show the standings
```

Pictures (answers, pin maps, effects) are added in the editor: the text format covers everything else.

## Events: register people a week ahead

Create an **event** in the host console to get a permanent link and QR code you can publish well in advance. People register once
(real name, email, a 4-digit PIN, avatar); on the day the same page lets them straight into the game when you open the lobby, and
walk-ins can still register until you press Start. No email is sent by DiQuizBuzz: attendees get calendar reminders (Google or .ics)
and you can copy the address list to message them yourself. See **`docs/EVENTS.md`**.

## Hosting it on AWS (cheapest EC2 setup)

One small Graviton instance with automatic HTTPS, no SSH, backups, and an "event mode" where you stop the server between events and pay roughly $0.70 a month (about 10 cents for an 8-hour event). Everything is in `deploy/aws/`; the guide is **`docs/DEPLOY_AWS.md`**.

**Simplest route (EC2 + CloudFront + S3, no domain name needed):** the short guide is **`docs/AWS_SIMPLE_GUIDE.md`**; every click is in `docs/AWS_LAUNCH_WALKTHROUGH.md`.

## Hosting it for free

Run it on **Render's free web service** with a free **S3-compatible bucket** (Cloudflare R2 or Backblaze B2) for persistence: DiQuizBuzz snapshots its database and copies uploads there, and restores them when Render restarts with an empty disk. A load test with 100 simulated players showed a question reaching every phone in under 20 ms. Full guide, free-tier caveats and alternatives: **[docs/DEPLOY.md](docs/DEPLOY.md)**. A `Dockerfile` and `render.yaml` are included.

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

## Brand

DiQuizBuzz — *The Quiz Buzz Platform by Di – Deepak Ingwale*. The logo files are vectors (SVG) in `quickz/static/brand/`:

| File | Use it for |
|---|---|
| `diquizbuzz-logo.svg` / `-dark.svg` | Full logo with tagline, on light / dark backgrounds |
| `diquizbuzz-wordmark.svg` / `-dark.svg` | Name only, for headers and small spaces |
| `diquizbuzz-card.svg` / `.png` | 16:9 card on navy for slides, WhatsApp and social posts (PNG is 3200×1800) |
| `diquizbuzz-mark.svg` | Square icon (also the browser-tab icon, `favicon.svg`) |

They are generated from the bundled Inter font by `tools/make_logo.py` (needs `pip install fonttools brotli`), so the letters are outlines and look the same everywhere.

**Logo pack** (for printers, partners and social media): `brand-pack/DiQuizBuzz-Logo-Pack.zip`, with every version as SVG, PDF and transparent PNG, single-colour white and black versions, app icons and `favicon.ico`, ready-sized social images and a brand guide. Rebuild it with `python tools/make_logo_pack.py` (needs `pip install fonttools brotli pillow` and Microsoft Edge or Chrome).

