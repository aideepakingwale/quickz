# Hosting DiQuizBuzz on AWS EC2 for the least money

> **Which template?** There are two, in `deploy/aws/`:
>
> | | `quickz-cloudfront.yaml` (**chosen setup**) | `quickz-ec2.yaml` |
> | --- | --- | --- |
> | Public address | `https://dxxxx.cloudfront.net` from AWS: no domain, no DuckDNS | your own name (DuckDNS or a domain), HTTPS by Caddy |
> | Pictures | S3 + the same CloudFront address | S3 + CloudFront on a second address, or served by the server |
> | Fixed IP needed | yes (about $3.65 a month, also while stopped) | no (the name follows a changing IP) |
> | Cheapest idle cost | about $4.30 a month | about $0.70 a month |
> | Traffic from CloudFront to the server | plain HTTP (secret-header guarded) | not applicable (visitors reach Caddy over HTTPS) |
>
> **Want the click-by-click version?** Follow [`AWS_LAUNCH_WALKTHROUGH.md`](AWS_LAUNCH_WALKTHROUGH.md) (CloudFront only): GitHub, the CloudFormation form, first sign-in, a 300-player rehearsal, and the event-day routine. The rest of this page describes the other (domain name) setup in more detail.

One small server runs everything: the app, automatic HTTPS (Caddy + Let's Encrypt), and its own database on the server's disk.
Nothing else is needed (no load balancer, no NAT gateway, no database service), and those are where AWS bills usually come from.

## What it costs

Prices are US East list prices from third-party price trackers; **check the AWS pricing pages for your region** (London costs
roughly 10-15 % more) before relying on them.

| Piece | Price | Per month if always on |
| --- | --- | --- |
| `t4g.nano` (2 vCPU burstable, 0.5 GB) | about $0.0042 / hour | about $3.1 |
| `t4g.micro` (1 GB) | about $0.0084 / hour | about $6.1 |
| `t4g.small` (2 GB) | about $0.0168 / hour | about $12.3 |
| Public IPv4 address (AWS charges for every one, since Feb 2024) | $0.005 / hour **while it exists** | about $3.65 |
| 8 GB `gp3` disk | about $0.08 / GB-month | about $0.64 |
| Traffic out to players | first 100 GB / month free, all services combined | $0 for any normal event |
| Off-server backups in S3 | pennies | < $0.10 |

So there are two sensible ways to run it:

* **Event mode (cheapest): stop the server between events.** A stopped instance costs only its disk, about **$0.70 a month**;
  a running one costs about **$0.01 an hour** (`t4g.nano`: $0.0092, `t4g.micro`: $0.0134 with the IP). An 8-hour event is
  about **7-11 cents**. The address changes every time you start it, so a free DuckDNS name follows it automatically.
* **Always on:** about **$7.40 / month** (`t4g.nano`) or **$10.40** (`t4g.micro`). Add an Elastic IP only if you need a fixed
  address; it costs the same $3.65 as the normal public IP, but it is also billed while the server is stopped.

New AWS accounts get free credits (reported at up to $200 for 6 months) which cover all of this at first. If you are on that
"Free plan", only certain instance types may be launched (reported: `t3.micro/small`, `t4g.micro/small`), so pick
**`t4g.micro`**; on a normal paid plan `t4g.nano` is fine. Either way, set a budget alert first (below).

## How many players, and which size?

I measured the server with simulated phones: 300 and 800 players through Caddy and HTTPS in the same Docker setup the server
runs, plus a no-network profile of the game engine. Every phone got every question, answered, and received the reveal.

| Players | Server app memory | Caddy | Server CPU while a question runs | Question reaches phones |
| --- | --- | --- | --- | --- |
| 300 | about 85 MB | about 55 MB | about 13 % of one core | about 60 ms |
| 800 | about 120 MB | about 125 MB | about 17 % of one core | about 60-140 ms |

Docker, the operating system and the app together add roughly 300 MB, so:

| Players in one game | Pick | Why |
| --- | --- | --- |
| up to ~100 | `t4g.nano` (0.5 GB) | works, but with almost no memory to spare |
| **300 - 500** | **`t4g.micro` (1 GB)** | comfortable; the setting to use for "300+" |
| 500 - 1,000 | `t4g.small` (2 GB) | more memory for sockets and for big pictures |

* The server is a single process (one core) by design, because the live game lives in memory. One core handled 800 simulated
  players on a laptop with room to spare; the burstable instance's CPU credits are not a concern (a whole 20-question game for
  300 players needs well under a minute of CPU).
* The player limit per game is `QUICKZ_MAX_PLAYERS` (the compose file sets 1,000; the program default is 500).
* **These numbers come from a laptop and a Docker VM, not from a real `t4g` instance**, whose cores are slower. Do one
  dress rehearsal at your real size (below) before the event.
* A cloud server has a big advantage for a crowd: phones do not need to be on the venue Wi-Fi. Players can use mobile data,
  and 300 phones on one venue access point is often the weakest link, so tell people they can switch Wi-Fi off.

### Rehearsing at 300+

From your own computer (not the server), with a game created and open in the host console:

```
python tools/crowd.py --url https://<yourname>.duckdns.org --code CODE --players 300
```

Press **Start**, play a few questions, and watch the server with `sudo docker stats`. If the host console is quick to react and
`free -m` shows memory to spare, you are fine. If not, change the instance type while stopped (a minute's work).

## Pictures for a big crowd: S3 + CloudFront (built in)

With hundreds of phones the pictures are most of the traffic, not the game. DiQuizBuzz is designed for **pictures from your
storage and video from public platforms**:

* **Pictures:** uploaded in the editor (shrunk in the browser first), stored in **S3**, and delivered to phones by
  **CloudFront**, so the small server never carries them.
* **Video:** paste a **YouTube or Vimeo link** (or use a GIF from Giphy etc.). Nothing to host. Uploaded audio/video is still
  possible for short clips; this setup caps it at 25 MB (`MAX_MEDIA_MB` in the template).

**How to get it:** leave **UseCdn = true** on the CloudFormation form (it is the default). The stack then also creates:

| What | Purpose |
| --- | --- |
| a private S3 bucket | holds uploaded pictures and your backups |
| a CloudFront distribution | the public address phones fetch pictures from (`PicturesCdn` in the stack Outputs) |
| a bucket policy | lets **only that distribution** read `quickz/uploads/*`; your database backups stay private |
| the server's IAM role | used by the app to upload, so **no access keys exist anywhere** |

Cost: CloudFront's first 1 TB and 10 million requests a month are free (the always-free tier), S3 storage is a couple of cents
per GB-month, and uploads are a fraction of a cent per thousand. A year of quizzes is typically pennies.

**What happens:** when you upload a picture the server saves it and copies it to S3 in the background. Until the copy is
confirmed, phones are served the picture by the server itself; after that, `/uploads/<file>` redirects them to CloudFront (and
the browser remembers it for a day). Nothing breaks if S3 or CloudFront is unreachable: the server simply keeps serving
pictures itself. Pictures are named by random IDs and never change, so the CDN never needs clearing.

**What to expect:**

* The stack takes **15-20 minutes** instead of ~5, because CloudFront takes a while to roll out. Then add about 5 more minutes for
  the server's first start.
* After the stack is ready, open the host console, upload one picture, play it once and check the browser's network tab: the
  picture should come from `...cloudfront.net`. Do this the day before an event.
* The database is also copied to the bucket every few seconds while the game changes it (a bonus live backup).
* The server's metadata "hop limit" is set to 2 (not 1) so the Docker container can read its IAM role; nothing else changes.

**Without the CDN** (`UseCdn = false`) the server delivers the pictures. That is fine for ordinary quizzes of 300 players: the
app shrinks uploads and each phone preloads the next question's pictures at a random moment, which spreads the load.

**By hand instead of the template:** create a private bucket and a CloudFront distribution with *Origin access control*, allow
`s3:GetObject` on `quickz/uploads/*` for that distribution, give the server's role `s3:PutObject/GetObject/ListBucket` on the
bucket, set the instance's metadata hop limit to 2, and put in the user data: `CDN_URL=https://dxxxx.cloudfront.net`,
`S3_ENDPOINT=https://s3.<region>.amazonaws.com`, `BACKUP_BUCKET=<bucket>`.

**Not on AWS?** Any S3-compatible bucket also works with access keys: set `QUICKZ_S3_ENDPOINT`, `QUICKZ_S3_BUCKET`,
`QUICKZ_S3_KEY_ID`, `QUICKZ_S3_SECRET` and `QUICKZ_PUBLIC_BUCKET_URL` in `/opt/quickz/deploy/aws/.env` (see `DEPLOY.md`, 3b; for
Cloudflare R2 use a custom domain, not the rate-limited `r2.dev` address).

Plain AWS traffic is fine otherwise: the first 100 GB a month out of AWS is free.

## Before you start

1. **The code in a Git repository** that the server can clone (GitHub is fine). This folder is not a repo yet:
   `git init`, commit, and push it. For a private repo create a read-only fine-grained token and use the address
   `https://x-access-token:TOKEN@github.com/OWNER/REPO.git`.
2. **A name for the server.** Free: create one at [duckdns.org](https://www.duckdns.org) (for example `ourquiz` gives
   `ourquiz.duckdns.org`) and copy your **token**. Or use your own domain and an Elastic IP.
3. **A budget alert** so nothing can surprise you: *Billing -> Budgets -> Create budget -> Zero spend* or a $5 monthly budget,
   with your email.
4. Pick the **region** nearest your players (for the UK, London `eu-west-2` or Ireland `eu-west-1`).

## Option A: one form (CloudFormation, recommended)

1. AWS console -> **CloudFormation** -> *Create stack* -> *With new resources* -> **Upload a template file** ->
   `deploy/aws/quickz-ec2.yaml`.
2. Fill the form. The ones that matter:

   | Parameter | What to put |
   | --- | --- |
   | VpcId / SubnetId | the default VPC and any of its subnets |
   | InstanceType | `t4g.micro` for 300-500 players (`t4g.nano` only for ~100; `t4g.small` for 500-1,000) |
   | RepoUrl | the Git address from step 1 |
   | DuckDnsName / DuckDnsToken | from duckdns.org (leave **Domain** empty) |
   | SetupToken | a long random string; you type it once to create the Super Admin |
   | UseElasticIp | `false` for event mode, `true` for always on with a fixed address |
   | UseCdn | `true` (S3 + CloudFront for pictures; recommended for 300+) |
| CreateBackupBucket | `true` |

3. Create the stack and wait for `CREATE_COMPLETE` (about 5 minutes, or **15-20 with UseCdn** because CloudFront is slow to roll out),
   then about **5 more minutes**: the first start installs Docker and builds the app. The **Outputs** tab shows the address (`HostConsole`).
4. Open `https://<yourname>.duckdns.org/host`. The first visit asks for the **setup token** to create the Super Admin account.
   After that the token is useless.

The server has **no SSH port open**. To get a shell (for logs or updates) use Session Manager:
`aws ssm start-session --target i-0123...` (the exact command is in the stack Outputs), or *EC2 -> Connect -> Session Manager*
in the console.

## Option B: by hand in the console

*EC2 -> Launch instance*: name `quickz`, image **Ubuntu Server 24.04 LTS (Arm)**, type `t4g.micro` (or `t4g.small` for 500+ players), **no key pair**,
a security group that allows **80 and 443** (TCP, and UDP 443) from anywhere and nothing else, an 8 GB `gp3` disk, an
**IAM instance profile** with the `AmazonSSMManagedInstanceCore` policy, and under *Advanced details*:
*Metadata version* = V2 only, *User data* = the edited contents of `deploy/aws/user-data.sh`.

## Running events (event mode)

| When | Do this |
| --- | --- |
| About 15 minutes before | Start the instance (console, or `aws ec2 start-instances --instance-ids i-0123...`). It takes 1-2 minutes; the name updates itself. |
| Check | Open `https://<yourname>.duckdns.org/host` and sign in. Rehearse with the **Add 10 test bots** menu. |
| After | Download your results, then **stop** the instance (console, or `aws ec2 stop-instances ...`). A backup is taken automatically as it stops. |

For a dress rehearsal from your own computer: `python tools/crowd.py --url https://<yourname>.duckdns.org --code CODE --players 300`
(see "How many players" above). If the server struggles, change the instance type in the console while it is stopped: that takes a minute.

## Looking after it

* **Logs / status** (from a Session Manager shell): `cd /opt/quickz/deploy/aws && sudo docker compose logs --tail 100 quickz`
  and `sudo docker compose ps`. The setup log is `/var/log/quickz-setup.log`.
* **Update to new code:** push to Git, then on the server `sudo bash /opt/quickz/deploy/aws/update.sh` (backs up first).
* **Backups:** a consistent copy of the database every 6 hours and on every stop (newest 14 on the disk, plus S3 if you made
  the bucket, including your uploaded pictures). Take one by hand: `sudo /opt/quickz/deploy/aws/backup.sh`.
* **Restore** (new or broken server): `aws s3 cp s3://BUCKET/db/latest.db.gz .`, then
  `sudo docker compose -f /opt/quickz/deploy/aws/docker-compose.yml stop quickz`,
  `gunzip -c latest.db.gz | sudo tee /opt/quickz-data/quickz.db >/dev/null`,
  `sudo rm -f /opt/quickz-data/quickz.db-wal /opt/quickz-data/quickz.db-shm`,
  `sudo chown 10001:10001 /opt/quickz-data/quickz.db`, `aws s3 sync s3://BUCKET/uploads /opt/quickz-data/uploads`
  (then `chown -R 10001:10001` it) and start the app again.
* **Forgot the Super Admin password:** `sudo docker compose -f /opt/quickz/deploy/aws/docker-compose.yml exec quickz python -m quickz reset-password USERNAME`.
* **Throw it all away:** delete the CloudFormation stack (the backup bucket and its contents are deliberately kept; delete
  it yourself when you no longer want them). If you used an Elastic IP, it is released with the stack.

## Why it is built this way

* **No NAT gateway, load balancer or managed database:** each of those costs more per month than this whole setup.
* **One server process:** live game state is held in memory by design; you do not need (or want) several copies.
* **Caddy** gets and renews the certificate by itself and passes the live-game WebSocket straight through. The certificate is
  stored on the disk, so stopping and starting the server does not request a new one.
* **No SSH, IMDSv2 only, web ports only:** nothing to brute-force. Updates to the operating system are installed automatically
  by Ubuntu.
* **Fast writes:** the database runs in SQLite's WAL mode with `synchronous=NORMAL`, so an answer does not wait for the disk
  (800 answers: about 0.6 s of waiting became 16 ms). If the whole machine crashes, the last instant of answers could be lost;
  the database itself stays intact. The 6-hourly and on-stop backups cover the rest.
* **`unlimited` CPU credits:** the burstable instance is never throttled in the middle of a quiz; the worst case for a long,
  heavy event is a few cents of extra CPU time.
* The setup token you passed is stored in the instance's launch data. It is only useful until the Super Admin exists.

## If something goes wrong

| Symptom | Likely cause and fix |
| --- | --- |
| Browser says the connection is not private / cannot connect after a start | DNS has not caught up (wait a minute) or the DuckDNS token is wrong: run `sudo /opt/quickz/deploy/aws/update-dns.sh` and read its output. |
| Caddy cannot get a certificate | The name must point at this server and ports 80 and 443 must be open. Let's Encrypt allows only a few attempts per hour: fix the cause, wait, and run `sudo docker compose restart caddy`. |
| Nothing at all after about 10 minutes on first launch | Read `/var/log/quickz-setup.log`. Usual causes: a wrong or private `RepoUrl`, or running out of memory on `t4g.nano` while building (re-run `sudo bash /opt/quickz/deploy/aws/bootstrap.sh` with the same variables, or use `t4g.micro`). |
| "Out of memory" during a big game | `free -m`. Move up one size (`t4g.micro` -> `t4g.small`), and avoid large uploaded videos (use YouTube/Vimeo links, which cost the server nothing). |
| Slow to join when hundreds scan the QR code at once | Normal for a few seconds (each join is a database write). Players just wait for the lobby screen. |
| Bill higher than expected | Look for a forgotten **Elastic IP**, a second instance, or large EBS snapshots. Public IPv4 addresses are billed per hour even when idle. |

If you only need the quiz for a few events a year, also see the free options in `DEPLOY.md`; an always-free setup there trades
a one-minute wake-up delay for $0.
