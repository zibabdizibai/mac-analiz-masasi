"""Basketbol oran düşüşleri (OddsPapi, ücretsiz plan) -> data/odds/basket.json

Çalıştırma: ODDSPAPI_KEY=... python3 scripts/papi_watch.py
GitHub Actions 15 dakikada bir çalıştırır (.github/workflows/papi.yml).

Ücretsiz plan ayda 250 istek verir, oranlar maç başına çekilir. Bu yüzden:
- Sadece sitedeki günün basketbol listesindeki maçlar izlenir (data/<gün>.json).
- Her maç için 2 istek: "açılış" (maç listede ilk görüldüğünde, en geç 12 saat kala)
  ve "kapanış" (başlamasına <= 75 dk kala). Düşüş = açılıştan kapanışa değişim.
- /tournaments ve /fixtures sonuçları önbelleğe alınır.
- Günlük tavan = kalan istek / sıfırlamaya kalan gün. Tavan dolarsa öncelik:
  Basketbol Süper Ligi > EuroLeague > EuroCup > NBA > diğerleri; Türk takımı olan maçlar önce.
- PAPI_DAY_MAX ortam değişkeni günlük tavanı elle yükseltir (elle çalıştırmada).
Pazar: 111 = Kazanan (uzatmalar dahil), sonuç 111 ev / 112 deplasman.
"""
import json, os, statistics, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pick_odds import sim, norm  # noqa: E402

API = "https://api.oddspapi.io/v4"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "odds"
STATE_P = OUT / "papi_state.json"
OUT_P = OUT / "basket.json"
DEBUG_P = OUT / "papi_debug.json"
IST = timezone(timedelta(hours=3))
BASKET = 11
MONTHLY = 250
MARKET, HOME_O, AWAY_O = "111", "111", "112"
CLOSE_MIN = 75
OPEN_H = 12
DROP_PTS = 0.03
OUTLIER = 1.06
SHARP = ("pinnacle",)
EXCH = ("betfair", "matchbook", "smarkets", "betdaq")
TR = ("fenerbahce", "anadolu efes", "efes", "galatasaray", "besiktas", "bahcesehir", "turk telekom", "karsiyaka",
      "tofas", "darussafaka", "manisa", "bursaspor", "trabzonspor", "aliaga", "mersin", "petkim", "buyukcekmece")
WANT = [  # (öncelik, bizim lig adımızdaki anahtar, OddsPapi turnuva adında aranacaklar)
    (0, "super lig", ["super lig", "bsl"]),
    (1, "euroleague", ["euroleague"]),
    (2, "eurocup", ["eurocup"]),
    (3, "nba", ["nba"]),
    (4, "acb", ["acb", "liga endesa"]),
    (4, "lba", ["lega a", "serie a", "lba"]),
    (4, "aba", ["aba", "adriatic"]),
    (5, "sampiyonlar ligi", ["champions league"]),
]

calls = 0


def get(path, params):
    global calls
    q = urllib.parse.urlencode({"apiKey": KEY, **params})
    req = urllib.request.Request(f"{API}{path}?{q}", headers={"User-Agent": "mac-analiz-masasi"})
    for attempt in range(3):
        try:
            time.sleep(1.2)
            calls += 1
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(3)
                continue
            body = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"{path} HTTP {e.code}: {body}")


def load(p, d):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d


def as_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ("data", "fixtures", "tournaments", "items", "results"):
            if isinstance(x.get(k), list):
                return x[k]
        return list(x.values()) if all(isinstance(v, dict) for v in x.values()) else [x]
    return []


def ts(v):
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def tname(t):
    return " ".join(str(t.get(k, "")) for k in ("tournamentName", "name", "categoryName", "category", "countryName"))


ALIAS = {"kizilyildiz": "crvena zvezda red star", "munih": "munich", "milano": "milano milan armani",
         "olimpia": "olimpia ea7", "budućnost": "buducnost", "kizil": "crvena", "maccabi": "maccabi playtika",
         "fenerbahce beko": "fenerbahce", "anadolu efes": "anadolu efes", "koleji": "koleji kol college"}


def tsim(a, b):
    a = " ".join(ALIAS.get(w, w) for w in norm(a).split())
    return sim(a, b)


def match_tournaments(tours):
    """lig anahtarı -> [tournamentId]"""
    out = {}
    for _, key, words in WANT:
        ids = []
        for t in tours:
            n = norm(tname(t))
            if key == "super lig" and not any(w in n for w in ("turk", "turkey", "turkiye")):
                continue
            if key in ("nba",) and ("g league" in n or "summer" in n or "wnba" in n):
                continue
            if key == "euroleague" and "women" in n:
                continue
            if any(f" {w} " in f" {n} " for w in words):
                tid = t.get("tournamentId", t.get("id"))
                if tid is not None:
                    ids.append(tid)
        out[key] = ids[:3]
    return out


def our_key(league):
    n = norm(league)
    for _, key, _ in WANT:
        if key in n or (key == "super lig" and "bsl" in n):
            return key
    return None


def prio(league, m):
    n = norm(league)
    p = next((pr for pr, key, _ in WANT if key in n), 9)
    tr = any(w in norm(m["home"] + " " + m["away"]) for w in TR)
    return (p - (0.5 if tr else 0), m.get("time", ""))


def parse_odds(o):
    """bookmakerOdds -> {slug: {"1": price, "2": price}}"""
    bo = (o or {}).get("bookmakerOdds") or {}
    out = {}
    for slug, feed in bo.items():
        mk = ((feed or {}).get("markets") or {}).get(MARKET)
        if not mk:
            continue
        cell = {}
        for oid, lab in ((HOME_O, "1"), (AWAY_O, "2")):
            pl = ((mk.get("outcomes") or {}).get(oid) or {}).get("players") or {}
            e = pl.get("0") or (next(iter(pl.values())) if pl else None)
            if isinstance(e, list):
                e = e[-1] if e else None
            if e and e.get("active", True) is not False and e.get("price") and float(e["price"]) > 1:
                cell[lab] = float(e["price"])
        if len(cell) == 2:
            out[slug] = cell
    return out


def title(slug):
    return {"pinnacle": "Pinnacle", "bet365": "bet365", "1xbet": "1xBet", "betfair-ex": "Betfair"}.get(
        slug, slug.replace("-", " ").replace(".", " ").title())


def implied(p):
    return 1 / p if p and p > 1 else None


def consensus(books, f):
    by = {}
    for b in books.values():
        if b.get(f):
            for k, v in b[f].items():
                by.setdefault(k, []).append(1 / v)
    return {k: round(1 / statistics.median(v), 3) for k, v in by.items() if v}


def summarize(rec):
    books = {}
    for slug, p in (rec.get("open") or {}).items():
        books.setdefault(slug, {})["open"] = p
    for slug, p in (rec.get("now") or {}).items():
        books.setdefault(slug, {})["now"] = p
    for b in books.values():  # sadece biri varsa ikisi de o olsun
        b.setdefault("open", b.get("now"))
        b.setdefault("now", b.get("open"))
    co, cn = consensus(books, "open"), consensus(books, "now")
    outcomes = []
    for lab, name in (("1", rec["home"]), ("2", rec["away"])):
        rows = [(s, b) for s, b in books.items() if b.get("now") and lab in b["now"] and lab in b["open"]]
        if not rows or lab not in cn:
            continue
        dropped = [(s, b) for s, b in rows if implied(b["now"][lab]) - implied(b["open"][lab]) >= DROP_PTS]
        sharp = any(s.startswith(SHARP) or any(x in s for x in EXCH) for s, _ in dropped)
        share = len(dropped) / len(rows)
        level = None
        if dropped and rec.get("now"):
            level = "güçlü" if share >= 0.5 and sharp else "orta" if share >= 0.25 or sharp else "zayıf"
        pin = next((b for s, b in rows if s.startswith("pinnacle")), None)
        best = max(rows, key=lambda r: r[1]["now"][lab])
        outcomes.append({
            "pick": lab, "name": name, "consOpen": co.get(lab), "consNow": cn[lab],
            "deltaPts": round(100 * (implied(cn[lab]) - implied(co.get(lab) or cn[lab])), 1),
            "books": len(rows), "dropped": len(dropped), "level": level, "sharpDropped": sharp,
            "pinnacle": {"open": pin["open"][lab], "now": pin["now"][lab]} if pin else None,
            "best": {"price": best[1]["now"][lab], "book": title(best[0])}, "firstMover": None,
            "outliers": [title(s) for s, b in rows if b["now"][lab] >= cn[lab] * OUTLIER][:5],
        })
    table = [{"key": s, "title": title(s), "sharp": s.startswith(SHARP), "exchange": any(x in s for x in EXCH),
              "odds": {k: {"open": b["open"][k], "now": b["now"][k], "prev": b["open"][k]} for k in b["now"] if k in b["open"]}}
             for s, b in sorted(books.items(), key=lambda kv: title(kv[0]).lower()) if b.get("now")]
    rank = {"güçlü": 3, "orta": 2, "zayıf": 1, None: 0}
    return {"id": rec["fixtureId"], "league": rec["league"], "home": rec["home"], "away": rec["away"],
            "commence": datetime.fromtimestamp(rec["start"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "signal": max((o["level"] for o in outcomes), key=lambda l: rank[l], default=None),
            "stage": "kapanış" if rec.get("now") else "açılış", "openAt": rec.get("openAt"), "nowAt": rec.get("nowAt"),
            "outcomes": outcomes, "table": table, "twoWay": True}


def run():
    t = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    st = load(STATE_P, {})
    st.setdefault("used", 0)
    st.setdefault("resetAt", t + 30 * 86400)
    if t >= st["resetAt"]:
        st["used"], st["resetAt"] = 0, t + 30 * 86400
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["dayUsed"] = today, 0
    days_left = max(1, (st["resetAt"] - t) / 86400)
    cap = max(4, int((MONTHLY - st["used"]) / days_left))
    if os.environ.get("PAPI_DAY_MAX", "").strip().isdigit():
        st["override"] = {"day": today, "max": int(os.environ["PAPI_DAY_MAX"])}
    if (st.get("override") or {}).get("day") == today:
        cap = max(cap, st["override"]["max"])
    status, debug = "ok", load(DEBUG_P, {})
    budget = lambda: st["dayUsed"] + calls < cap and st["used"] + calls < MONTHLY - 2  # noqa: E731

    # günün (ve gece yarısından sonraki NBA için ertesi günün) maçları
    ours = []
    for d in (today, (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")):
        dd = load(DATA / f"{d}.json", {})
        for m in dd.get("basketbol", {}).get("matches", []):
            try:
                hh, mm = (m.get("time") or "00:00").split(":")[:2]
                ko = datetime.fromisoformat(d).replace(hour=int(hh), minute=int(mm), tzinfo=IST).timestamp()
            except ValueError:
                continue
            if -3600 < ko - t < 36 * 3600 and our_key(m.get("league", "")):
                ours.append((d, m, ko))
    recs = st.setdefault("matches", {})
    try:
        if not st.get("tours") or t - st.get("toursAt", 0) > 14 * 86400:
            tours = as_list(get("/tournaments", {"sportId": BASKET}))
            debug["tournamentsSample"] = tours[:3]
            st["tours"], st["toursAt"] = match_tournaments(tours), t
            st["allTours"] = sorted({tname(x).strip() for x in tours})[:400]
        need = {our_key(m["league"]) for _, m, ko in ours if m["id"] not in recs or not recs[m["id"]].get("fixtureId")}
        fx_cache = st.setdefault("fixtures", {})
        for key in sorted(k for k in need if k):
            ck = f"{key}:{today}"
            if ck in fx_cache:
                continue
            fl = []
            for tid in st["tours"].get(key, []):
                if not budget():
                    break
                frm = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%d")
                to = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
                res = as_list(get("/fixtures", {"sportId": BASKET, "tournamentId": tid, "from": frm, "to": to}))
                debug.setdefault("fixturesSample", res[:2])
                fl += [{"id": f.get("fixtureId", f.get("id")), "start": ts(f.get("startTime")),
                        "p1": f.get("participant1Name", ""), "p2": f.get("participant2Name", "")} for f in res]
            fx_cache[ck] = fl
        for k in list(fx_cache):
            if not k.endswith(today):
                del fx_cache[k]
        # maçları eşleştir
        for d, m, ko in ours:
            rec = recs.setdefault(m["id"], {"league": m["league"], "home": m["home"], "away": m["away"], "start": ko})
            if rec.get("fixtureId") or rec.get("noFixture"):
                continue
            best, bs, flip = None, 0, False
            for f in fx_cache.get(f"{our_key(m['league'])}:{today}", []):
                if not f["start"] or abs(f["start"] - ko) > 18 * 3600:
                    continue
                a = min(tsim(m["home"], f["p1"]), tsim(m["away"], f["p2"]))
                b = min(tsim(m["home"], f["p2"]), tsim(m["away"], f["p1"]))
                if max(a, b) > bs:
                    best, bs, flip = f, max(a, b), b > a
            if best and bs >= 0.5:
                rec.update({"fixtureId": best["id"], "flip": flip, "start": best["start"] or ko,
                            "apiName": f"{best['p1']} - {best['p2']}"})
            elif f"{our_key(m['league'])}:{today}" in fx_cache:
                rec["noFixture"] = True
                debug.setdefault("unmatchedFixtures", {})[m["id"]] = [
                    f"{f['p1']} - {f['p2']}" for f in fx_cache[f"{our_key(m['league'])}:{today}"]][:20]
        # açılış / kapanış istekleri, öncelik sırasına göre
        todo = sorted(((d, m, ko) for d, m, ko in ours), key=lambda x: prio(x[1]["league"], x[1]))
        for d, m, ko in todo:
            rec = recs[m["id"]]
            if not rec.get("fixtureId"):
                continue
            mins = (rec["start"] - t) / 60
            stage = None
            if not rec.get("open") and 0 < mins <= OPEN_H * 60:
                stage = "open"
            elif rec.get("open") and not rec.get("now") and -10 < mins <= CLOSE_MIN:
                stage = "now"
            if not stage or not budget():
                continue
            raw = get("/odds", {"fixtureId": rec["fixtureId"]})
            if "oddsSample" not in debug:
                bo = (raw or {}).get("bookmakerOdds") or {}
                debug["oddsSample"] = {k: bo[k] for k in list(bo)[:2]}
                debug["oddsKeys"] = [k for k in (raw or {}) if k != "bookmakerOdds"]
            od = parse_odds(raw)
            if rec.get("flip"):
                od = {s: {"1": c["2"], "2": c["1"]} for s, c in od.items()}
            if od:
                rec[stage], rec[stage + "At"] = od, int(t * 1000)
            else:
                rec.setdefault("note", "oran yok")
                if stage == "open":
                    rec["openTries"] = rec.get("openTries", 0) + 1
                    if rec["openTries"] >= 2:
                        rec["open"] = {}
    except RuntimeError as e:
        status = str(e)[:200]
    st["used"] += calls
    st["dayUsed"] += calls
    st["matches"] = {k: v for k, v in recs.items() if v.get("start", 0) > t - 6 * 3600}
    events = [summarize(r) for r in st["matches"].values() if r.get("open") or r.get("now")]
    rank = {"güçlü": 3, "orta": 2, "zayıf": 1, None: 0}
    events.sort(key=lambda e: (-rank[e["signal"]], e["commence"]))
    unmatched = [f"{r['home']} - {r['away']}" for r in st["matches"].values() if r.get("noFixture")]
    out = {"updatedAt": int(t * 1000), "status": status, "source": "OddsPapi",
           "leagues": sorted({e["league"] for e in events}), "credits": MONTHLY - st["used"],
           "dailyCap": cap, "events": events, "unmatched": unmatched,
           "bslFound": bool((st.get("tours") or {}).get("super lig"))}
    if calls or not OUT_P.exists():
        OUT_P.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        STATE_P.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        DEBUG_P.write_text(json.dumps(debug, ensure_ascii=False, indent=1)[:60000], encoding="utf-8")
    print(f"durum: {status} · istek {calls} · bugün {st['dayUsed']}/{cap} · ay {st['used']}/{MONTHLY} · "
          f"maç {len(events)} · eşleşmeyen {len(unmatched)} · turnuvalar {st.get('tours')}")


if __name__ == "__main__":
    KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
    if not KEY:
        sys.exit("ODDSPAPI_KEY yok")
    run()
