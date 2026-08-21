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
EARLY_MIN_MC = float(os.getenv("EARLY_MIN_MC", "5000"))
EARLY_MAX_MC = float(os.getenv("EARLY_MAX_MC", "75000"))
EARLY_MIN_LIQ = float(os.getenv("EARLY_MIN_LIQ", "1500"))
EARLY_MIN_VOL_5M = float(os.getenv("EARLY_MIN_VOL_5M", "800"))
EARLY_MIN_BUYS_5M = int(os.getenv("EARLY_MIN_BUYS_5M", "8"))
EARLY_MAX_AGE_HOURS = float(os.getenv("EARLY_MAX_AGE_HOURS", "6"))
EARLY_MIN_SCORE = int(os.getenv("EARLY_MIN_SCORE", "58"))

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

def early_score_pair(p):
    liq = num((p.get("liquidity") or {}).get("usd"))
    mc = num(p.get("marketCap") or p.get("fdv"))

    volume = p.get("volume") or {}
    vol5m = num(volume.get("m5"))
    vol1h = num(volume.get("h1"))

    txns = p.get("txns") or {}
    tx5m = txns.get("m5") or {}
    buys5m = int(tx5m.get("buys") or 0)
    sells5m = int(tx5m.get("sells") or 0)

    pc = p.get("priceChange") or {}
    ch5 = num(pc.get("m5"))
    ch1 = num(pc.get("h1"))
    age = age_hours(p)

    score = 0
    reasons = []
    flags = []

    volume_accel = (vol5m * 12 / vol1h) if vol1h > 0 else 0
    liq_ratio = (liq / mc) if mc > 0 else 0

    if EARLY_MIN_MC <= mc <= EARLY_MAX_MC:
        score += 18
        reasons.append("early market cap")

    if age <= 1:
        score += 12
        reasons.append("very fresh")
    elif age <= EARLY_MAX_AGE_HOURS:
        score += 7
        reasons.append("fresh")

    if liq >= EARLY_MIN_LIQ:
        score += 10
        reasons.append("usable early liquidity")

    if 0.08 <= liq_ratio <= 0.60:
        score += 8
        reasons.append("healthy liquidity ratio")

    if vol5m >= EARLY_MIN_VOL_5M:
        score += 12
        reasons.append("5m volume waking up")

    if volume_accel >= 2.0:
        score += 15
        reasons.append("strong volume acceleration")
    elif volume_accel >= 1.3:
        score += 9
        reasons.append("volume accelerating")

    if buys5m >= EARLY_MIN_BUYS_5M:
        score += 8
        reasons.append("active 5m buyers")

    if buys5m > sells5m * 1.35 and buys5m >= 8:
        score += 12
        reasons.append("5m buy pressure")
    elif buys5m > sells5m:
        score += 5
        reasons.append("buyers leading")

    if 1 <= ch5 <= 12:
        score += 10
        reasons.append("early positive momentum")
    elif 12 < ch5 <= 22:
        score += 5
        reasons.append("momentum building")

    if ch5 < -10:
        score -= 15
        flags.append("5m trend weak")

    if ch1 < -20:
        score -= 18
        flags.append("1h trend heavily negative")
    elif ch1 < -10:
        score -= 10
        flags.append("1h trend negative")

    if ch5 > 30:
        score -= 20
        flags.append("already pumping")

    if ch1 > 100:
        score -= 15
        flags.append("1h move already extended")

    if sells5m > buys5m * 1.4 and sells5m >= 8:
        score -= 15
        flags.append("5m sellers taking control")

    if liq < EARLY_MIN_LIQ:
        score -= 20
        flags.append("very thin liquidity")

    return max(0, min(100, score)), reasons, flags


def early_qualifies(p):
    mc = num(p.get("marketCap") or p.get("fdv"))
    liq = num((p.get("liquidity") or {}).get("usd"))
    volume = p.get("volume") or {}
    vol5m = num(volume.get("m5"))
    tx5m = ((p.get("txns") or {}).get("m5") or {})
    buys5m = int(tx5m.get("buys") or 0)

    return (
        EARLY_MIN_MC <= mc <= EARLY_MAX_MC
        and liq >= EARLY_MIN_LIQ
        and vol5m >= EARLY_MIN_VOL_5M
        and buys5m >= EARLY_MIN_BUYS_5M
        and age_hours(p) <= EARLY_MAX_AGE_HOURS
    )
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

def entry_status(ch5, ch1, vol5m, vol1h, buys5m, sells5m):
    accel = (vol5m * 12 / vol1h) if vol1h > 0 else 0

    if ch5 >= 35 or ch1 >= 120:
        return "🔴 DO NOT CHASE"

    if ch5 >= 20 or ch1 >= 75:
        return "🟠 EXTENDED"

    if accel >= 1.5 and buys5m > sells5m * 1.2:
        return "🟢 EARLY"

    return "🟡 MOVING"


def signal_text(p, ca, score, reasons, flags):
    base = p.get("baseToken") or {}
    sym = base.get("symbol") or "?"

    mc = num(p.get("marketCap") or p.get("fdv"))
    liq = num((p.get("liquidity") or {}).get("usd"))

    volume = p.get("volume") or {}
    vol5m = num(volume.get("m5"))
    vol1h = num(volume.get("h1"))

    txns = p.get("txns") or {}
    tx5m = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    buys5m = int(tx5m.get("buys") or 0)
    sells5m = int(tx5m.get("sells") or 0)

    buys1h = int(tx1h.get("buys") or 0)
    sells1h = int(tx1h.get("sells") or 0)

    pc = p.get("priceChange") or {}
    ch5 = num(pc.get("m5"))
    ch1 = num(pc.get("h1"))

    age = age_hours(p)

    volume_accel = (vol5m * 12 / vol1h) if vol1h > 0 else 0

    if score >= 90:
        tier = "🚨 EXTREME ALERT"
    elif score >= 82:
        tier = "🔥 HIGH ALERT"
    else:
        tier = "⚡ WATCH"

    status = entry_status(
        ch5,
        ch1,
        vol5m,
        vol1h,
        buys5m,
        sells5m,
    )

    clean_flags = ", ".join(flags) if flags else "No basic market-data flags"
    why = ", ".join(reasons[:6]) if reasons else "Momentum conditions met"

    embed = {
        "title": f"{tier} — ${sym}",
        "description": f"**{status}**\nScore: **{score}/100**",
        "color": (
            15158332 if score >= 90
            else 16753920 if score >= 82
            else 5763719
        ),
        "fields": [
            {
                "name": "💰 MARKET",
                "value": (
                    f"**MC:** {fmt_money(mc)}\n"
                    f"**Liquidity:** {fmt_money(liq)}\n"
                    f"**Age:** {age:.1f}h"
                ),
                "inline": True,
            },
            {
                "name": "📊 VOLUME",
                "value": (
                    f"**5m:** {fmt_money(vol5m)}\n"
                    f"**1h:** {fmt_money(vol1h)}\n"
                    f"**Acceleration:** {volume_accel:.2f}x"
                ),
                "inline": True,
            },
            {
                "name": "🚀 MOMENTUM",
                "value": (
                    f"**5m:** {ch5:+.1f}%\n"
                    f"**1h:** {ch1:+.1f}%"
                ),
                "inline": True,
            },
            {
                "name": "🟢 BUY / SELL FLOW",
                "value": (
                    f"**5m:** {buys5m} buys / {sells5m} sells\n"
                    f"**1h:** {buys1h} buys / {sells1h} sells"
                ),
                "inline": False,
            },
            {
                "name": "🧠 WHY IT TRIGGERED",
                "value": why,
                "inline": False,
            },
            {
                "name": "⚠️ RED FLAGS",
                "value": clean_flags,
                "inline": False,
            },
            {
                "name": "📋 CONTRACT ADDRESS",
                "value": f"```{ca}```",
                "inline": False,
            },
        ],
        "footer": {
            "text": "Spidey Meme Scanner • Signal only"
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if p.get("url"):
        embed["url"] = p.get("url")

    return {
        "content": ca,
        "embeds": [embed],
    }


def discord_alert(msg):
    if not DISCORD_WEBHOOK_URL:
        return

    try:
        payload = msg if isinstance(msg, dict) else {"content": str(msg)}

        requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
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

            # Normal signal
            score, reasons, flags = score_pair(p)

            if qualifies(p) and score >= MIN_SCORE:
                ranked.append(
                    (score, ca, p, reasons, flags, "normal")
                )

            # Early signal — catches coins before the larger move
            early_score, early_reasons, early_flags = early_score_pair(p)

            if early_qualifies(p) and early_score >= EARLY_MIN_SCORE:
                ranked.append(
                    (early_score, ca, p, early_reasons, early_flags, "early")
                )

            time.sleep(0.08)

        except Exception as e:
            print("token error:", ca, e)

    ranked.sort(key=lambda x: x[0], reverse=True)

    if not ranked:
        print("No signals passed filters.")
        return

    sent = set()

    for score, ca, p, reasons, flags, signal_type in ranked[:12]:

        # Prevent the same contract being sent twice in one scan
        if ca in sent:
            continue

        sent.add(ca)

        msg = signal_text(
            p,
            ca,
            score,
            reasons,
            flags
        )

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
