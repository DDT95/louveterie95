"""Télécharge le référentiel officiel des communes du 95 (geo.api.gouv.fr) -> engine/communes95.csv."""
import csv
import json
import urllib.request
from pathlib import Path

URL = 'https://geo.api.gouv.fr/departements/95/communes?fields=code,nom&format=json'
rows = json.load(urllib.request.urlopen(URL, timeout=60))
rows.sort(key=lambda r: r['code'])
out = Path(__file__).with_name('communes95.csv')
with open(out, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f, delimiter=';')
    w.writerow(['insee_com', 'commune'])
    w.writerows((r['code'], r['nom']) for r in rows)
print(len(rows), 'communes ->', out)
