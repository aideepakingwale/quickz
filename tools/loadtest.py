"""Load test: N simulated phones play a real game over real WebSockets.

    python tools/loadtest.py --players 100
    python tools/loadtest.py --players 250 --storm

It starts its own Quickz server (temporary database), joins N players, runs a few questions and reports
  * how long each broadcast (question / reveal) takes to reach EVERY player (p50 / p95 / max),
  * answer-to-acknowledgement latency,
  * server CPU time and peak memory,
  * (--storm) how long N simultaneous reconnects take.
Dev tool: needs `pip install psutil httpx websockets` (already in the dev venv).
"""

import argparse
import asyncio
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
QUIZ = """# Load test
Q: One?
- A
- B*
- C
- D
time: 20
Q: Two? Pick all
- A*
- B*
- C
time: 20
Q: Three?
- A
- B
- C*
time: 20
Q: Four?
- A*
- B
time: 20
"""


def pct(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))]


def fmt(values, unit="ms"):
    if not values:
        return "n/a"
    return f"p50 {pct(values, 50):6.0f}  p95 {pct(values, 95):6.0f}  max {max(values):6.0f} {unit}  (n={len(values)})"


class Player:
    def __init__(self, idx, code, base, ws_base):
        self.idx, self.code, self.base, self.ws_base = idx, code, base, ws_base
        self.token = None
        self.ws = None
        self.seen: dict[str, float] = {}          # "question:3" / "reveal:3" -> first time seen
        self.answered_at: float | None = None
        self.ack: dict[int, float] = {}
        self.task = None
        self.errors = 0

    async def join(self, client, sem):
        async with sem:
            t = time.perf_counter()
            r = await client.post("/api/join", json={"code": self.code, "nickname": f"Load{self.idx:03d}"})
            r.raise_for_status()
            self.token = r.json()["token"]
            return (time.perf_counter() - t) * 1000

    async def connect(self):
        t = time.perf_counter()
        self.ws = await connect(f"{self.ws_base}/ws/player/{self.code}?token={self.token}", max_size=2 ** 20, ping_interval=None)
        self.task = asyncio.create_task(self.reader())
        return (time.perf_counter() - t) * 1000

    async def reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if m.get("t") != "state":
                    continue
                v, now = m["v"], time.perf_counter()
                q = v.get("q")
                if v["phase"] == "question" and q:
                    self.seen.setdefault(f"question:{q['index']}", now)
                    if v.get("answered") and self.answered_at and q["index"] not in self.ack:
                        self.ack[q["index"]] = (now - self.answered_at) * 1000
                elif v["phase"] == "reveal" and q:
                    self.seen.setdefault(f"reveal:{q['index']}", now)
                    if self.answered_at and q["index"] not in self.ack and v.get("result", {}).get("answered"):
                        self.ack[q["index"]] = (now - self.answered_at) * 1000
                elif v["phase"] == "finished":
                    self.seen.setdefault("finished", now)
        except Exception:
            self.errors += 1

    async def answer(self, q):
        await asyncio.sleep(random.uniform(0.05, 1.5))     # people don't all tap at the same instant
        ids = [o["id"] for o in q["options"]]
        self.answered_at = time.perf_counter()
        await self.ws.send(json.dumps({"t": "answer", "choice": random.sample(ids, 1 if q["type"] == "mcq" else 2)}))


async def wait_for(players, key, timeout=20):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if all(key in p.seen for p in players):
            return True
        await asyncio.sleep(0.02)
    return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=100)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--storm", action="store_true", help="drop and re-open every connection at once")
    args = ap.parse_args()
    N = args.players

    tmp = Path(tempfile.mkdtemp(prefix="quickz_load_"))
    env = dict(os.environ, QUICKZ_PORT=str(args.port), QUICKZ_DATA=str(tmp), QUICKZ_HOST="127.0.0.1",
               QUICKZ_PUBLIC_URL=f"http://127.0.0.1:{args.port}", QUICKZ_SETUP_TOKEN="loadtest-token", PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen([sys.executable, "-m", "quickz"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base, ws_base = f"http://127.0.0.1:{args.port}", f"ws://127.0.0.1:{args.port}"
    try:
        async with httpx.AsyncClient(base_url=base, headers={"X-Quickz": "1"}, timeout=30) as client:
            for _ in range(80):
                try:
                    if (await client.get("/healthz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            else:
                raise RuntimeError("server did not start: " + srv.stderr.read().decode())

            # the process tree: on Windows the venv launcher spawns the real interpreter as a child
            root = psutil.Process(srv.pid)
            procs = [root] + root.children(recursive=True)

            def cpu_seconds():
                return sum(sum(p.cpu_times()[:2]) for p in procs if p.is_running())

            def rss_mb():
                return sum(p.memory_info().rss for p in procs if p.is_running()) / 1e6

            idle_rss = rss_mb()
            r = await client.post("/api/auth/setup", json={"token": "loadtest-token", "username": "loader", "display_name": "Loader", "password": "load test password 123"})
            r.raise_for_status()
            quiz = (await client.post("/api/quizzes/import", json={"text": QUIZ})).json()["id"]
            game = (await client.post("/api/games", json={"quiz_id": quiz, "settings": {"auto_reveal": False}})).json()
            code = game["code"]
            cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            host = await connect(f"{ws_base}/ws/host/{code}", additional_headers={"Cookie": cookie, "Origin": base}, ping_interval=None)
            await host.recv()
            screen = await connect(f"{ws_base}/ws/screen/{code}", ping_interval=None)
            host_views = {}

            async def host_reader():
                async for raw in host:
                    m = json.loads(raw)
                    if m.get("t") == "state":
                        host_views["last"] = m["v"]

            asyncio.create_task(host_reader())

            players = [Player(i, code, base, ws_base) for i in range(N)]
            cpu0 = cpu_seconds()
            t0 = time.perf_counter()
            sem = asyncio.Semaphore(40)
            join_ms = await asyncio.gather(*[p.join(client, sem) for p in players])
            conn_ms = []
            for chunk in range(0, N, 40):
                conn_ms += await asyncio.gather(*[p.connect() for p in players[chunk:chunk + 40]])
            join_total = time.perf_counter() - t0
            await asyncio.sleep(1.5)  # lobby push settles
            peak = rss_mb()

            qlat, rlat, acks = [], [], []
            for qi in range(4):
                for p in players:
                    p.answered_at = None
                t = time.perf_counter()
                await host.send(json.dumps({"t": "next"}))
                ok = await wait_for(players, f"question:{qi}")
                if not ok:
                    print(f"  !! question {qi}: not every player received it in time")
                qlat += [(p.seen.get(f"question:{qi}", t + 20) - t) * 1000 for p in players]
                q = host_views["last"]["q"]
                await asyncio.gather(*[p.answer(q) for p in players])
                await asyncio.sleep(1.5)
                t = time.perf_counter()
                await host.send(json.dumps({"t": "end_question"}))
                await wait_for(players, f"reveal:{qi}")
                rlat += [(p.seen.get(f"reveal:{qi}", t + 20) - t) * 1000 for p in players]
                acks += [p.ack[qi] for p in players if qi in p.ack]
                peak = max(peak, rss_mb())
            await host.send(json.dumps({"t": "finish"}))
            await wait_for(players, "finished")
            cpu_total = cpu_seconds() - cpu0
            wall = time.perf_counter() - t0

            storm = None
            if args.storm:
                for p in players:
                    await p.ws.close()
                await asyncio.sleep(0.5)
                t = time.perf_counter()
                for p in players:
                    p.seen.clear()
                for chunk in range(0, N, 50):
                    await asyncio.gather(*[p.connect() for p in players[chunk:chunk + 50]])
                got = await wait_for(players, "finished", timeout=15)
                storm = (time.perf_counter() - t, got)

            print(f"\n=== Quickz load test: {N} players, {len(players)} WebSockets + host + projector ===")
            print(f"join (HTTP)         {fmt(join_ms)}")
            print(f"WebSocket connect   {fmt(conn_ms)}")
            print(f"all {N} joined+connected in {join_total:.1f}s")
            print(f"question broadcast  {fmt(qlat)}   <- host presses Next until that phone shows it")
            print(f"answer -> ack       {fmt(acks)}")
            print(f"reveal broadcast    {fmt(rlat)}")
            print(f"server CPU time     {cpu_total:.1f} s over {wall:.1f} s wall  ({100 * cpu_total / wall:.0f}% of one core on average)")
            print(f"server memory       {idle_rss:.0f} MB idle -> {peak:.0f} MB peak")
            print(f"dropped sockets     {sum(p.errors for p in players)}")
            if storm:
                print(f"reconnect storm     all {N} back in {storm[0]:.1f}s ({'ok' if storm[1] else 'INCOMPLETE'})")
            worst = pct(qlat, 100)
            print("verdict             " + ("PASS" if worst < 1500 and sum(p.errors for p in players) == 0 else "CHECK"))
            for p in players:
                if p.ws:
                    await p.ws.close()
            await host.close()
            await screen.close()
    finally:
        srv.terminate()
        try:
            srv.wait(5)
        except Exception:
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
