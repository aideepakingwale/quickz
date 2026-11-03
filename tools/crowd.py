"""Fill a LIVE game with simulated players, to rehearse a big crowd against your real deployment.

You run the game yourself from the host console (so no host password is needed here):

    pip install httpx websockets
    python tools/crowd.py --url https://your-app.onrender.com --code ABC123 --players 100

The bots join with names like "Bot001", open a WebSocket each, and answer every question (every type) after a random 0.3-8 s
(buzzer questions: a random subset buzz). They stay until the game finishes (or Ctrl+C), then print how fast they
joined and connected and how many received each question, which tells you whether the free tier is coping.
Use --prefix to change the names, --rate to throttle how fast they join (players/second).
"""

import argparse
import asyncio
import json
import random
import ssl
import time
from urllib.parse import urlparse

import httpx
from websockets.asyncio.client import connect


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))] if values else float("nan")


class Bot:
    def __init__(self, idx, args, base, ws_base):
        self.idx, self.args, self.base, self.ws_base = idx, args, base, ws_base
        self.name = f"{args.prefix}{idx + 1:03d}"
        self.token = None
        self.seen: dict[int, float] = {}       # question index -> when its state arrived
        self.sent: set[int] = set()
        self.join_ms = self.connect_ms = None
        self.errors = 0
        self.finished = asyncio.Event()

    async def join(self, client):
        t = time.perf_counter()
        r = await client.post("/api/join", json={"code": self.args.code, "nickname": self.name})
        if r.status_code != 200:
            raise RuntimeError(f"{self.name}: join failed HTTP {r.status_code} {r.text[:120]}")
        self.token = r.json()["token"]
        self.join_ms = (time.perf_counter() - t) * 1000

    async def run(self):
        t = time.perf_counter()
        extra = {"ssl": ssl._create_unverified_context()} if self.args.insecure and self.ws_base.startswith("wss") else {}
        async with connect(f"{self.ws_base}/ws/player/{self.args.code}?token={self.token}", max_size=2 ** 20, open_timeout=30, **extra) as ws:
            self.connect_ms = (time.perf_counter() - t) * 1000
            async for raw in ws:
                m = json.loads(raw)
                if m.get("t") != "state":
                    continue
                v = m["v"]
                if v["phase"] == "finished":
                    self.finished.set()
                    return
                q = v.get("q")
                if v["phase"] == "question" and q and not v.get("answered") and q["index"] not in self.sent \
                        and not q.get("awaiting_media") and not q.get("locked"):
                    self.seen.setdefault(q["index"], time.perf_counter())
                    self.sent.add(q["index"])
                    asyncio.create_task(self.answer(ws, q))
                elif q:
                    self.seen.setdefault(q["index"], time.perf_counter())

    @staticmethod
    def guess(q):
        """A plausible answer from what a phone can see (no secrets), for every question type."""
        t, ids = q["type"], [o["id"] for o in q.get("options", [])]
        if t in ("mcq", "poll"):
            return [random.choice(ids)] if ids else None
        if t == "multi":
            return random.sample(ids, random.randint(1, min(2, len(ids)))) if ids else None
        if t == "text":
            return [random.choice(["Paris", "Mars", "no idea", "Everest", "42", "Shakespeare"])]
        if t == "number":
            spec = q.get("number") or {}
            lo, hi = spec.get("min"), spec.get("max")
            return [round(random.uniform(lo, hi)) if lo is not None and hi is not None else random.randint(0, 2000)]
        if t == "order":
            random.shuffle(ids)
            return ids
        if t == "match":
            rights = [o["id"] for o in q.get("targets", [])]
            random.shuffle(rights)
            return [[a, b] for a, b in zip(ids, rights)]
        if t == "pin":
            return [random.uniform(5, 95), random.uniform(5, 95)]
        if t == "jigsaw":
            n = q["jigsaw"]["rows"] * q["jigsaw"]["cols"]
            pieces = list(range(n))                          # solved is 0..n-1, so some bots "finish" the puzzle
            if random.random() < 0.6:
                random.shuffle(pieces)
            return pieces
        return None

    async def answer(self, ws, q):
        await asyncio.sleep(random.uniform(0.3, self.args.think))
        try:
            if q["type"] == "buzzer":
                if random.random() < 0.15:
                    await ws.send(json.dumps({"t": "buzz"}))
                return
            choice = self.guess(q)
            if choice is not None:
                await ws.send(json.dumps({"t": "answer", "choice": choice}))
        except Exception:
            self.errors += 1


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="https://your-app.onrender.com")
    ap.add_argument("--code", required=True, help="the game code shown on the host console")
    ap.add_argument("--players", type=int, default=100)
    ap.add_argument("--prefix", default="Bot")
    ap.add_argument("--rate", type=float, default=20, help="joins per second (default 20)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the TLS certificate (a local test server)")
    ap.add_argument("--think", type=float, default=8, help="max seconds a bot takes to answer (default 8)")
    args = ap.parse_args()
    args.code = args.code.strip().upper()

    base = args.url.rstrip("/")
    ws_base = ("wss" if urlparse(base).scheme == "https" else "ws") + "://" + urlparse(base).netloc
    bots = [Bot(i, args, base, ws_base) for i in range(args.players)]

    async with httpx.AsyncClient(base_url=base, headers={"X-Quickz": "1"}, timeout=60, verify=not args.insecure) as client:
        r = await client.get("/healthz")           # also wakes a sleeping free instance
        print(f"server answered /healthz with HTTP {r.status_code}")
        t0 = time.perf_counter()
        sem = asyncio.Semaphore(max(1, int(args.rate)))

        async def start(b):
            async with sem:
                await b.join(client)
                await asyncio.sleep(1 / max(args.rate, 1))
            await b.run()

        tasks = [asyncio.create_task(start(b)) for b in bots]
        while not all(b.connect_ms or t.done() for b, t in zip(bots, tasks)):
            await asyncio.sleep(0.5)
        up = sum(1 for b in bots if b.connect_ms)
        print(f"{up}/{args.players} players connected in {time.perf_counter() - t0:.1f}s - now start the game from the host console.")
        print("Waiting for the game to finish (Ctrl+C to stop early)...")
        try:
            await asyncio.gather(*[b.finished.wait() for b in bots if b.connect_ms])
        except asyncio.CancelledError:
            pass
        for t in tasks:
            t.cancel()

    joins = [b.join_ms for b in bots if b.join_ms]
    conns = [b.connect_ms for b in bots if b.connect_ms]
    print(f"\n=== {len(conns)}/{args.players} connected, {sum(b.errors for b in bots)} send errors ===")
    print(f"join HTTP         p50 {pct(joins, 50):6.0f}  p95 {pct(joins, 95):6.0f}  max {max(joins, default=0):6.0f} ms")
    print(f"WebSocket connect p50 {pct(conns, 50):6.0f}  p95 {pct(conns, 95):6.0f}  max {max(conns, default=0):6.0f} ms")
    seen_q = sorted({i for b in bots for i in b.seen})
    for i in seen_q:
        got = sum(1 for b in bots if i in b.seen)
        print(f"question {i + 1:>2}: reached {got}/{len(conns)} bots")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
