# Hosting Quickz for free (Render + a free bucket)

**Plan:** run the app on **Render's free web service** and keep your data in a **free S3-compatible bucket**
(Cloudflare R2 or Backblaze B2). Quickz stores accounts, quizzes, results and uploaded media there automatically,
so Render's wiped-on-restart disk stops mattering.

> Why a bucket and not a hosted database? A game must never wait on the network. Every answer is written
> to the database; with a remote Postgres 20-80 ms away, 100 players answering would stall the whole game loop.
> Quickz keeps SQLite as its fast local store and snapshots it to the bucket a few seconds after any change.
> Same durability, no latency on the hot path. (A remote Postgres is also not free forever on Render:
> its free databases expire after 30 days.)

## 1. Create the bucket (5 minutes)

**Cloudflare R2** (10 GB free, no egress fees)
1. Cloudflare dashboard -> R2 -> *Create bucket* (e.g. `quickz-data`).
2. R2 -> *Manage API tokens* -> *Create API token* with **Object Read & Write** on that bucket.
3. Note: *Access Key ID*, *Secret Access Key*, and your *Account ID*.
   - `QUICKZ_S3_ENDPOINT` = `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
   - `QUICKZ_S3_REGION` = `auto`

**Backblaze B2** (10 GB free)
1. B2 -> *Buckets* -> *Create a bucket* (private). Note the S3 endpoint shown, e.g. `s3.us-west-004.backblazeb2.com`.
2. *App Keys* -> *Add a new application key* limited to that bucket. Note *keyID* and *applicationKey*.
   - `QUICKZ_S3_ENDPOINT` = `https://s3.us-west-004.backblazeb2.com`
   - `QUICKZ_S3_REGION` = `us-west-004` (must match the endpoint)

Free-tier terms change often; confirm on the provider's pricing page before relying on them.

## 2. Deploy on Render

1. Push this folder to a GitHub repository (the `data/` folder and `.venv` are already git-ignored).
2. Render -> **New +** -> **Blueprint** -> choose the repo. It reads `render.yaml` (Docker, free plan, health check `/healthz`).
3. When asked, fill the four secrets: `QUICKZ_S3_ENDPOINT`, `QUICKZ_S3_BUCKET`, `QUICKZ_S3_KEY_ID`, `QUICKZ_S3_SECRET`
   (and `QUICKZ_S3_REGION` for B2).
4. Deploy. Open the service's **Logs**: on the very first start Quickz prints a **Setup link**. Open it to create the
   Super Admin. (It only appears while no accounts exist, and the token is only in your logs.)
5. Sign in, open **Team**: the **Cloud backup** card should show *last saved ... seconds ago*.
6. **Prove it:** Render -> *Manual Deploy -> Restart*. Sign in again. Your quizzes are still there.

Render supplies `PORT`, `RENDER_EXTERNAL_URL` (used for the QR codes) and HTTPS. Quickz detects Render and trusts its
proxy headers automatically (secure cookies, correct client IPs for the sign-in lockout).

## 3. What 100 concurrent players looks like

Measured with `python tools/loadtest.py --players 100 --storm` (100 real WebSocket clients, 4 questions, on a laptop):

| | result |
|---|---|
| Question reaches all 100 phones | p50 10 ms, worst 18 ms |
| Answer acknowledged | p50 4 ms, worst 25 ms |
| Reveal reaches all 100 phones | worst 28 ms |
| Server CPU for the whole game | 0.9 s (about 6 % of one core) |
| Server memory | 57 MB idle, 86 MB with 100 players |
| 100 phones reconnecting at once | 0.1 s |

Render's free instance is much smaller than a laptop (reported as a fraction of a CPU and 512 MB), so expect the same
shape with larger numbers. **Do a rehearsal**: join 10 phones, use *Add 10 test bots*, run two questions, 10 minutes before.
Re-run the test against your own deployment's numbers with `tools/loadtest.py` (point it at a staging copy).

## 4. Event-day checklist for a free tier

- **Wake it up first.** Free services sleep after 15 minutes without traffic and take about a minute to wake.
  Open the site 5-10 minutes before guests arrive. During a game the phones' WebSocket traffic keeps it awake.
- **Keep clips short and play them on the projector only** ("Play on: Projector"). Media sent to 100 phones multiplies
  bandwidth by 100 and free plans have small monthly allowances.
- Keep the **host console open** during the game; it is the only thing that can advance questions.
- One instance only. Live game state is in memory; running two replicas would split the room.
- After the event: *Results -> Download CSV*, and optionally *Export* your quizzes as JSON.

## 5. If something goes wrong

| Symptom | Cause / fix |
|---|---|
| Log says `SignatureDoesNotMatch` | The secret key (or key id) is wrong. R2: use the **Secret Access Key** shown once when you create the API token (not the "token value"). B2: use the application key itself, with its keyID as the key id. Re-paste both without spaces, and keep the key's permission at *Object Read & Write* for this bucket. The log line also prints the region/endpoint/lengths it used. |
| Service won't start, log says *Refusing to start with an empty database* | The bucket couldn't be read (wrong key, endpoint or region). Fix the `QUICKZ_S3_*` values. This protects your data. |
| Cloud backup card says *last error* | Bucket unreachable or key lacks write permission. The game keeps running; it retries every 10 s. |
| Everyone signed out after a restart | Shouldn't happen (sessions are in the database). Check the backup card is green. |
| Setup link appears again | The database was not restored: check the bucket settings before creating a second admin. |
| Phones can't connect | Use the **https** address shown on the projector; check the service isn't asleep. |

## 6. Other places to host it

| Option | Always on? | Persistent data | Notes |
|---|---|---|---|
| **Your laptop + a free Cloudflare Tunnel** | while the laptop is on | yes | Best for one-off events. Use a *named* tunnel (free account); the anonymous "quick tunnel" caps at 200 in-flight requests and has no uptime guarantee. |
| **Oracle Cloud Always Free VM** | yes | yes (disk) | Only truly always-on free option; needs a card for verification and its free Arm allowance was reportedly reduced in 2026. Run the Docker image behind Caddy for HTTPS. |
| **Render free** (this guide) | sleeps after 15 min idle | via the bucket | Easiest. Cold start about a minute. |
| Koyeb free | sleeps | via the bucket | May require card verification; check current terms. |
| Fly.io | - | - | No longer free for new accounts. |
| Vercel / Netlify / PythonAnywhere free | - | - | Not suitable: no long-lived WebSockets. |

Sources for the free-tier facts above (checked October 2026): Render's [Deploy for Free](https://render.com/docs/free),
Cloudflare's [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/),
Oracle's [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm).
