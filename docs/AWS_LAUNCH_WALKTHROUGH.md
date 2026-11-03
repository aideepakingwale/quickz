# Launching DiQuizBuzz on AWS with CloudFront only (no domain name): step by step

Follow this top to bottom the first time (allow **about 1 hour**, most of it waiting). Tick the boxes as you go.
The background is in `DEPLOY_AWS.md`; this page is only the "what do I click" version.

**What you will end up with**

```
 phones, projector and host console
            |  https://dxxxxxxxxxxxx.cloudfront.net      <- the ONLY public address (AWS gives it to you; no domain, no DuckDNS)
            v
      [ Amazon CloudFront ] ---- /uploads/* (pictures) ----> [ S3 bucket ]       (private; CloudFront may read pictures only)
            |  everything else: the site, the API and the live game (WebSockets)
            v
      [ one EC2 server: DiQuizBuzz + its database ]     reached by you through Session Manager (no SSH port)
```

Players never type the address: they scan the QR code on the projector, which already contains it.

**What it costs (US East list prices from third-party sources: check your region):**

| | While you use it | While it is stopped |
| --- | --- | --- |
| `t4g.micro` server | about $0.0084 / hour | $0 |
| Fixed IP address (CloudFront needs a fixed target) | $3.65 / month | $3.65 / month |
| 8 GB disk | $0.64 / month | $0.64 / month |
| S3 + CloudFront at this size | free tier (first 1 TB, 10 M requests / month) | cents |

So **about $4.30 a month idle** plus a cent an hour while running (an 8-hour event is about 7 cents), or about **$10.40 a month
always on**. The fixed IP is the price of "CloudFront only, no domain": a server's own AWS name changes whenever it is stopped.

---

## Part 0. What you need before you start

- [ ] An **AWS account** with admin rights (an IAM admin user is better than the root user).
- [ ] A **GitHub account** (free) to hold the code; the server downloads it from there.
- [ ] This project folder on your computer and **Git** installed (`git --version` works in PowerShell).
- [ ] About **15 minutes** at the start and **20-25 minutes** of waiting later.

Decide now: your **AWS region**: the one nearest your players. UK: **Europe (London) `eu-west-2`**.

---

## Part 1. Put the code on GitHub (10 minutes)

The project folder is not a Git repository yet.

1. [ ] Open **PowerShell** and run:

   ```powershell
   cd D:\Personal\DM-Association\UK\Quickz
   git init -b main
   git add .
   git status
   ```

   Read the list: it must **not** contain `data/`, `.venv/`, or any `.env` file (they are already ignored). If you see them, stop and ask.

2. [ ] Save the first snapshot:

   ```powershell
   git commit -m "DiQuizBuzz"
   ```

   (If Git asks who you are: `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`, then repeat.)

3. [ ] On github.com click **+ -> New repository**. Name it `quickz`. Choose **Private** (recommended). Do **not** add a README. Click *Create repository*.

4. [ ] Back in PowerShell (replace `OWNER` with your GitHub user name):

   ```powershell
   git remote add origin https://github.com/OWNER/quickz.git
   git push -u origin main
   ```

   A browser window may ask you to sign in to GitHub the first time.

5. [ ] **Private repo only: make a read-only key for the server.**
   GitHub -> your photo -> **Settings** -> **Developer settings** -> **Personal access tokens** -> **Fine-grained tokens** ->
   **Generate new token**. Name `quickz-server`; *Expiration* **1 year**; *Repository access* **Only select repositories** -> `quickz`;
   *Permissions* -> **Repository permissions** -> **Contents: Read-only**. Click **Generate** and copy the token (it starts with `github_pat_`).
   Your **RepoUrl** is then:

   ```
   https://x-access-token:github_pat_XXXXXXXX@github.com/OWNER/quickz.git
   ```

   Public repo? Your RepoUrl is simply `https://github.com/OWNER/quickz.git`.

- [ ] Write down your **RepoUrl**. (Treat the token like a password. When it expires, make a new one before you next update the server.)

---

## Part 2. Get your AWS account ready (10 minutes)

1. [ ] Sign in to **console.aws.amazon.com**. At the top right, open the **region** menu and choose your region (for example **Europe (London) eu-west-2**). Stay in this region for everything below.
2. [ ] **Set a spending alarm first.** Search **Budgets** -> **Create budget** -> **Use a template** -> **Zero spend budget** (or **Monthly cost budget** with $10) -> enter your email -> **Create**.
3. [ ] Check you have a **default VPC**: search **VPC** -> *Your VPCs*. One row should say **Default VPC: Yes**. If not: *Actions* -> **Create default VPC**.
4. [ ] Make a **setup token** (a one-time password for creating your Super Admin). In PowerShell:

   ```powershell
   [guid]::NewGuid().ToString("N")
   ```

   Copy the result. Write down: **SetupToken**.
5. [ ] **Optional hardening (2 minutes, recommended):** let the server accept visitors **only from CloudFront**.
   VPC console -> **Managed prefix lists** (left menu) -> in the filter choose *AWS-managed*, and find
   **`com.amazonaws.global.cloudfront.origin-facing`**. Copy its **Prefix list ID** (it looks like `pl-3b927c52`; yours differs by region).
   Write down: **CloudFrontPrefixListId**. (Skip this and the server is still protected by a secret header; see "Good to know".)

> **Brand-new AWS account?** Free-plan accounts may only launch certain instance types; `t4g.micro` is the safe choice and is what this guide uses.

---

## Part 3. Create the stack (the main step)

A *stack* is everything in one go: the server, its permissions, the fixed IP, the bucket and CloudFront.

1. [ ] In the console search bar type **CloudFormation** and open it. Check the region (top right) is still correct.
2. [ ] Click **Create stack** -> **With new resources (standard)**.
3. [ ] *Prepare template*: **Choose an existing template**. *Specify template*: **Upload a template file** -> **Choose file** -> pick

   `D:\Personal\DM-Association\UK\Quickz\deploy\aws\quickz-cloudfront.yaml`

   (this is the CloudFront-only template; the other file `quickz-ec2.yaml` is for setups with a domain name). Click **Next**.
4. [ ] **Stack name:** `quickz`. Fill the parameters like this (anything not listed: leave as is):

   | Parameter | What to enter |
   | --- | --- |
   | **VpcId** | the default VPC |
   | **SubnetId** | any subnet **that belongs to that VPC** (if unsure: VPC console -> *Subnets*, and match the *VPC* column to the default VPC's ID) |
   | **InstanceType** | `t4g.micro` |
   | **RepoUrl** | your RepoUrl from Part 1 |
   | **RepoBranch** | `main` |
   | **SetupToken** | the token from Part 2 |
   | **CloudFrontPrefixListId** | the `pl-...` ID from Part 2 step 5, or leave empty |
   | **DiskGb** | `8` |
   | **ImageId** | leave the default |

   Click **Next**.
5. [ ] *Configure stack options*: leave everything as it is. Click **Next**.
6. [ ] *Review*: scroll to the bottom. **Tick the box** "I acknowledge that AWS CloudFormation might create IAM resources." (The template creates the server's permission role; without the tick, *Submit* fails.) Click **Submit**.

### Wait for it (about 20 minutes)

- [ ] Stay on the stack page. Open the **Events** tab and press the refresh button now and then.
  **CloudFront** is the slow part (10-15 minutes); the server is created after it, because it needs to know the CloudFront address.
- [ ] The top of the page changes to **CREATE_COMPLETE** (green) when done.

**If it says `ROLLBACK_COMPLETE` or `CREATE_FAILED`:** open **Events**, find the **first** red line and read *Status reason*:

| Message contains | What to do |
| --- | --- |
| "account must be verified before you can add new CloudFront resources" | Happens on very new accounts. Open a support case with AWS asking them to verify the account for CloudFront, then create the stack again. |
| "capabilit" / "IAM resources" | You forgot the tick in step 6. Create the stack again and tick it. |
| "Unsupported" / "instance type ... not supported in AZ" | Pick another **SubnetId** (a different zone) and retry. |
| "AccessDenied" | Your AWS user lacks permission. Use an admin user. |
| "prefix list" / "InvalidPrefixListId" | The pl-... ID is wrong or from another region. Re-copy it, or leave the field empty. |

Then **Delete** the failed stack, fix the cause and **Create stack** again.

---

## Part 4. Read the results

1. [ ] Open the stack's **Outputs** tab and note:

   | Output | What it is |
   | --- | --- |
   | **SiteUrl** | your one public address, `https://dxxxxxxxxxxxx.cloudfront.net` |
   | **HostConsole** | `SiteUrl` + `/host`: where you sign in |
   | **InstanceId** | the server's ID (`i-0123...`) |
   | **Bucket** | the bucket holding pictures and backups |
   | **OpenAShell** | a command to open a shell without SSH (the console does the same, see below) |

2. [ ] **Wait about 5 more minutes.** The server is installing Docker and building DiQuizBuzz. Until then CloudFront shows
   *"502 / 504: The request could not be satisfied"*. That is normal; refresh in a few minutes.

### Optional: watch it finish

1. Search **EC2** -> **Instances** -> tick **quickz**.
2. **Connect** (top right) -> tab **Session Manager** -> **Connect**. (If it says the agent is not online yet, wait two minutes and retry.)
3. In the black window:

   ```
   sudo tail -n 30 /var/log/quickz-setup.log
   ```

   When the last line reads `DiQuizBuzz is up behind CloudFront: https://d....cloudfront.net/host   setup token: ...` it is ready.

---

## Part 5. First sign-in

1. [ ] Open **HostConsole** in your browser (padlock = good).
2. [ ] The page says *Welcome to DiQuizBuzz*. Enter the **setup token** (SetupToken from Part 2), your name, a **username** and a **password** of at least 10 characters. Click **Create Super Admin account**.
3. [ ] Sign in. The setup token is now dead.
4. [ ] On the dashboard click **Showcase quiz**, then **Host this quiz**, then **Open lobby**. Choose a colour theme and start. This is the quickest tour of everything.

---

## Part 6. Check that pictures come through CloudFront and S3

The bundled showcase pictures are served by the server itself, so test with a picture **you upload**:

1. [ ] Create a new quiz -> **Add question** -> **Single choice**. Add any picture and two answers. (It autosaves.)
2. [ ] Host it: **Host this quiz** -> **Open lobby** -> **Open projector**, and join from a phone with the QR code.
3. [ ] In the projector tab press **F12** -> **Network** tab, tick **Disable cache**, start the question and click the `/uploads/...` request.
   In the **Response headers** you want to see **`x-cache: Miss from cloudfront`** (first time) and then **`Hit from cloudfront`**, and
   **`server: AmazonS3`**. (For the first few seconds after an upload the picture may come from the server instead: the copy to S3
   happens in the background. Try again after 10 seconds.)

---

## Part 7. A dress rehearsal with a crowd (do not skip this)

This also proves that the **live game works through CloudFront** (WebSockets); I have tested every other part but not this
on a real AWS account, so this step is your confirmation.

On **your own computer** (not the server), with the lobby open in the host console and the **game code** showing:

```powershell
cd D:\Personal\DM-Association\UK\Quickz
.\.venv\Scripts\python.exe tools\crowd.py --url https://dxxxxxxxxxxxx.cloudfront.net --code 12345 --players 300
```

Use your own SiteUrl and game code. Wait for "300/300 players connected", press **Start quiz** in the host console and play a few
questions; the bots answer by themselves. If the console stays quick and nothing errors, you are ready for 300 real people.

---

## Part 8. Event-day routine

**Before the event (15 minutes ahead)**

1. [ ] EC2 -> **Instances** -> tick **quickz** -> **Instance state** -> **Start instance**.
2. [ ] Wait about **3 minutes**, then open your SiteUrl/host and sign in. **The address never changes**, so there is nothing to update.
3. [ ] Open the lobby, show the QR on the projector, and use **Add 10 test bots** once to check everything responds.

**After the event**

1. [ ] Download your results from the results page.
2. [ ] EC2 -> **Instances** -> tick **quickz** -> **Instance state** -> **Stop instance**. A backup is taken automatically as it stops.
3. [ ] Done. While stopped, visitors see CloudFront's "could not be satisfied" page; your quizzes, results and pictures are all kept.
   Cost while stopped: about $4.30 a month (the fixed IP and the disk).

---

## Part 9. Updating DiQuizBuzz later

1. [ ] On your computer: `git add .`, `git commit -m "what changed"`, `git push`.
2. [ ] Start the server (if stopped), open a **Session Manager** shell (as in Part 4) and run:

   ```
   sudo bash /opt/quickz/deploy/aws/update.sh
   ```

   It takes a backup, pulls your new code, rebuilds, and restarts (about 2 minutes; players reconnect by themselves).

---

## Part 10. Taking everything away

Only if you are finished with it for good:

1. [ ] CloudFormation -> select **quickz** -> **Delete**. (CloudFront takes ~10 minutes to remove; the fixed IP is released.)
2. [ ] The **bucket is kept on purpose** (your pictures and backups). To remove it: S3 -> select it -> **Empty** -> then **Delete**.

---

## Good to know

- **The hop between CloudFront and your server is plain HTTP.** The visitor's connection is always HTTPS, but CloudFront talks to
  the server over HTTP on AWS's network, because a server's own AWS name cannot carry a certificate. That includes the host's
  sign-in. Two protections limit it: the server refuses every request that lacks a secret header only CloudFront adds, and (if you
  filled in `CloudFrontPrefixListId`) the firewall only lets CloudFront's addresses in. If you ever need end-to-end encryption, use
  the other template (`quickz-ec2.yaml`) with a domain name.
- **Players do not need the venue Wi-Fi.** The site is public, so phones can use mobile data.
- **The picture setup:** pictures go to S3 and are delivered by CloudFront from `/uploads/...`; if a picture is not yet copied to S3
  CloudFront fetches it from the server, so there is never a broken image while you edit.

## Troubleshooting

| Problem | Try this |
| --- | --- |
| "502 / 504: The request could not be satisfied" | The server is stopped, still starting (give it 3-5 minutes), or crashed. Shell: `cd /opt/quickz/deploy/aws && sudo docker compose ps` and `sudo docker compose logs --tail 50 quickz`. |
| Opening the server's own AWS address shows "only answers through its CloudFront address" | Correct: that is the secret-header guard working. Use the SiteUrl. |
| The page loads but the live game never connects (players stuck on "Connecting") | WebSockets are failing through CloudFront. Check the rehearsal (Part 7) output and `sudo docker compose logs quickz`; send me the lines. |
| Nothing after 25 minutes at first launch | `sudo cat /var/log/quickz-setup.log`. The usual causes are a wrong or expired **RepoUrl**/token. Fix the value, **delete the stack and create it again** (Part 10 step 1, then Part 3). |
| Pictures do not show `cloudfront` / `AmazonS3` headers | Wait 10 seconds after upload. If still not: `sudo docker compose logs --tail 50 quickz` and look for "IAM role"; check the instance's *Metadata options* show **IMDSv2 required** and **hop limit 2**. Pictures still work meanwhile (the server delivers them). |
| Out of memory with a big game | Stop the instance -> *Actions* -> *Instance settings* -> **Change instance type** -> `t4g.small` -> start it again. |
| "Forgot my Super Admin password" | Shell: `sudo docker compose -f /opt/quickz/deploy/aws/docker-compose.cloudfront.yml exec quickz python -m quickz reset-password USERNAME` |
| Costs more than expected | Billing -> **Bills** and **Cost Explorer**. Usual causes: the server left running between events; a second stack left over. |

---

## One-page cheat sheet

| I want to... | Do this |
| --- | --- |
| Start for an event | EC2 -> Instances -> **quickz** -> Instance state -> **Start**; wait 3 min |
| Stop after an event | same menu -> **Stop** (backup is automatic) |
| Open the host console | stack **Outputs -> HostConsole** (`https://dxxxx.cloudfront.net/host`; it never changes) |
| Get a server shell | EC2 -> **quickz** -> Connect -> **Session Manager** |
| Update the app | `git push`, then shell: `sudo bash /opt/quickz/deploy/aws/update.sh` |
| Make a backup now | shell: `sudo /opt/quickz/deploy/aws/backup.sh` |
| Delete everything | CloudFormation -> **quickz** -> Delete (then empty/delete the bucket) |
