"""Stratified sample of an extracted term list: at most --cap terms per
reference BFO class (all terms of rarer classes kept), fixed seed.

    python stratified_sample.py obi_terms.csv --cap 25 --seed 42 --out obi_sample.csv
"""
import argparse, csv, random
from collections import Counter, defaultdict
ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--cap", type=int, default=25)
ap.add_argument("--seed", type=int, default=42); ap.add_argument("--out", required=True)
a = ap.parse_args()
rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
by = defaultdict(list)
for r in rows: by[r["gold_ontology"]].append(r)
rng = random.Random(a.seed)
sample = []
for cls in sorted(by):
    items = sorted(by[cls], key=lambda r: r["iri"])
    sample += items if len(items) <= a.cap else rng.sample(items, a.cap)
sample.sort(key=lambda r: (r["gold_ontology"], r["term"].lower()))
with open(a.out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(sample)
print(len(sample), dict(Counter(r["gold_ontology"] for r in sample)))
