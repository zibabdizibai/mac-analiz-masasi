"""Builds data/karne/<day>.json from a grading file and data/<day>.json.

Usage: python3 scripts/build_karne.py <day YYYY-MM-DD> <grading.json>

grading.json shape (written by the nightly scorecard task):
{
  "matches": {
    "<match id>": {
      "score": "1-2",                 # final score ("" if not played)
      "detail": "İY 0-0",             # optional: half-time / quarter info
      "status": "bitti|ertelendi|iptal",
      "verdictHit": true,             # did the one-line verdict come true
      "picks": ["tuttu", "tutmadı", "iade", "belirsiz"],  # same order as the analysis picks
      "note": "short comment"         # optional
    }
  },
  "notes": ["Nerede yanıldık: ...", "İyi giden: ..."]
}
All summary numbers are computed here, not taken from the grading file.
"""
import json, sys, time
from pathlib import Path

day, grading_path = sys.argv[1], Path(sys.argv[2])
root = Path(__file__).resolve().parent.parent
day_data = json.loads((root / "data" / f"{day}.json").read_text(encoding="utf-8"))
grading = json.loads(grading_path.read_text(encoding="utf-8"))

sport_of = {m["id"]: s for s in ("futbol", "basketbol") for m in day_data.get(s, {}).get("matches", [])}
analyses = day_data.get("analyses", {})
VALID = {"tuttu", "tutmadı", "iade", "belirsiz"}

def bucket():
    return {"tuttu": 0, "tutmadı": 0, "iade": 0, "belirsiz": 0}

summary = {"genel": bucket(), "risk": {}, "spor": {}, "verdict": {"tuttu": 0, "toplam": 0}}
odds_p = root / "data" / "odds_picks" / f"{day}.json"
odds = json.loads(odds_p.read_text(encoding="utf-8")).get("matches", {}) if odds_p.exists() else {}

def money():
    return {"bets": 0, "profit": 0.0}

roi = {"tümü": money(), "değerli": money(), "orta": money(), "yüksek": money(), "düşük": money(), "futbol": money(), "basketbol": money()}
clv = {"n": 0, "sum": 0.0, "beat": 0}
calib = {"n": 0, "prob": 0.0, "hit": 0}
matches_out = {}
for mid, g in grading.get("matches", {}).items():
    a = analyses.get(mid)
    if not a or mid not in sport_of:
        print(f"uyarı: {mid} analizlerde yok, atlandı")
        continue
    picks = (a.get("result") or {}).get("picks", [])
    results = [str(x).strip().lower() for x in g.get("picks", [])]
    out_picks = []
    for i, p in enumerate(picks):
        res = results[i] if i < len(results) and results[i] in VALID else "belirsiz"
        risk = str(p.get("risk") or "orta").lower()
        sp = sport_of[mid]
        for b in (summary["genel"], summary["risk"].setdefault(risk, bucket()), summary["spor"].setdefault(sp, bucket())):
            b[res] += 1
        rec = odds.get(mid, {})
        op = (rec.get("open") or [None] * 99)[i] if i < len(rec.get("open") or []) else None
        cp = (rec.get("close") or [None] * 99)[i] if i < len(rec.get("close") or []) else None
        profit = None
        if op and res in ("tuttu", "tutmadı", "iade"):
            profit = round(op["median"] - 1, 3) if res == "tuttu" else -1.0 if res == "tutmadı" else 0.0
            keys = ["tümü", risk, sp] + (["değerli"] if op.get("value") else [])
            for k in keys:
                if k in roi:
                    roi[k]["bets"] += res != "iade"
                    roi[k]["profit"] += profit
            if cp:
                clv["n"] += 1
                clv["sum"] += op["median"] / cp["median"] - 1
                clv["beat"] += op["median"] > cp["median"]
        pr = p.get("prob")
        if isinstance(pr, (int, float)) and 0 < pr < 1 and res in ("tuttu", "tutmadı"):
            calib["n"] += 1
            calib["prob"] += pr
            calib["hit"] += res == "tuttu"
        out_picks.append({"market": p.get("market", ""), "risk": risk, "confidence": p.get("confidence"), "result": res,
                          "prob": pr, "odds": op["median"] if op else None, "close": cp["median"] if cp else None,
                          "ev": op.get("ev") if op else None, "value": bool(op and op.get("value")), "profit": profit})
    if g.get("status", "bitti") == "bitti":
        summary["verdict"]["toplam"] += 1
        summary["verdict"]["tuttu"] += 1 if g.get("verdictHit") else 0
    matches_out[mid] = {
        "score": g.get("score", ""), "detail": g.get("detail", ""), "status": g.get("status", "bitti"),
        "verdictHit": bool(g.get("verdictHit")), "note": g.get("note", ""), "picks": out_picks,
    }

summary["roi"] = {k: {"bets": v["bets"], "profit": round(v["profit"], 2),
                      "roi": round(100 * v["profit"] / v["bets"], 1) if v["bets"] else None} for k, v in roi.items()}
summary["clv"] = {"n": clv["n"], "avg": round(100 * clv["sum"] / clv["n"], 1) if clv["n"] else None,
                  "beatPct": round(100 * clv["beat"] / clv["n"]) if clv["n"] else None}
summary["calib"] = {"n": calib["n"], "avgProb": round(100 * calib["prob"] / calib["n"]) if calib["n"] else None,
                    "hitRate": round(100 * calib["hit"] / calib["n"]) if calib["n"] else None}
out = {"day": day, "updatedAt": int(time.time() * 1000), "summary": summary,
       "matches": matches_out, "notes": [str(n) for n in grading.get("notes", [])][:8]}
kd = root / "data" / "karne"
kd.mkdir(parents=True, exist_ok=True)
(kd / f"{day}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
gen = summary["genel"]
decided = gen["tuttu"] + gen["tutmadı"]
r = summary["roi"]["tümü"]
print(f"oranlı öneri {r['bets']}, kâr {r['profit']} birim, ROI {r['roi']}% · CLV {summary['clv']['avg']}% (n={summary['clv']['n']})")
print(f"{day}: {len(matches_out)} maç, öneri {gen['tuttu']}/{decided}"
      + (f" (%{round(100*gen['tuttu']/decided)})" if decided else ""))
