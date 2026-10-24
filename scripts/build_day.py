"""Builds data/<day>.json and updates data/index.json from exported JSON files.

Usage: python3 scripts/build_day.py <day YYYY-MM-DD> <export_dir>
<export_dir> holds fixtures/<sport>-<day>.json and analyses/<id>.json files
(the shape the Maç Analiz Masası artifact database uses).
"""
import json, sys, time
from pathlib import Path

day, src = sys.argv[1], Path(sys.argv[2])
root = Path(__file__).resolve().parent.parent
data_dir = root / "data"
data_dir.mkdir(exist_ok=True)

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

out = {"day": day, "updatedAt": int(time.time() * 1000), "analyses": {}}
for sport in ("futbol", "basketbol"):
    f = src / "fixtures" / f"{sport}-{day}.json"
    matches = load(f).get("matches", []) if f.exists() else []
    out[sport] = {"matches": [
        {k: m.get(k, "") for k in ("id", "league", "time", "home", "away", "venue", "note")}
        for m in matches
    ]}

for f in sorted((src / "analyses").glob("*.json")) if (src / "analyses").exists() else []:
    a = load(f)
    if a.get("day") == day and a.get("matchId") and isinstance(a.get("result"), dict):
        out["analyses"][a["matchId"]] = {"result": a["result"], "createdAt": a.get("createdAt")}

(data_dir / f"{day}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

idx_path = data_dir / "index.json"
idx = load(idx_path) if idx_path.exists() else {"days": []}
days = sorted(set(idx.get("days", [])) | {day}, reverse=True)[:60]
idx_path.write_text(json.dumps({"days": days}, ensure_ascii=False), encoding="utf-8")
print(f"{day}: {sum(len(out[s]['matches']) for s in ('futbol','basketbol'))} maç, {len(out['analyses'])} analiz")
