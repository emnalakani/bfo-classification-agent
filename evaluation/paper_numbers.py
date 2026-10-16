"""Numbers of the paper that are not printed by compare_final.py,
classify_disagreements.py or rq2_stats.py.

Run from the repository root, after compare_final.py and
classify_disagreements.py have written results/paper/:

    python evaluation/paper_numbers.py

It reads only saved files (no LLM call) and prints, for each item, the
section of the paper it supports:
  - Table 1: dataset sizes and BFO classes covered (Section 4.1)
  - McNemar tests B0 vs B1+CoT (Section 5.1, "The tree alone ...")
  - Fisher's exact tests (Section 5.1, "The agent stops where the baselines fail")
  - refining runs on ENVO activities (Section 5.2)
  - RQ3 breakdowns (Section 5.3)
  - Table 4 with the manual labels (Section 5.3)
  - history answers (Section 5.3, "The decision tree alone can lead away from BFO")
  - conflicting answers placed in a class disjoint from the reference (Section 6)
"""
import csv
import json
from collections import Counter
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "results" / "paper"
TEST = ROOT / "results" / "test"
INCOMP = {"other_branch", "wrong_main_branch"}
SYSTEMS = {"B0": "b0", "B1+CoT": "b1cot", "Agent": "agent"}


# ------------------------------------------------------------------ helpers
def load_parents():
    tax = json.loads((ROOT / "src" / "app" / "taxonomy.json").read_text(encoding="utf-8"))
    parents = {}

    def walk(n, p=None):
        parents[n["id"]] = p
        for c in n.get("children", []):
            walk(c, n["id"])

    walk(tax)
    return parents


def lineage(c, parents):
    out = []
    while c:
        out.append(c)
        c = parents.get(c)
    return out


def mcnemar(b, c):
    """Exact two-sided McNemar test (binomial), as in compare_final.py."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def fisher(a, b, c, d):
    """Two-sided Fisher's exact test for the table [[a, b], [c, d]]."""
    r1, c1, n = a + b, a + c, a + b + c + d

    def p(x):
        return comb(c1, x) * comb(n - c1, r1 - x) / comb(n, r1)

    p_obs = p(a)
    lo, hi = max(0, r1 + c1 - n), min(r1, c1)
    return sum(p(x) for x in range(lo, hi + 1) if p(x) <= p_obs * (1 + 1e-9))


def per_term(ds):
    return list(csv.DictReader(open(PAPER / f"per_term_{ds}.csv", encoding="utf-8")))


def raw(ds, system):
    folder = TEST / f"{ds.lower()}_{SYSTEMS[system]}"
    for line in (folder / "raw.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("ok"):
                yield rec


def header(title):
    print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


# ------------------------------------------------------------------ sections
def table1():
    header("Table 1 - test sets (Section 4.1)")
    covered = set()
    for name, ds in (("obi", "OBI"), ("envo", "ENVO")):
        rep = json.loads((ROOT / "datasets" / "construction" / f"{name}_report.json").read_text())
        rows = list(csv.DictReader(open(ROOT / "datasets" / "test" / f"{name}_sample.csv", encoding="utf-8")))
        classes = {r["gold_ontology"] for r in rows}
        covered |= classes
        print(f"{ds:5} native {rep['native_classes']:5}  kept {rep['filters']['kept']:5}  "
              f"sample {len(rows):4}  BFO classes {len(classes)}")
    print(f"BFO classes covered by the two samples together: {len(covered)}")


def mcnemar_b0_b1():
    header("McNemar B0 vs B1+CoT (Section 5.1)")
    for ds in ("OBI", "ENVO"):
        rows = per_term(ds)
        for label, test in (("exact", lambda o: o == "exact"), ("conflicting", lambda o: o in INCOMP)):
            b = sum(test(r["B0_outcome"]) and not test(r["B1+CoT_outcome"]) for r in rows)
            c = sum(test(r["B1+CoT_outcome"]) and not test(r["B0_outcome"]) for r in rows)
            print(f"{ds:5} {label:12} only B0: {b:3}  only B1+CoT: {c:3}  p = {mcnemar(b, c):.2g}")


def fisher_tests():
    header("Fisher's exact tests, ENVO (Section 5.1)")
    rows = per_term("ENVO")
    for base in ("B0", "B1+CoT"):
        conf = [r for r in rows if r[f"{base}_outcome"] in INCOMP]
        exact = [r for r in rows if r[f"{base}_outcome"] == "exact"]
        a = sum(r["Agent_outcome"] == "more_general" for r in conf)
        c = sum(r["Agent_outcome"] == "more_general" for r in exact)
        p = fisher(a, len(conf) - a, c, len(exact) - c)
        print(f"{base:7} agent more general on {a}/{len(conf)} terms where {base} conflicts, "
              f"{c}/{len(exact)} where {base} is exact;  p = {p:.1e}")


def activities():
    header("Refining agent runs on ENVO activities (Section 5.2)")
    gold = {r["term"]: r["gold"] for r in per_term("ENVO")}
    n = Counter()
    for rec in raw("ENVO", "Agent"):
        x = rec["response"]
        if gold.get(rec["term"]) == "occurrent" and x.get("terminal_class") == "process":
            n[x.get("confidence")] += 1
    print(f"runs answering process for a term whose reference is occurrent: {dict(n)}")


def rq3(parents):
    header("Disagreements of the agent (Section 5.3)")
    for ds in ("OBI", "ENVO"):
        rows = list(csv.DictReader(open(PAPER / f"disagreements_{ds}_Agent.csv", encoding="utf-8")))
        groups = Counter(r["group"] for r in rows)
        gen = [r for r in rows if r["group"] == "more_general"]
        print(f"{ds}: {len(rows)} disagreements {dict(groups)}")
        print(f"   refining (reference -> agent): "
              f"{dict(Counter((r['gold'], r['agent_class']) for r in rows if r['group'] == 'more_specific'))}")
        print(f"   refining exactly one level below the reference: "
              f"{sum(lineage(r['agent_class'], parents)[1] == r['gold'] for r in rows if r['group'] == 'more_specific')}")
        if gen:
            print(f"   more general: mean levels above reference {sum(int(r['levels']) for r in gen) / len(gen):.1f}; "
                  f"ambiguous {sum(r['ambiguous'] == 'True' for r in gen)}")
            print(f"   distinction at which they stop: {dict(Counter(r['cause'] for r in gen))}")


def table4():
    header("Table 4 - conflicting answers of the agent with manual labels (Section 5.3)")
    labels = {(r["dataset"], r["term"]): r for r in
              csv.DictReader(open(ROOT / "results" / "manual_labels" / "conflicting_answers.csv", encoding="utf-8"))}
    for ds in ("OBI", "ENVO"):
        for r in per_term(ds):
            if r["Agent_outcome"] in INCOMP:
                lab = labels.get((ds, r["term"]))
                print(f"{ds:5} {r['term'][:45]:45} ref {r['gold']:18} agent {r['Agent_class']:33} "
                      f"origin {lab['origin'] if lab else 'MISSING LABEL'}")


def history():
    header("history answers (Section 5.3)")
    for ds in ("OBI", "ENVO"):
        for system in SYSTEMS:
            hist = Counter(rec["term"] for rec in raw(ds, system)
                           if rec["response"].get("terminal_class") == "history")
            print(f"{ds:5} {system:7} runs answering history: {sum(hist.values())} {dict(hist)}")
    for system in SYSTEMS:
        ice = [rec["response"].get("terminal_class") for rec in raw("ENVO", system) if rec["term"] == "ice age"]
        print(f"ice age, {system:7}: {ice}")


def disjointness(parents):
    header("Conflicting answers placed in a class disjoint from the reference (Section 6)")
    spec = json.loads((ROOT / "evaluation" / "bfo2020_disjoint_pairs.json").read_text())
    pairs = {frozenset((p[0]["id"], p[1]["id"])) for p in spec["pairs"]}
    print(f"disjoint pairs declared in BFO 2020: {len(pairs)}")

    def disjoint(a, b):
        return any(frozenset((x, y)) in pairs for x in lineage(a, parents) for y in lineage(b, parents))

    for system in SYSTEMS:
        total = dis = 0
        for ds in ("OBI", "ENVO"):
            for r in per_term(ds):
                if r[f"{system}_outcome"] in INCOMP:
                    total += 1
                    dis += disjoint(r[f"{system}_class"], r["gold"])
        print(f"{system:7} conflicting answers on paired terms: {total}, disjoint from the reference: {dis}")


def main():
    parents = load_parents()
    table1()
    mcnemar_b0_b1()
    fisher_tests()
    activities()
    rq3(parents)
    table4()
    history()
    disjointness(parents)


if __name__ == "__main__":
    main()
