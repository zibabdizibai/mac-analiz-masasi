"""football-data.org'dan maç öncesi istatistik çeker -> data/stats/<gün>.json

Çalıştırma: FOOTBALLDATA_KEY=... python3 scripts/stats_fetch.py [YYYY-MM-DD]
Gün verilmezse: İstanbul saatiyle 18:00'den sonra yarın, önce bugün.
Her çalıştırmada data/stats/status.json'a özet ve hatalar yazılır.

Ücretsiz plan: dakikada 10 istek, güncel sezon; Süper Lig ve sakatlık verisi yok.
İstek bütçesi: lig başına 2 (maçlar + puan durumu), maç başına 3
(iki takımın son maçları + iki takımın son karşılaşmaları).
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://api.football-data.org/v4"
COMPS = {"PL": "Premier Lig", "DED": "Eredivisie", "PD": "La Liga", "SA": "Serie A",
         "BL1": "Bundesliga", "FL1": "Ligue 1", "CL": "Şampiyonlar Ligi"}
MAX_MATCHES = 25
LAST_N = 10
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "stats"
KEY = os.environ.get("FOOTBALLDATA_KEY", "").strip()
IST = timezone(timedelta(hours=3))
errors, calls = [], {"n": 0}
_team_cache = {}


def api(path, **params):
    calls["n"] += 1
    url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"X-Auth-Token": KEY, "User-Agent": "mac-analiz-masasi"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            time.sleep(6.5)  # dakikada 10 istek sınırı
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(61)
                continue
            body = e.read().decode(errors="ignore")[:160]
            errors.append(f"{path} HTTP {e.code} {body}")
            time.sleep(6.5)
            return {}
        except Exception as e:
            errors.append(f"{path} {type(e).__name__}")
            return {}
    return {}


def pct(a, b):
    return round(100 * a / b) if b else None


def avg(a, b):
    return round(a / b, 2) if b else None


def tables(code):
    d = api(f"/competitions/{code}/standings")
    out = {}
    for s in d.get("standings", []):
        typ = s.get("type")
        for row in s.get("table", []):
            t = out.setdefault(row["team"]["id"], {})
            t[typ.lower()] = {k: row.get(k) for k in ("position", "playedGames", "won", "draw", "lost", "points", "goalsFor", "goalsAgainst")}
            if typ == "TOTAL":
                t["form"] = row.get("form")
    return out


def team_recent(team_id):
    """Takımın son LAST_N resmi maçından iç/dış saha gol istatistikleri."""
    if team_id in _team_cache:
        return _team_cache[team_id]
    d = api(f"/teams/{team_id}/matches", status="FINISHED", limit=LAST_N * 2,
            dateFrom=(datetime.now(IST) - timedelta(days=120)).strftime("%Y-%m-%d"),
            dateTo=datetime.now(IST).strftime("%Y-%m-%d"))
    ms = sorted(d.get("matches", []), key=lambda m: m["utcDate"])[-LAST_N:]
    agg = {w: {"n": 0, "gf": 0, "ga": 0, "cs": 0, "fts": 0, "o25": 0, "btts": 0} for w in ("home", "away", "total")}
    form = ""
    for m in ms:
        ft = (m.get("score") or {}).get("fullTime") or {}
        if ft.get("home") is None:
            continue
        home = m["homeTeam"]["id"] == team_id
        gf, ga = (ft["home"], ft["away"]) if home else (ft["away"], ft["home"])
        form += "W" if gf > ga else "D" if gf == ga else "L"
        for w in ("home" if home else "away", "total"):
            a = agg[w]
            a["n"] += 1; a["gf"] += gf; a["ga"] += ga
            a["cs"] += ga == 0; a["fts"] += gf == 0
            a["o25"] += gf + ga > 2; a["btts"] += gf > 0 and ga > 0
    res = {
        "form": form[-6:], "sample": {w: agg[w]["n"] for w in agg},
        "goalsForAvg": {w: avg(agg[w]["gf"], agg[w]["n"]) for w in agg},
        "goalsAgainstAvg": {w: avg(agg[w]["ga"], agg[w]["n"]) for w in agg},
        "cleanSheetPct": {w: pct(agg[w]["cs"], agg[w]["n"]) for w in agg},
        "failedToScorePct": {w: pct(agg[w]["fts"], agg[w]["n"]) for w in agg},
        "over25Pct": {w: pct(agg[w]["o25"], agg[w]["n"]) for w in agg},
        "bttsPct": {w: pct(agg[w]["btts"], agg[w]["n"]) for w in agg},
    }
    _team_cache[team_id] = res
    return res


def main():
    if not KEY:
        sys.exit("FOOTBALLDATA_KEY yok")
    now_ist = datetime.now(IST)
    day = sys.argv[1] if len(sys.argv) > 1 else (now_ist + timedelta(days=1 if now_ist.hour >= 18 else 0)).strftime("%Y-%m-%d")
    # İstanbul günü UTC'de iki takvim gününe yayılabilir: bir gün öncesi + o gün
    d0 = (datetime.fromisoformat(day) - timedelta(days=1)).strftime("%Y-%m-%d")
    OUT.mkdir(parents=True, exist_ok=True)

    matches, per_comp = [], {}
    for code, lname in COMPS.items():
        if len(matches) >= MAX_MATCHES:
            break
        d = api(f"/competitions/{code}/matches", dateFrom=d0, dateTo=day)
        todays = [m for m in d.get("matches", [])
                  if datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(IST).strftime("%Y-%m-%d") == day
                  and m.get("status") not in ("POSTPONED", "CANCELLED", "SUSPENDED")]
        per_comp[lname] = len(todays)
        if not todays:
            continue
        tb = tables(code) if code != "CL" else {}
        for m in todays:
            if len(matches) >= MAX_MATCHES:
                break
            h, a = m["homeTeam"], m["awayTeam"]
            h2h = api(f"/matches/{m['id']}/head2head", limit=5)
            def side(t):
                return {"id": t["id"], "name": t.get("shortName") or t.get("name"), "fullName": t.get("name"),
                        "table": tb.get(t["id"]), "stats": team_recent(t["id"])}
            matches.append({
                "league": lname, "fixtureId": m["id"],
                "kickoff": datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(IST).isoformat(),
                "matchday": m.get("matchday"), "home": side(h), "away": side(a),
                "h2h": [{"date": x["utcDate"][:10], "home": x["homeTeam"].get("shortName") or x["homeTeam"]["name"],
                         "away": x["awayTeam"].get("shortName") or x["awayTeam"]["name"],
                         "score": f'{x["score"]["fullTime"]["home"]}-{x["score"]["fullTime"]["away"]}'}
                        for x in h2h.get("matches", []) if (x.get("score") or {}).get("fullTime", {}).get("home") is not None][:5],
                "note": "Sakatlık verisi bu kaynakta yok; haberlerden kontrol edin.",
            })

    gen = int(time.time() * 1000)
    if matches:
        (OUT / f"{day}.json").write_text(json.dumps({"day": day, "generatedAt": gen, "source": "football-data.org",
                                                     "statsWindow": f"son {LAST_N} resmi maç (tüm kulvarlar)",
                                                     "matches": matches}, ensure_ascii=False, indent=1), encoding="utf-8")
    status = {"at": gen, "day": day, "source": "football-data.org", "matches": len(matches), "perLeague": per_comp,
              "calls": calls["n"], "errors": errors[:30]}
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
