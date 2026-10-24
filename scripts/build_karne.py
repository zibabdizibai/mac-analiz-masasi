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
        out_picks.append({"market": p.get("market", ""), "risk": risk, "confidence": p.get("confidence"), "result": res})
    if g.get("status", "bitti") == "bitti":
        summary["verdict"]["toplam"] += 1
        summary["verdict"]["tuttu"] += 1 if g.get("verdictHit") else 0
    matches_out[mid] = {
        "score": g.get("score", ""), "detail": g.get("detail", ""), "status": g.get("status", "bitti"),
        "verdictHit": bool(g.get("verdictHit")), "note": g.get("note", ""), "picks": out_picks,
    }

out = {"day": day, "updatedAt": int(time.time() * 1000), "summary": summary,
       "matches": matches_out, "notes": [str(n) for n in grading.get("notes", [])][:8]}
kd = root / "data" / "karne"
kd.mkdir(parents=True, exist_ok=True)
(kd / f"{day}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
gen = summary["genel"]
decided = gen["tuttu"] + gen["tutmadı"]
print(f"{day}: {len(matches_out)} maç, öneri {gen['tuttu']}/{decided}"
      + (f" (%{round(100*gen['tuttu']/decided)})" if decided else ""))
