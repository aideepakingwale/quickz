# DiQuizBuzz on AWS: the simple guide (EC2 + CloudFront + S3)

One CloudFormation stack puts DiQuizBuzz online with three AWS services. Allow **about an hour** the first time (most of it
waiting). Every click is in [`AWS_LAUNCH_WALKTHROUGH.md`](AWS_LAUNCH_WALKTHROUGH.md); this page is the short version.

## What you get

```
 phones · projector · host console
            │   https://dxxxxxxxxxxxx.cloudfront.net        ← the only public address (no domain name needed)
            ▼
   ┌──────────────────┐   /uploads/* (pictures)   ┌──────────────────────────┐
   │ Amazon CloudFront│ ────────────────────────► │ S3 bucket (private)      │
   └──────────────────┘                           │  pictures + backups      │
            │  the site, the API, the live game (WebSockets)  └──────────────────────────┘
            ▼                                                        ▲ backups every 6 h and on stop
   ┌──────────────────────────────────────────┐                      │
   │ EC2 t4g.micro: DiQuizBuzz + its database │ ─────────────────────┘
   └──────────────────────────────────────────┘   (you reach it with Session Manager; no SSH port)
```

| Service | Job |
| --- | --- |
| **EC2** | Runs DiQuizBuzz and its SQLite database in Docker. Stop it between events. |
| **CloudFront** | The HTTPS front door. Carries the pages and the live game, and caches pictures worldwide. |
| **S3** | Private storage for uploaded pictures and database backups. Only CloudFront may read the pictures. |

**Cost** (list prices from third-party trackers; check your region in the AWS Pricing Calculator):

| | Running | Stopped |
| --- | --- | --- |
| t4g.micro server | ~$0.0084 / hour | $0 |
| Fixed IP (CloudFront needs a fixed target) + 8 GB disk | ~$4.30 / month | ~$4.30 / month |
| S3 + CloudFront at this size | inside the free tier | cents |

An 8-hour event costs about 7 cents on top of the ~$4.30 a month. Left on all month: about $10.40.

**Server size:** `t4g.nano` up to ~100 players · **`t4g.micro` 300–500 (recommended)** · `t4g.small` 500–1000. You can change the
size later in a minute (stop → change instance type → start).

## Before you start

- An AWS account with an admin user, and a GitHub account.
- Your region: the one nearest your players (UK: **Europe (London) eu-west-2**).
- This project folder and Git on your computer.

## Step 1: put the code on GitHub (10 min)

```powershell
cd D:\Personal\DM-Association\UK\Quickz
git init -b main
git add .
git commit -m "DiQuizBuzz"
git remote add origin https://github.com/OWNER/quickz.git
git push -u origin main
```

Check that `git status` before the commit does **not** list `data/`, `.venv/` or any `.env` file. For a **private** repository,
make a fine-grained GitHub token with *Contents: Read-only* for that one repository. Your **RepoUrl** is then
`https://x-access-token:github_pat_XXXX@github.com/OWNER/quickz.git` (public repository: just the plain URL).

## Step 2: prepare AWS (10 min)

1. Pick your region at the top right of the AWS console and stay in it.
2. **Budgets → Create budget → Zero spend** (or a $10 monthly budget), so AWS emails you if anything costs money.
3. **VPC → Your VPCs**: make sure a *Default VPC* exists (*Actions → Create default VPC* if not).
4. Make a **setup token** (a one-time password for creating your Super Admin): `[guid]::NewGuid().ToString("N")` in PowerShell.
5. Optional but recommended: in **VPC → Managed prefix lists**, copy the ID (`pl-…`) of
   `com.amazonaws.global.cloudfront.origin-facing`, so only CloudFront can reach the server.

## Optional: your own name (e.g. diquizbuzz.deepakingwale.com)

Free if you already own the domain; done with two DNS records (GoDaddy steps are in the published guide).

1. In the AWS console switch to **US East (N. Virginia) us-east-1** (CloudFront reads certificates only from there) →
   **Certificate Manager → Request a public certificate** for `diquizbuzz.deepakingwale.com`, **DNS validation**.
2. Add the certificate's validation **CNAME** at your DNS provider (at GoDaddy, the *Name* without `.deepakingwale.com.`).
3. When it shows **Issued**, copy its ARN, switch back to your region, and in Step 3 fill **CustomDomain** and **CertificateArn**.
4. After the stack is created, add a **CNAME** `diquizbuzz` → the stack output **CloudFrontName**. Use only the new address from then on.

## Step 3: create the stack (5 min to fill in, ~20 min to build)

**CloudFormation → Create stack → With new resources → Upload a template file** →
`deploy\aws\quickz-cloudfront.yaml`. Stack name `quickz`, then:

| Parameter | Value |
| --- | --- |
| VpcId / SubnetId | the default VPC and any subnet in it |
| InstanceType | `t4g.micro` |
| RepoUrl / RepoBranch | from Step 1 / `main` |
| SetupToken | from Step 2 |
| CloudFrontPrefixListId | the `pl-…` ID, or leave empty |
| CustomDomain / CertificateArn | only for your own name (above), else leave empty |
| DiskGb / ImageId | leave the defaults |

On the last page **tick "I acknowledge that AWS CloudFormation might create IAM resources"** and Submit. Wait for
**CREATE_COMPLETE** (CloudFront is the slow part). If it fails, the first red line in the *Events* tab says why; the walkthrough
has a table of fixes.

## Step 4: sign in (5 min)

1. Open the stack's **Outputs**: **HostConsole** is `https://dxxxx.cloudfront.net/host`. It never changes.
2. Wait about **5 more minutes** while the server builds the app (CloudFront shows "502/504" until then).
3. Open HostConsole, enter the **setup token**, and create your Super Admin. The token stops working after that.

## Step 5: rehearse before the first real event (15 min)

1. **Pictures:** upload a picture to a question, host it, and in the projector's browser tools (F12 → Network) check the
   `/uploads/...` request says `server: AmazonS3` and `x-cache: … from cloudfront`.
2. **A crowd:** open a lobby and, from your own computer, send 300 practice players:

   ```powershell
   .\.venv\Scripts\python.exe tools\crowd.py --url https://dxxxx.cloudfront.net --code 12345 --players 300
   ```

   Start the quiz and play it through; the practice players answer every question type by themselves. When it ends, the tool
   prints how many players each question reached: you want 300/300 every time.

## Event day

| When | Do |
| --- | --- |
| 15 min before | EC2 → Instances → **quickz** → Instance state → **Start**; wait 3 min, then sign in. Same address as always. |
| After | Download results, then **Stop** the instance. A backup to S3 is taken automatically as it stops. |

## Looking after it

| Task | How |
| --- | --- |
| Update the app | `git push` from your computer, then in a Session Manager shell (EC2 → quickz → Connect): `sudo bash /opt/quickz/deploy/aws/update.sh` |
| Back up now | Session Manager shell: `sudo /opt/quickz/deploy/aws/backup.sh` |
| Forgot the Super Admin password | Session Manager shell: `sudo docker compose -f /opt/quickz/deploy/aws/docker-compose.cloudfront.yml exec quickz python -m quickz reset-password USERNAME` |
| Remove everything | CloudFormation → quickz → Delete. The S3 bucket is kept on purpose; empty and delete it yourself if you want it gone. |

## Good to know

- **Players can use mobile data.** The site is public, so the venue Wi-Fi doesn't matter.
- **Security:** visitors always use HTTPS. The short hop from CloudFront to the server is plain HTTP inside AWS. The server
  refuses anything without a secret header only CloudFront adds, and the optional prefix list keeps everyone else out at the
  firewall. For encryption all the way to the server you need a domain name and the other template (`quickz-ec2.yaml`).
- **Print QR codes only after the stack exists, and don't delete it afterwards.** A new stack gets a new CloudFront address,
  and printed codes would point to the old one.
- **Not yet tried on a real AWS account.** The templates pass CloudFormation's checker. The Docker setup was rehearsed
  locally behind a simulated CloudFront, and the app has played full games with 300 simulated players answering every
  question type. The live game over CloudFront's WebSockets is what Step 5 confirms on your account, so don't skip it.

## If something goes wrong

| Problem | Try |
| --- | --- |
| "502/504 The request could not be satisfied" | The server is stopped or still starting. Wait 3–5 minutes. Still failing: Session Manager shell → `sudo docker compose -f /opt/quickz/deploy/aws/docker-compose.cloudfront.yml logs --tail 50 quickz` |
| Nothing after 25 minutes on first launch | `sudo cat /var/log/quickz-setup.log`. Usually a wrong or expired RepoUrl token: delete the stack and create it again. |
| Players stuck on "Connecting" | WebSockets aren't getting through CloudFront. Send the rehearsal output and the logs above for a look. |
| Slow or out of memory with a big crowd | Stop → change instance type to `t4g.small` → start. |
| Unexpected bill | Billing → Cost Explorer. Usually the server left running, or a second stack left behind. |
