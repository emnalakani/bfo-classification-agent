"""
RQ2 statistics (Section 5.2): stability, kind of variation, trace validity,
effect of the checks, and calibration of the confidence levels.

Usage (from the repository root):
    python evaluation/rq2_stats.py results/test/obi_agent results/test/obi_b0 results/test/obi_b1cot \
        results/test/envo_agent results/test/envo_b0 results/test/envo_b1cot

Each folder must contain raw.jsonl and per_term.csv written by run_eval.py.
Options:
    --taxonomy PATH   BFO taxonomy (default src/app/taxonomy.json)

Outcome categories (reference r, answer c):
    exact       c = r
    general     c is a strict ancestor of r        (exact + general = implied)
    refining    c is a strict descendant of r
    conflicting c and r are incomparable
An empty class ("not established") is treated as entity.
"""
import argparse
import collections
import csv
import json
import os
import sys


def load_parents(path):
    with open(path, encoding="utf-8") as f:
        tree = json.load(f)
    parents = {}

    def walk(node, parent=None):
        parents[node["id"]] = parent
        for child in node.get("children", []):
            walk(child, node["id"])

    walk(tree)
    return parents


def lineage(cls, parents):
    out, x = [], (cls or "entity")
    while x:
        out.append(x)
        x = parents.get(x)
    return out


def outcome(pred, gold, parents):
    pred = pred or "entity"
    if pred == gold:
        return "exact"
    if pred in lineage(gold, parents):
        return "general"
    if gold in lineage(pred, parents):
        return "refining"
    return "conflicting"


def comparable(a, b, parents):
    a, b = a or "entity", b or "entity"
    return a in lineage(b, parents) or b in lineage(a, parents)


def pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "n/a"


def analyse(folder, parents):
    gold = {}
    with open(os.path.join(folder, "per_term.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gold[row["term"]] = row["gold_ontology"]

    runs = collections.defaultdict(list)
    recs = []
    system = "?"
    with open(os.path.join(folder, "raw.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r.get("ok", True) or "response" not in r:
                continue
            x = r["response"]
            system = r.get("system", x.get("system", system))
            term = r["term"]
            if term not in gold:
                continue
            cls = x.get("terminal_class") or ""
            runs[term].append(cls)
            recs.append((term, cls, x))

    print("=" * 70)
    print(f"{folder}   system={system}   terms={len(runs)}   runs={len(recs)}")

    # ---- stability -------------------------------------------------------
    stab = []
    unstable = []
    for term, classes in runs.items():
        counts = collections.Counter(c or "entity" for c in classes)
        stab.append(counts.most_common(1)[0][1] / len(classes))
        if len(counts) > 1:
            unstable.append(term)
    same_path = [t for t in unstable
                 if all(comparable(a, b, parents) for a in runs[t] for b in runs[t])]
    print(f"mean stability            {sum(stab) / len(stab):.3f}")
    print(f"fully stable terms        {len(runs) - len(unstable)} / {len(runs)}")
    print(f"unstable terms            {len(unstable)}")
    print(f"  same path (depth only)  {len(same_path)}")
    print(f"  different branches      {len(unstable) - len(same_path)}")
    for t in unstable:
        if t not in same_path:
            print(f"      cross-branch: {t}: {runs[t]}  (ref {gold[t]})")

    # ---- agent only: checks and confidence --------------------------------
    if not any("trace_valid_first_attempt" in x for _, _, x in recs):
        return
    n = len(recs)
    valid1 = sum(bool(x.get("trace_valid_first_attempt")) for _, _, x in recs)
    repair = sum(bool(x.get("trace_repair_triggered")) for _, _, x in recs)
    fallback = sum(x.get("trace_validation_status") == "fallback_to_valid_prefix"
                   for _, _, x in recs)
    print(f"valid first attempt       {valid1} / {n} = {pct(valid1, n)}")
    print(f"repair triggered          {repair} / {n}")
    print(f"fallback                  {fallback} / {n} = {pct(fallback, n)}")

    changes = collections.Counter()
    for term, cls, x in recs:
        first = x.get("model_first_attempt_class") or ""
        if (first or "entity") != (cls or "entity"):
            changes[(outcome(first, gold[term], parents),
                     outcome(cls, gold[term], parents))] += 1
    print(f"class changed by checks   {sum(changes.values())} / {n}")
    for (a, b), k in changes.most_common():
        print(f"      {a:>11} -> {b:<11} {k}")

    first_exact = sum(outcome(x.get("model_first_attempt_class") or "", gold[t], parents) == "exact"
                      for t, _, x in recs)
    final_exact = sum(outcome(c, gold[t], parents) == "exact" for t, c, _ in recs)
    print(f"exact runs before checks  {pct(first_exact, n)}   after checks {pct(final_exact, n)}")

    errors = collections.Counter()
    for _, _, x in recs:
        if not x.get("trace_valid_first_attempt"):
            for e in x.get("trace_first_attempt_errors") or []:
                errors[str(e).split(":")[0][:60]] += 1
    print("first-attempt errors (by step/check):")
    for e, k in errors.most_common(10):
        print(f"      {k:3d}  {e}")

    print("calibration (run level):")
    print(f"      {'level':<7}{'runs':>6}{'exact':>9}{'implied':>9}{'refining':>10}{'conflict':>10}")
    table = collections.defaultdict(collections.Counter)
    for t, c, x in recs:
        table[x.get("confidence", "?")][outcome(c, gold[t], parents)] += 1
    for level in ["high", "medium", "low"]:
        v = table.get(level, collections.Counter())
        m = sum(v.values())
        print(f"      {level:<7}{m:>6}{pct(v['exact'], m):>9}"
              f"{pct(v['exact'] + v['general'], m):>9}"
              f"{pct(v['refining'], m):>10}{pct(v['conflicting'], m):>10}")
    why_low = collections.Counter(
        "ambiguous" if x.get("ambiguous") else x.get("trace_validation_status")
        for _, _, x in recs if x.get("confidence") == "low")
    print(f"low confidence because    {dict(why_low)}")
    refining_by_level = collections.Counter(
        x.get("confidence") for t, c, x in recs if outcome(c, gold[t], parents) == "refining")
    conflict_by_level = collections.Counter(
        x.get("confidence") for t, c, x in recs if outcome(c, gold[t], parents) == "conflicting")
    print(f"refining runs by level    {dict(refining_by_level)}")
    print(f"conflicting runs by level {dict(conflict_by_level)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--taxonomy", default="src/app/taxonomy.json")
    args = ap.parse_args()
    parents = load_parents(args.taxonomy)
    for folder in args.folders:
        if not os.path.exists(os.path.join(folder, "raw.jsonl")):
            print(f"skip {folder}: no raw.jsonl", file=sys.stderr)
            continue
        analyse(folder, parents)


if __name__ == "__main__":
    main()
