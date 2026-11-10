import json
from collections import defaultdict
from datetime import date

with open("docs/data/films_cache.json", encoding="utf-8") as f:
    films = json.load(f)

cinema_max = defaultdict(str)
cinema_min = defaultdict(lambda: "9999-99-99")
for film in films.values():
    for c in film.get("cinemas", []):
        cid = c["id"]
        for dk in list(c.get("showtimes", {}).keys()) + list(c.get("vose_showtimes", {}).keys()):
            if dk > cinema_max[cid]:
                cinema_max[cid] = dk
            if dk < cinema_min[cid]:
                cinema_min[cid] = dk

today = date.today().isoformat()
print(f"Today: {today}\n")
print(f"{'Cinema':<22} {'From':<12} {'To':<12} Days ahead")
print("-" * 58)
for cid in sorted(cinema_max.keys()):
    max_d = cinema_max[cid]
    days = (date.fromisoformat(max_d) - date.fromisoformat(today)).days
    print(f"  {cid:<20} {cinema_min[cid]:<12} {max_d:<12} +{days}")
