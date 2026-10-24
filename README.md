# Maç Analiz Masası

Günün futbol ve basketbol maçları için hazırlanmış analizleri gösteren statik site. Üyelik veya Claude hesabı gerekmez; GitHub Pages'te ücretsiz yayınlanır.

## Yayınlama (GitHub Pages)

1. GitHub'da yeni bir repo aç (ör. `mac-analiz-masasi`) ve bu klasördeki dosyaları yükle.
2. Repo → **Settings → Pages** → Source: **Deploy from a branch**, Branch: `main`, klasör `/ (root)` → Save.
3. Birkaç dakika sonra site `https://<kullanıcı-adın>.github.io/mac-analiz-masasi/` adresinde açılır.

## Veri nasıl güncellenir

Site sadece `data/` klasöründeki dosyaları okur:

- `data/index.json` — analizi olan günlerin listesi: `{"days": ["2026-10-24", ...]}`
- `data/YYYY-MM-DD.json` — o günün maçları ve analizleri:

```json
{
  "day": "2026-10-24",
  "updatedAt": 1792857000000,
  "futbol":    { "matches": [ { "id": "...", "league": "...", "time": "20:00", "home": "...", "away": "...", "venue": "...", "note": "..." } ] },
  "basketbol": { "matches": [ ... ] },
  "analyses":  { "<maç id>": { "result": { "verdict": "...", "summary": "...", "picks": [], "news": [], "factors": [], "comparisons": [], "consensus": "...", "risks": [], "sources": [] } } }
}
```

Her gece 00:00'da çalışan zamanlanmış görev bu dosyaları üretip repoya gönderir.

## Yerelde deneme

`fetch` dosya protokolünde çalışmadığı için klasörde basit bir sunucu aç:

```
python3 -m http.server 8000
```

Sonra `http://localhost:8000` adresine git.
