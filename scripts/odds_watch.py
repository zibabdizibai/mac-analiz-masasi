"""Oran takibi: The Odds API'den maç sonucu (1X2) oranlarını çeker, açılışa göre
düşüşleri bulur ve siteye data/odds/drops.json yazar.

Çalıştırma: ODDS_API_KEY=... python3 scripts/odds_watch.py
GitHub Actions her 15 dakikada bir çalıştırır (.github/workflows/odds.yml).

Kredi tasarrufu:
- /events uç noktası ücretsizdir; hangi ligin ne zaman maçı olduğunu oradan öğreniriz.
- /odds isteği (istek başına bölge x pazar = 1 kredi) sadece maça yakınken atılır:
  ilk maça <=2 saat varsa 25 dk'da bir, <=8 saat 55 dk'da bir, <=48 saat 6 saatte bir.
- Günlük tavan = kalan kredi / ay sonuna kalan gün. Tavan dolunca sadece
  maça <=2 saat kalan ligler kontrol edilir; kalan kredi 5'in altına düşünce durur.
"""
import json, os, statistics, sys, time, urllib.error, urllib.parse, urllib.request
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

LEAGUES = {
    "soccer_turkey_super_league": "Trendyol Süper Lig",
    "soccer_epl": "Premier Lig",
    "soccer_netherlands_eredivisie": "Eredivisie",
}
REGIONS = "eu"
MARKET = "h2h"
SHARP = {"pinnacle"}
EXCHANGES = {"betfair_ex_eu", "matchbook"}
DROP_PTS = 0.03      # ima edilen ihtimalde +3 puan = "düştü"
OUTLIER = 1.06       # konsensüsün %6 üstü = uç değer
HORIZON_H = 48

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "odds"
STATE_P = OUT / "state.json"
DROPS_P = OUT / "drops.json"
API = "https://api.the-odds-api.com/v4/sports"


def now():
    return time.time()


def iso_to_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def get(url, params, fetch=None):
    """Returns (json, headers). `fetch` lets tests inject fake responses."""
    q = urllib.parse.urlencode(params)
    if fetch:
        return fetch(url, params)
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "mac-analiz-masasi"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()), {k.lower(): v for k, v in r.headers.items()}


def load_state():
    if STATE_P.exists():
        return json.loads(STATE_P.read_text(encoding="utf-8"))
    return {"lastFetch": {}, "events": {}, "budget": {}, "usage": {}}


def daily_cap(state, t):
    rem = state["budget"].get("remaining")
    rem = 500 if rem is None else rem
    d = datetime.fromtimestamp(t, timezone.utc)
    days_left = monthrange(d.year, d.month)[1] - d.day + 1
    return max(4, int(rem // max(days_left, 1))), rem


def interval_for(hours_to_next):
    if hours_to_next <= 2:
        return 25 * 60
    if hours_to_next <= 8:
        return 55 * 60
    if hours_to_next <= HORIZON_H:
        return 6 * 3600
    return None


def implied(p):
    return 1.0 / p if p and p > 1 else None


def update_event(ev_state, ev, t):
    ev_state.update({"home": ev["home_team"], "away": ev["away_team"], "commence": ev["commence_time"]})
    books = ev_state.setdefault("books", {})
    for bk in ev.get("bookmakers", []):
        mk = next((m for m in bk.get("markets", []) if m.get("key") == MARKET), None)
        if not mk:
            continue
        b = books.setdefault(bk["key"], {"title": bk.get("title", bk["key"]), "o": {}})
        b["title"] = bk.get("title", bk["key"])
        for oc in mk.get("outcomes", []):
            name, price = oc["name"], float(oc["price"])
            s = b["o"].get(name)
            if not s:
                b["o"][name] = {"open": price, "openAt": t, "prev": price, "last": price, "lastAt": t, "dropAt": None}
                continue
            if price != s["last"]:
                s["prev"], s["last"] = s["last"], price
            s["lastAt"] = t
            if s["dropAt"] is None and implied(price) - implied(s["open"]) >= DROP_PTS:
                s["dropAt"] = t
            elif s["dropAt"] is not None and implied(price) - implied(s["open"]) < DROP_PTS:
                s["dropAt"] = None
    # konsensüs geçmişi (ima edilen ihtimallerin medyanı -> oran)
    cons = consensus(books, "last")
    if cons:
        hist = ev_state.setdefault("hist", [])
        if not hist or hist[-1][1] != cons:
            hist.append([t, cons])
        del hist[:-48]
        ev_state.setdefault("consOpen", cons)


def consensus(books, field):
    by = {}
    for b in books.values():
        for name, s in b["o"].items():
            ip = implied(s[field])
            if ip:
                by.setdefault(name, []).append(ip)
    return {n: round(1 / statistics.median(v), 3) for n, v in by.items() if v}


def label(name, ev):
    return "1" if name == ev["home"] else "2" if name == ev["away"] else "X" if name.lower() == "draw" else name


def summarize(eid, ev, league):
    books = ev.get("books", {})
    cons_now = consensus(books, "last")
    cons_open = consensus(books, "open")
    outcomes = []
    for name in cons_now:
        rows = [(k, b, b["o"][name]) for k, b in books.items() if name in b["o"]]
        n = len(rows)
        dropped = [(k, b, s) for k, b, s in rows if implied(s["last"]) - implied(s["open"]) >= DROP_PTS]
        share = len(dropped) / n if n else 0
        sharp_drop = any(k in SHARP | EXCHANGES for k, _, _ in dropped)
        best = max(rows, key=lambda r: r[2]["last"]) if rows else None
        first = min((r for r in dropped if r[2]["dropAt"]), key=lambda r: r[2]["dropAt"], default=None)
        pin = next((s for k, _, s in rows if k == "pinnacle"), None)
        level = None
        if dropped:
            level = "güçlü" if share >= 0.5 and sharp_drop else "orta" if share >= 0.25 or sharp_drop else "zayıf"
        outcomes.append({
            "pick": label(name, ev), "name": name,
            "consOpen": cons_open.get(name), "consNow": cons_now[name],
            "deltaPts": round(100 * (implied(cons_now[name]) - implied(cons_open.get(name) or cons_now[name])), 1),
            "books": n, "dropped": len(dropped), "level": level, "sharpDropped": sharp_drop,
            "pinnacle": {"open": pin["open"], "now": pin["last"]} if pin else None,
            "best": {"price": best[2]["last"], "book": best[1]["title"]} if best else None,
            "firstMover": first[1]["title"] if first else None,
            "outliers": [b["title"] for _, b, s in rows if s["last"] >= cons_now[name] * OUTLIER],
        })
    order = {"1": 0, "X": 1, "2": 2}
    outcomes.sort(key=lambda o: order.get(o["pick"], 9))
    table = []
    for k, b in sorted(books.items(), key=lambda kv: kv[1]["title"].lower()):
        cells = {label(n, ev): {"open": s["open"], "now": s["last"], "prev": s["prev"]} for n, s in b["o"].items()}
        table.append({"key": k, "title": b["title"], "sharp": k in SHARP, "exchange": k in EXCHANGES, "odds": cells})
    rank = {"güçlü": 3, "orta": 2, "zayıf": 1, None: 0}
    return {"id": eid, "league": league, "home": ev["home"], "away": ev["away"], "commence": ev["commence"],
            "signal": max((o["level"] for o in outcomes), key=lambda l: rank[l], default=None),
            "outcomes": outcomes, "table": table,
            "hist": ev.get("hist", [])[-24:]}


def run(key, fetch=None, t=None):
    t = t or now()
    OUT.mkdir(parents=True, exist_ok=True)
    state = load_state()
    day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
    usage = state.setdefault("usage", {})
    if usage.get("day") != day:
        usage.update({"day": day, "used": 0})
    cap, remaining = daily_cap(state, t)
    status, fetched = "ok", []

    for lg, lg_name in LEAGUES.items():
        try:
            events, _ = get(f"{API}/{lg}/events", {"apiKey": key, "dateFormat": "iso"}, fetch)
        except urllib.error.HTTPError as e:
            status = f"{lg}: events HTTP {e.code}"
            continue
        upcoming = [e for e in events if 0 < iso_to_ts(e["commence_time"]) - t <= HORIZON_H * 3600]
        if not upcoming:
            continue
        h = min(iso_to_ts(e["commence_time"]) - t for e in upcoming) / 3600
        iv = interval_for(h)
        last = state["lastFetch"].get(lg, 0)
        if iv is None or t - last < iv:
            continue
        if remaining < 5:
            status = "kredi bitti"
            break
        if usage["used"] >= cap and h > 2:
            continue
        try:
            data, hdr = get(f"{API}/{lg}/odds", {"apiKey": key, "regions": REGIONS, "markets": MARKET,
                                                  "oddsFormat": "decimal", "dateFormat": "iso"}, fetch)
        except urllib.error.HTTPError as e:
            status = f"{lg}: odds HTTP {e.code}"
            if e.code in (401, 429):
                break
            continue
        state["lastFetch"][lg] = t
        usage["used"] += int(hdr.get("x-requests-last", 1) or 1)
        if hdr.get("x-requests-remaining") is not None:
            remaining = int(float(hdr["x-requests-remaining"]))
            state["budget"] = {"remaining": remaining, "used": int(float(hdr.get("x-requests-used", 0))), "at": t}
        fetched.append(lg_name)
        for ev in data:
            if 0 < iso_to_ts(ev["commence_time"]) - t <= HORIZON_H * 3600:
                es = state["events"].setdefault(ev["id"], {"league": lg_name})
                update_event(es, ev, t)

    # başlamasından 3 saat geçen maçları at
    state["events"] = {k: v for k, v in state["events"].items() if iso_to_ts(v["commence"]) > t - 3 * 3600}
    if not fetched and DROPS_P.exists():
        return status, fetched  # yeni veri yok; dosyalara dokunma (gereksiz commit olmasın)
    items = [summarize(k, v, v["league"]) for k, v in state["events"].items() if v.get("books")]
    rank = {"güçlü": 3, "orta": 2, "zayıf": 1, None: 0}
    items.sort(key=lambda e: (-rank[e["signal"]], e["commence"]))
    out = {"updatedAt": int(t * 1000), "status": status, "leagues": list(LEAGUES.values()),
           "credits": state.get("budget", {}).get("remaining"), "dailyCap": cap, "events": items}
    DROPS_P.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    STATE_P.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return status, fetched


if __name__ == "__main__":
    k = os.environ.get("ODDS_API_KEY", "").strip()
    if not k:
        sys.exit("ODDS_API_KEY yok: GitHub > Settings > Secrets and variables > Actions'a ekleyin.")
    st, f = run(k)
    print(f"durum: {st} · çekilen: {', '.join(f) or 'yok'}")
