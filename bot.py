#!/usr/bin/env python3
"""
Low-cap Solana meme-coin signal bot.

What it does:
- Pulls fresh/boosted Solana tokens from DEX Screener.
- Fetches pair data for each token.
- Filters for low market cap, liquidity, volume and recent activity.
- Scores momentum and basic risk.
- Prints WATCH / STRONG WATCH signals.
- Optionally sends Discord alerts.

It does NOT place trades. Paper/signal mode is intentional.
"""

import os
import time
import json
import math
import requests
from datetime import datetime, timezone

BASE = "https://api.dexscreener.com"
CHAIN = "solana"

SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "15"))
MIN_MC = float(os.getenv("MIN_MC", "20000"))
MAX_MC = float(os.getenv("MAX_MC", "150000"))
MIN_LIQ = float(os.getenv("MIN_LIQ", "8000"))
MIN_VOL_1H = float(os.getenv("MIN_VOL_1H", "5000"))
MIN_BUYS_1H = int(os.getenv("MIN_BUYS_1H", "25"))
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "24"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "70"))

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_BASE = "https://public-api.birdeye.so"



seen_alerts = {}

def get_json(url):
    r = requests.get(url, timeout=12, headers={"User-Agent": "meme-signal-bot/1.0"})
    r.raise_for_status()
    return r.json()
    
    def birdeye_get(path, params=None):

    if not BIRDEYE_API_KEY:

        return None

    headers = {

        "X-API-KEY": BIRDEYE_API_KEY,

        "x-chain": "solana",

        "accept": "application/json",

    }

    try:

        r = requests.get(

            f"{BIRDEYE_BASE}{path}",

            headers=headers,

            params=params or {},

            timeout=15,

        )

        r.raise_for_status()

        return r.json()

    except Exception as e:

        print("birdeye error:", e)

        return None

def candidate_addresses():
    out = []
    endpoints = [
        f"{BASE}/token-profiles/latest/v1",
        f"{BASE}/token-boosts/latest/v1",
        f"{BASE}/token-boosts/top/v1",
    ]
    for url in endpoints:
        try:
            data = get_json(url)
            if isinstance(data, dict):
                data = [data]
            for x in data or []:
                if str(x.get("chainId", "")).lower() == CHAIN:
                    ca = x.get("tokenAddress")
                    if ca:
                        out.append(ca)
        except Exception as e:
            print("source error:", url, e)
    return list(dict.fromkeys(out))

def token_pairs(ca):
    # Official DEX Screener token endpoint; returns all known pairs for the token.
    data = get_json(f"{BASE}/latest/dex/tokens/{ca}")
    return data.get("pairs") or []

def num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def age_hours(pair):
    created = pair.get("pairCreatedAt")
    if not created:
        return 9999
    return max(0, (time.time() * 1000 - float(created)) / 3_600_000)

def best_pair(pairs):
    sol = [p for p in pairs if str(p.get("chainId","")).lower() == CHAIN]
    if not sol:
        return None
    return max(sol, key=lambda p: num((p.get("liquidity") or {}).get("usd")))

def score_pair(p):
    liq = num((p.get("liquidity") or {}).get("usd"))
    mc = num(p.get("marketCap") or p.get("fdv"))
    vol1h = num((p.get("volume") or {}).get("h1"))
    vol5m = num((p.get("volume") or {}).get("m5"))
    tx1h = (p.get("txns") or {}).get("h1") or {}
    tx5m = (p.get("txns") or {}).get("m5") or {}
    buys1h, sells1h = int(tx1h.get("buys") or 0), int(tx1h.get("sells") or 0)
    buys5m, sells5m = int(tx5m.get("buys") or 0), int(tx5m.get("sells") or 0)
    pc = p.get("priceChange") or {}
    ch5 = num(pc.get("m5"))
    ch1 = num(pc.get("h1"))
    age = age_hours(p)

    score = 0
    reasons, flags = [], []

    if MIN_MC <= mc <= MAX_MC:
        score += 20; reasons.append("target market cap")
    if liq >= MIN_LIQ:
        score += min(20, 10 + int(math.log10(max(liq / MIN_LIQ, 1)) * 8))
        reasons.append("usable liquidity")
    if vol1h >= MIN_VOL_1H:
        score += min(18, 9 + int(math.log10(max(vol1h / MIN_VOL_1H, 1)) * 8))
        reasons.append("1h volume")
    if buys1h >= MIN_BUYS_1H:
        score += 10; reasons.append("active buyers")
    if buys1h > sells1h * 1.15 and buys1h >= 10:
        score += 10; reasons.append("buy pressure")
    if vol5m > 0 and vol1h > 0 and vol5m * 12 > vol1h * 1.2:
        score += 8; reasons.append("volume accelerating")
    if 0 < ch5 <= 25:
        score += 5; reasons.append("positive 5m momentum")
    if 0 < ch1 <= 80:
        score += 5; reasons.append("positive 1h momentum")
    if age <= MAX_AGE_HOURS:
        score += 4; reasons.append("fresh")

    # Basic risk deductions. These are heuristics, not a smart-contract audit.
    if liq < MIN_LIQ:
        score -= 25; flags.append("thin liquidity")
    if mc and liq / mc < 0.06:
        score -= 12; flags.append("low liquidity vs MC")
    if ch5 > 35:
        score -= 12; flags.append("5m move already stretched")
    if ch1 > 120:
        score -= 15; flags.append("1h move already stretched")
    if sells5m > buys5m * 1.6 and sells5m >= 8:
        score -= 12; flags.append("5m sell pressure")
    if age > MAX_AGE_HOURS:
        score -= 8; flags.append("older than target")

    return max(0, min(100, score)), reasons, flags

def qualifies(p):
    mc = num(p.get("marketCap") or p.get("fdv"))
    liq = num((p.get("liquidity") or {}).get("usd"))
    vol1h = num((p.get("volume") or {}).get("h1"))
    buys1h = int((((p.get("txns") or {}).get("h1") or {}).get("buys")) or 0)
    return (
        MIN_MC <= mc <= MAX_MC
        and liq >= MIN_LIQ
        and vol1h >= MIN_VOL_1H
        and buys1h >= MIN_BUYS_1H
        and age_hours(p) <= MAX_AGE_HOURS
    )

def fmt_money(v):
    v = num(v)
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000: return f"${v/1_000:.1f}K"
    return f"${v:.0f}"

def signal_text(p, ca, score, reasons, flags):
    base = p.get("baseToken") or {}
    sym = base.get("symbol") or "?"
    mc = num(p.get("marketCap") or p.get("fdv"))
    liq = num((p.get("liquidity") or {}).get("usd"))
    vol1h = num((p.get("volume") or {}).get("h1"))
    tx1h = (p.get("txns") or {}).get("h1") or {}
    ch5 = num((p.get("priceChange") or {}).get("m5"))
    ch1 = num((p.get("priceChange") or {}).get("h1"))
    tier = "🔥 STRONG WATCH" if score >= 82 else "👀 WATCH"
    return (
        f"{tier} ${sym} | score {score}/100\n"
        f"MC {fmt_money(mc)} | Liq {fmt_money(liq)} | 1H Vol {fmt_money(vol1h)}\n"
        f"1H buys/sells {tx1h.get('buys',0)}/{tx1h.get('sells',0)} | "
        f"5m {ch5:+.1f}% | 1h {ch1:+.1f}%\n"
        f"CA: {ca}\n"
        f"Why: {', '.join(reasons[:5]) or 'n/a'}\n"
        f"Flags: {', '.join(flags) if flags else 'none from basic market-data checks'}\n"
        f"{p.get('url','')}"
    )

def discord_alert(msg):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": msg},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        print("discord error:", e)

def should_alert(ca, score):
    now = time.time()
    prev = seen_alerts.get(ca)
    # Alert again only after 20 min, or if score improves materially.
    if prev and now - prev["time"] < 1200 and score < prev["score"] + 8:
        return False
    seen_alerts[ca] = {"time": now, "score": score}
    return True

def scan_once():
    addrs = candidate_addresses()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] candidates={len(addrs)}")
    ranked = []
    for ca in addrs[:80]:
        try:
            p = best_pair(token_pairs(ca))
            if not p:
                continue
            score, reasons, flags = score_pair(p)
            if qualifies(p) and score >= MIN_SCORE:
                ranked.append((score, ca, p, reasons, flags))
            time.sleep(0.08)
        except Exception as e:
            print("token error:", ca, e)

    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        print("No signals passed filters.")
        return

    for score, ca, p, reasons, flags in ranked[:8]:
        msg = signal_text(p, ca, score, reasons, flags)
        print("\n" + msg)
        if should_alert(ca, score):
            discord_alert(msg)

def main():
    print("Meme Coin Signal Bot")
    print(f"MC={MIN_MC:,.0f}-{MAX_MC:,.0f} | min liq={MIN_LIQ:,.0f} | scan={SCAN_SECONDS}s")
    print("Signal-only mode: NO automatic trade execution.") 
    discord_alert("✅ Spidey Bot connected to Discord")
    while True:
        try:
            scan_once()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print("scan error:", e)
        time.sleep(max(5, SCAN_SECONDS))

if __name__ == "__main__":
    main()
