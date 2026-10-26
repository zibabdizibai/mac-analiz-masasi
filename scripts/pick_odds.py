"""Önerilerin oranlarını kaydeder -> data/odds_picks/<gün>.json

Modlar:
  open  : günün analizlerindeki her önerinin o anki piyasa oranını kaydeder
          (analizler repoya gönderilince iş akışı tetikler; bugün + yarın).
  close : başlamasına <=25 dk kalan maçların kapanış oranını kaydeder
          (15 dakikada bir çalışır).

Çalıştırma: ODDS_API_KEY=... python3 scripts/pick_odds.py open|close

Öneri biçimi (analizdeki result.picks[i].bet):
  {"type":"1X2","sel":"1|X|2"} · {"type":"DC","sel":"1X|X2|12"} · {"type":"DNB","sel":"1|2"}
  {"type":"OU","sel":"over|under","line":2.5} · {"type":"AH","sel":"home|away","line":-1.5}
  {"type":"BTTS","sel":"yes|no"} · {"type":"OTHER"}  (BTTS ve OTHER için toplu oran yok)
Değer: ev = prob * piyasa_oranı - 1 (piyasa oranı = sitelerin medyanı).
"""
import json, os, re, statistics, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.the-odds-api.com/v4/sports"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTD = DATA / "odds_picks"
STATE = OUTD / "_state.json"
IST = timezone(timedelta(hours=3))
VALUE_EV = 0.05
CLOSE_WINDOW_MIN = 25

STATIC = {  # lig adındaki anahtar kelime -> sport key
    "super lig": "soccer_turkey_super_league", "premier lig": "soccer_epl", "premier league": "soccer_epl",
    "la liga": "soccer_spain_la_liga", "serie a": "soccer_italy_serie_a", "bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one", "eredivisie": "soccer_netherlands_eredivisie",
    "sampiyonlar ligi": "soccer_uefa_champs_league", "champions league": "soccer_uefa_champs_league",
    "avrupa ligi": "soccer_uefa_europa_league", "europa league": "soccer_uefa_europa_league",
    "konferans ligi": "soccer_uefa_europa_conference_league", "championship": "soccer_efl_champ",
    "nba": "basketball_nba", "euroleague": "basketball_euroleague",
}
STOP = {"fc", "cf", "sk", "jk", "bk", "afc", "ac", "as", "sc", "ssc", "cd", "ud", "rc", "club", "de", "the", "basket",
        "basketbol", "spor", "kulubu", "calcio", "1913", "1907", "1909", "1899", "ev", "cf."}


def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower().replace("ı", "i"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def toks(s):
    return {t for t in norm(s).split() if t not in STOP and len(t) > 1}


def sim(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    # kısmi eşleşme: "inter" ~ "internazionale", "psg" ~ "paris saint germain" baş harfleri
    for x in ta - tb:
        if any(y.startswith(x) or x.startswith(y) for y in tb if min(len(x), len(y)) >= 4):
            inter += 0.8
    ini_a, ini_b = "".join(t[0] for t in norm(a).split()), "".join(t[0] for t in norm(b).split())
    if len(ta) == 1 and next(iter(ta)) == ini_b or len(tb) == 1 and next(iter(tb)) == ini_a:
        inter += 1
    return inter / min(len(ta), len(tb))


def get(path, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{API}{path}?{q}", headers={"User-Agent": "mac-analiz-masasi"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()), {k.lower(): v for k, v in r.headers.items()}


def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def sport_key(league, sports):
    n = norm(league)
    for k, v in STATIC.items():
        if k in n:
            return v
    best, bs = None, 0
    for s in sports:  # ücretsiz /sports listesinden en benzer başlık
        sc = sim(league, s.get("title", "") + " " + s.get("description", ""))
        if sc > bs:
            best, bs = s["key"], sc
    return best if bs >= 0.6 else None


def match_event(m, day, events):
    best, bs = None, 0
    for e in events:
        try:
            dt = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")).astimezone(IST)
        except Exception:
            continue
        if abs((dt.date() - datetime.fromisoformat(day).date()).days) > 1:
            continue
        sc = min(sim(m["home"], e["home_team"]), sim(m["away"], e["away_team"]))
        if sc > bs:
            best, bs = e, sc
    return best if bs >= 0.5 else None


def prices(ev, bet):
    """Bir öneri için her sitedeki oran -> {bookmaker_key: (title, price)}"""
    t, sel, line = bet.get("type"), str(bet.get("sel", "")).lower(), bet.get("line")
    home, away = ev["home_team"], ev["away_team"]
    out = {}
    for bk in ev.get("bookmakers", []):
        mk = {m["key"]: m for m in bk.get("markets", [])}
        p = None
        if t in ("1X2", "DC", "DNB") and "h2h" in mk:
            o = {("1" if x["name"] == home else "2" if x["name"] == away else "X"): x["price"] for x in mk["h2h"]["outcomes"]}
            if t == "1X2":
                p = o.get(sel.upper())
            elif t == "DC" and len(o) == 3 and len(sel) == 2:
                ip = sum(1 / o[c] for c in sel.upper() if c in o)
                p = round(1 / ip, 3) if ip else None
            elif t == "DNB" and len(o) == 3 and sel in ("1", "2"):
                pw, pd = 1 / o[sel], 1 / o["X"]
                p = round((1 - pd) / pw, 3) if pw else None
        elif t == "OU" and "totals" in mk and line is not None:
            for x in mk["totals"]["outcomes"]:
                if x["name"].lower() == sel and abs(float(x.get("point", -1)) - float(line)) < 1e-6:
                    p = x["price"]
        elif t == "AH" and "spreads" in mk and line is not None:
            team = home if sel == "home" else away
            for x in mk["spreads"]["outcomes"]:
                if x["name"] == team and abs(float(x.get("point", 99)) - float(line)) < 1e-6:
                    p = x["price"]
        if p and p > 1:
            out[bk["key"]] = (bk.get("title", bk["key"]), float(p))
    return out


def snapshot(ev, bet, t):
    pr = prices(ev, bet)
    if not pr:
        return None
    vals = [v for _, v in pr.values()]
    bk, (title, best) = max(pr.items(), key=lambda kv: kv[1][1])
    return {"median": round(statistics.median(vals), 3), "best": best, "bestBook": title,
            "pinnacle": pr.get("pinnacle", (None, None))[1], "books": len(vals), "at": int(t * 1000)}


def markets_for(sk):
    return "h2h,spreads,totals" if sk.startswith("basketball") else "h2h,totals"


def run(mode, key):
    t = time.time()
    OUTD.mkdir(parents=True, exist_ok=True)
    state = load(STATE, {"remaining": None})
    rem = state.get("remaining")
    if rem is not None and rem < (20 if mode == "open" else 60):
        print(f"kredi az ({rem}); {mode} atlandı")
        return
    now_ist = datetime.now(IST)
    days = [now_ist.strftime("%Y-%m-%d"), (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")]
    sports = None
    todo = {}  # sport_key -> [(day, matchId, match, analysis)]
    for day in days:
        dp = DATA / f"{day}.json"
        if not dp.exists():
            continue
        d = load(dp, {})
        out = load(OUTD / f"{day}.json", {"day": day, "matches": {}})
        for sp in ("futbol", "basketbol"):
            for m in d.get(sp, {}).get("matches", []):
                a = d.get("analyses", {}).get(m["id"])
                if not a:
                    continue
                rec = out["matches"].get(m["id"], {})
                try:
                    hh, mm = (m.get("time") or "00:00").split(":")[:2]
                    ko = datetime.fromisoformat(day).replace(hour=int(hh), minute=int(mm), tzinfo=IST)
                    if sp == "basketbol" and "nba" in norm(m.get("league", "")) and int(hh) < 12:
                        pass  # NBA maçları gece; tarih zaten o gün
                except ValueError:
                    ko = None
                mins = (ko.timestamp() - t) / 60 if ko else None
                if mins is not None and mins < -5:
                    continue  # başlamış
                if mode == "open" and rec.get("open"):
                    continue
                if mode == "close" and (not rec.get("open") or rec.get("close") or mins is None or mins > CLOSE_WINDOW_MIN):
                    continue
                if sports is None:
                    try:
                        sports, _ = get("", {"apiKey": key})
                    except Exception:
                        sports = []
                sk = rec.get("sportKey") or sport_key(m.get("league", ""), sports)
                if sk:
                    todo.setdefault(sk, []).append((day, m["id"], m, a))
    if not todo:
        print("yapılacak yok")
        return
    outs = {}
    for sk, items in todo.items():
        try:
            evs, hdr = get(f"/{sk}/odds", {"apiKey": key, "regions": "eu", "markets": markets_for(sk),
                                           "oddsFormat": "decimal", "dateFormat": "iso"})
        except urllib.error.HTTPError as e:
            print(f"{sk}: HTTP {e.code}")
            if e.code in (401, 429):
                break
            continue
        if hdr.get("x-requests-remaining") is not None:
            state["remaining"] = int(float(hdr["x-requests-remaining"]))
        for day, mid, m, a in items:
            out = outs.setdefault(day, load(OUTD / f"{day}.json", {"day": day, "matches": {}}))
            rec = out["matches"].setdefault(mid, {})
            rec["sportKey"] = sk
            ev = None
            if rec.get("eventId"):
                ev = next((e for e in evs if e["id"] == rec["eventId"]), None)
            ev = ev or match_event(m, day, evs)
            if not ev:
                rec.setdefault("note", "Oran sağlayıcısında maç bulunamadı")
                continue
            rec.update({"eventId": ev["id"], "apiHome": ev["home_team"], "apiAway": ev["away_team"]})
            snaps = []
            for p in (a.get("result") or {}).get("picks", []):
                bet = p.get("bet") or {}
                s = snapshot(ev, bet, t) if bet.get("type") not in (None, "OTHER", "BTTS") else None
                if s and mode == "open":
                    prob = p.get("prob")
                    if isinstance(prob, (int, float)) and 0 < prob < 1:
                        s["prob"] = prob
                        s["ev"] = round(prob * s["median"] - 1, 3)
                        s["value"] = s["ev"] >= VALUE_EV
                snaps.append(s)
            rec[mode] = snaps
            if mode == "open":
                ok = [s for s in snaps if s and "ev" in s]
                rec["pass"] = bool(ok) and not any(s["value"] for s in ok)
    for day, out in outs.items():
        out["updatedAt"] = int(t * 1000)
        (OUTD / f"{day}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps(state), encoding="utf-8")
    print(f"{mode}: {sum(len(v) for v in todo.values())} maç, ligler: {', '.join(todo)} · kalan kredi {state.get('remaining')}")


if __name__ == "__main__":
    k = os.environ.get("ODDS_API_KEY", "").strip()
    if not k:
        sys.exit("ODDS_API_KEY yok")
    run(sys.argv[1] if len(sys.argv) > 1 else "open", k)
