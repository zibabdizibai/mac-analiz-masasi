"""API-Football'dan maç öncesi istatistik çeker -> data/stats/<gün>.json

Çalıştırma: APIFOOTBALL_KEY=... python3 scripts/stats_fetch.py [YYYY-MM-DD]
Gün verilmezse: İstanbul saatiyle 18:00'den sonra yarın, önce bugün.
Her çalıştırmada data/stats/status.json'a hesap/kota/hata bilgisi yazılır
(GitHub Actions loglarına bakmadan sorunu görmek için).

İstek bütçesi (ücretsiz plan günde 100): lig başına 2 (fikstür + puan durumu),
maç başına 4 (iki takım istatistiği, sakatlıklar, son karşılaşmalar) + 1 (status).
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
TZ = "Europe/Istanbul"
LEAGUES = {203: "Trendyol Süper Lig", 39: "Premier Lig", 88: "Eredivisie"}
MAX_MATCHES = 20
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "stats"
KEY = os.environ.get("APIFOOTBALL_KEY", "").strip()
errors, calls = [], {"n": 0}


def api(path, **params):
    calls["n"] += 1
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY, "User-Agent": "mac-analiz-masasi"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        errors.append(f"{path} HTTP {e.code}")
        return []
    except Exception as e:  # ağ hatası
        errors.append(f"{path} {type(e).__name__}")
        return []
    err = data.get("errors")
    if err:
        errors.append(f"{path} {json.dumps(err, ensure_ascii=False)[:200]}")
    time.sleep(6.5)  # ücretsiz plan dakikada ~10 istek
    return data.get("response") or []


def season_for(day):
    d = datetime.fromisoformat(day)
    return d.year if d.month >= 7 else d.year - 1


def pct(a, b):
    return round(100 * a / b) if b else None


def team_stats(league, season, team_id):
    r = api("/teams/statistics", league=league, season=season, team=team_id)
    if not isinstance(r, dict):
        return None
    fx, g = r.get("fixtures", {}), r.get("goals", {})
    played = fx.get("played", {})
    def avg(side, where):
        try:
            return float(g[side]["average"][where])
        except (KeyError, TypeError, ValueError):
            return None
    cs, fts = r.get("clean_sheet", {}), r.get("failed_to_score", {})
    uo = (g.get("for") or {}).get("under_over") or {}
    return {
        "form": (r.get("form") or "")[-6:],
        "played": played,
        "wins": fx.get("wins"), "draws": fx.get("draws"), "loses": fx.get("loses"),
        "goalsForAvg": {w: avg("for", w) for w in ("home", "away", "total")},
        "goalsAgainstAvg": {w: avg("against", w) for w in ("home", "away", "total")},
        "cleanSheetPct": {w: pct(cs.get(w) or 0, played.get(w) or 0) for w in ("home", "away", "total")},
        "failedToScorePct": {w: pct(fts.get(w) or 0, played.get(w) or 0) for w in ("home", "away", "total")},
        "scoredOver15Pct": pct((uo.get("1.5") or {}).get("over") or 0, played.get("total") or 0) if uo else None,
    }


def standings_map(league, season):
    r = api("/standings", league=league, season=season)
    out = {}
    try:
        groups = r[0]["league"]["standings"]
    except (IndexError, KeyError, TypeError):
        return out
    for grp in groups:
        for row in grp:
            out[row["team"]["id"]] = {
                "rank": row.get("rank"), "points": row.get("points"), "goalsDiff": row.get("goalsDiff"),
                "form": row.get("form"), "played": row["all"].get("played"),
                "home": {k: row["home"].get(k) for k in ("played", "win", "draw", "lose")} | {"gf": row["home"]["goals"]["for"], "ga": row["home"]["goals"]["against"]},
                "away": {k: row["away"].get(k) for k in ("played", "win", "draw", "lose")} | {"gf": row["away"]["goals"]["for"], "ga": row["away"]["goals"]["against"]},
            }
    return out


def main():
    if not KEY:
        sys.exit("APIFOOTBALL_KEY yok")
    now_ist = datetime.now(timezone(timedelta(hours=3)))
    day = sys.argv[1] if len(sys.argv) > 1 else (now_ist + timedelta(days=1 if now_ist.hour >= 18 else 0)).strftime("%Y-%m-%d")
    season = season_for(day)
    OUT.mkdir(parents=True, exist_ok=True)

    st = api("/status")
    acct = st if isinstance(st, dict) else {}
    matches = []
    for lid, lname in LEAGUES.items():
        fixtures = api("/fixtures", date=day, league=lid, season=season, timezone=TZ)
        if not fixtures:
            continue
        table = standings_map(lid, season)
        for f in fixtures:
            if len(matches) >= MAX_MATCHES:
                break
            if f["fixture"]["status"]["short"] in ("PST", "CANC", "ABD"):
                continue
            h, a = f["teams"]["home"], f["teams"]["away"]
            inj = api("/injuries", fixture=f["fixture"]["id"])
            h2h = api("/fixtures/headtohead", h2h=f"{h['id']}-{a['id']}", last=5, timezone=TZ)
            def side(t, where):
                return {"id": t["id"], "name": t["name"], "table": table.get(t["id"]),
                        "stats": team_stats(lid, season, t["id"]),
                        "injuries": [{"player": i["player"]["name"], "type": i["player"].get("type"), "reason": i["player"].get("reason")}
                                     for i in inj if i["team"]["id"] == t["id"]]}
            matches.append({
                "league": lname, "fixtureId": f["fixture"]["id"], "kickoff": f["fixture"]["date"],
                "venue": (f["fixture"].get("venue") or {}).get("name"),
                "home": side(h, "home"), "away": side(a, "away"),
                "h2h": [{"date": x["fixture"]["date"][:10], "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"],
                         "score": f'{x["goals"]["home"]}-{x["goals"]["away"]}'} for x in h2h if x["goals"]["home"] is not None][:5],
            })

    gen = int(time.time() * 1000)
    if matches:
        (OUT / f"{day}.json").write_text(json.dumps({"day": day, "season": season, "generatedAt": gen, "source": "API-Football",
                                                     "matches": matches}, ensure_ascii=False, indent=1), encoding="utf-8")
    status = {"at": gen, "day": day, "season": season, "matches": len(matches), "calls": calls["n"],
              "plan": (acct.get("subscription") or {}).get("plan"), "requests": acct.get("requests"), "errors": errors[:30]}
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
