"""Classify every non-exact agent answer and its cause, reproducibly.

Inputs (run from the eval folder, after compare_final.py):
    final_comparison/per_term_<DATASET>.csv   (gold class and outcome per system)
    <agent folder>/raw.jsonl                    (the agent's traces)

    python classify_disagreements.py --dataset ENVO --agent test_envo_agent
    python classify_disagreements.py --dataset OBI  --agent test_obi_agent

Outputs (final_comparison/):
    disagreements_<DATASET>.csv   one row per non-exact term, automatic cause,
                                  evidence, and an empty `verdict` column for the
                                  incompatible cases (to fill by hand)
    and a printed summary table.

Automatic coding (no judgement involved):
  * more_specific  -> R1 "reference more general than the agent"
  * more_general   -> the first CONTESTED question of the majority-class runs
                      gives the missing distinction (D1-D6 below); if the agent
                      flagged an ambiguity it is reported too; if no question was
                      contested the agent stopped on a conservative COMMON answer.
  * incompatible   -> automatic hints only (anchor conflict, reference named as
                      the agent's alternative); the verdict is coded by hand:
                      agent_error / reference_inconsistent / both_defensible.
"""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# Missing distinction, by the diagram question at which the agent stopped.
DISTINCTION = {
    "Q_CONT_OCC": "D1 continuant vs occurrent",
    "Q_DEPENDENCE": "D2 dependence on a bearer",
    "Q_SHARED": "D2 dependence on a bearer",
    "Q_REALISATION": "D3 realizable vs quality",
    "Q_SITUATION": "D3 role / disposition / function",
    "Q_FUNCTION": "D3 role / disposition / function",
    "Q_MATTER": "D4 material vs immaterial",
    "Q_BOUNDED_OR_PORTION": "D4 material vs immaterial",
    "Q_COLLECTION": "D5 unity: object / aggregate / part",
    "Q_PROPER_PART": "D5 unity: object / aggregate / part",
    "Q_ST_RELATION": "D6 kind of occurrent or region",
    "Q_MOMENT": "D6 kind of occurrent or region",
    "Q_PROCESS_BOUNDARY": "D6 kind of occurrent or region",
    "Q_SUBSTANTIAL_CHANGE": "D6 kind of occurrent or region",
    "Q_CHANGE_OR_RATE": "D6 kind of occurrent or region",
}
# Why the definition left the decision open: a reading of the agent's own
# ambiguity message and contested-step reasoning. Checked in this order.
TRIGGERS = [
    ("T5 designation vs designated thing",
     r"designation|legal|status|information content"),
    ("T3 magnitude noun (quality / disposition / profile)",
     r"quality|disposition|magnitude|measur|\brate\b|realis|realiz|capacity|\benergy\b"),
    ("T2 thing vs happening",
     r"process|event|occurr|time interval|temporal|span of time|rainfall"),
    ("T1 matter vs the space it occupies",
     r"space|site|surface|immaterial|spatial extent|region|hollow|boundary"),
    ("T4 unity not stated (whole / part / collection)",
     r"proper part|aggregate|collection|separate object|amount of|mass noun|"
     r"maximal|single body|part of"),
]
OUTCOME_GROUP = {
    "exact": "exact", "more_specific": "more_specific",
    "more_general": "more_general",
    "other_branch": "incompatible", "wrong_main_branch": "incompatible",
}


def load_parents(path):
    tax = json.loads(Path(path).read_text(encoding="utf-8"))
    parents = {}

    def walk(node, parent=None):
        parents[node["id"]] = parent
        for child in node.get("children", []):
            walk(child, node["id"])

    walk(tax)
    return parents


def lineage(cls, parents):
    out = []
    while cls:
        out.append(cls)
        cls = parents.get(cls)
    return out


def levels(pred, gold, parents):
    """(up, down): steps from the agent's class up to the closest common
    ancestor, then down to the reference. more_general -> (0, k);
    more_specific -> (k, 0); incompatible -> both > 0."""
    a, g = lineage(pred, parents), lineage(gold, parents)
    if pred not in parents or gold not in parents:
        return None, None
    common = next(c for c in a if c in g)
    return a.index(common), g.index(common)


def contested_step(steps):
    for st in steps or []:
        if (str(st.get("grade", "")).lower() == "contested"
                or str(st.get("answer", "")).lower() == "not_established"):
            return st
    return None


def trigger(responses):
    """Most frequent trigger over the majority-class runs."""
    found = Counter()
    for x in responses:
        st = contested_step(x.get("steps")) or {}
        text = " ".join([x.get("ambiguity_message") or "",
                         st.get("reasoning") or ""]).lower()
        if not text.strip():
            continue
        found[next((name for name, rx in TRIGGERS if re.search(rx, text)),
                   "T0 no recognisable trigger")] += 1
    return found.most_common(1)[0][0] if found else "T0 no reasoning recorded"


def first_contested(steps):
    for st in steps or []:
        grade = str(st.get("grade", "")).lower()
        answer = str(st.get("answer", "")).lower()
        if grade == "contested" or answer == "not_established":
            return st.get("question_id") or "?"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="OBI or ENVO (as in compare_final)")
    ap.add_argument("--agent", required=True, help="agent result folder (raw.jsonl)")
    ap.add_argument("--dir", default="final_comparison")
    ap.add_argument("--system", default="Agent", help="column prefix in per_term csv")
    ap.add_argument("--taxonomy",
                    default=str(Path(__file__).resolve().parent.parent
                                / "src" / "app" / "taxonomy.json"))
    a = ap.parse_args()

    per_term = list(csv.DictReader(open(Path(a.dir) / f"per_term_{a.dataset}.csv",
                                        encoding="utf-8")))
    runs = defaultdict(list)
    for line in open(Path(a.agent) / "raw.jsonl", encoding="utf-8"):
        if line.strip():
            rec = json.loads(line)
            if rec.get("ok") and rec["response"].get("terminal_class"):
                runs[rec["term"]].append(rec["response"])

    parents = load_parents(a.taxonomy)
    rows, summary = [], Counter()
    for r in per_term:
        term, gold = r["term"], r["gold"]
        cls, outcome = r[f"{a.system}_class"], r[f"{a.system}_outcome"]
        group = OUTCOME_GROUP.get(outcome, outcome)
        if group == "exact":
            continue
        maj = [x for x in runs.get(term, []) if x.get("terminal_class") == cls]
        ambiguous = any(x.get("ambiguous") for x in maj)
        alternatives = Counter(x.get("alternative_class") for x in maj
                               if x.get("alternative_class"))
        alternative = alternatives.most_common(1)[0][0] if alternatives else ""
        anchor = Counter(x.get("inherited_bfo_class") for x in maj
                         if x.get("inherited_bfo_class"))
        anchor = anchor.most_common(1)[0][0] if anchor else ""
        stops = Counter(first_contested(x.get("steps")) for x in maj)
        stop = stops.most_common(1)[0][0] if stops else None

        up, down = levels(cls, gold, parents)
        trig = trigger(maj) if group in ("more_general", "incompatible") else ""
        if group == "more_specific":
            n_levels = up
        elif group == "more_general":
            n_levels = down
        else:
            n_levels = (up or 0) + (down or 0)

        if group == "more_specific":
            cause = "R1 reference more general than the answer"
        elif group == "more_general":
            if stop:
                cause = DISTINCTION.get(stop, f"D? {stop}")
            else:
                cause = "D0 conservative COMMON answer (no contested step)"
        else:
            hints = []
            if anchor and anchor != gold:
                hints.append(f"quoted genus anchors {anchor}")
            if alternative == gold:
                hints.append("reference named as alternative")
            cause = "I " + ("; ".join(hints) if hints else "to code by hand")

        summary[(group, cause.split(" ", 1)[0] if group != "incompatible" else "I")] += 1
        rows.append({
            "term": term, "gold": gold, "agent_class": cls, "outcome": outcome,
            "group": group, "levels": n_levels, "up": up, "down": down,
            "cause": cause, "trigger": trig, "stop_question": stop or "",
            "ambiguous": ambiguous, "alternative": alternative,
            "reference_is_alternative": alternative == gold, "anchor": anchor,
            "verdict": "",  # incompatible only: agent_error / reference_inconsistent / both_defensible
        })

    tag = a.system.replace("+", "").replace(" ", "")
    out = Path(a.dir) / f"disagreements_{a.dataset}_{tag}.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["term"])
        w.writeheader()
        w.writerows(rows)

    n_all = len(per_term)
    print(f"{a.dataset}: {len(rows)} non-exact terms out of {n_all}")
    by_group = Counter(r["group"] for r in rows)
    for g in ("more_specific", "more_general", "incompatible"):
        print(f"\n  {g}: {by_group[g]}")
        causes = Counter(r["cause"] if g != "incompatible" else r["cause"]
                         for r in rows if r["group"] == g)
        for c, k in causes.most_common():
            print(f"     {k:>3}  {c}")
        dist = Counter(r["levels"] for r in rows if r["group"] == g)
        if dist:
            label = "path length (up+down)" if g == "incompatible" else "levels"
            vals = [r["levels"] for r in rows if r["group"] == g and r["levels"] is not None]
            print(f"     {label}: " + ", ".join(f"{k}: {dist[k]}" for k in sorted(dist, key=lambda x: (x is None, x)))
                  + (f"   (mean {sum(vals)/len(vals):.2f})" if vals else ""))
    mg_cause_lvl = Counter((r["cause"].split(" ", 1)[0], r["levels"]) for r in rows
                           if r["group"] == "more_general")
    if mg_cause_lvl:
        print("\n  more general: cause x levels")
        for (c, l), k in sorted(mg_cause_lvl.items()):
            print(f"     {c:<3} {l} level(s): {k}")
    tr = Counter((r["trigger"], r["group"]) for r in rows if r["trigger"])
    if tr:
        print("\n  why the definition left the decision open (general / incompatible)")
        for t in sorted({k[0] for k in tr}):
            print(f"     {tr[(t, 'more_general')]:>3} {tr[(t, 'incompatible')]:>3}  {t}")
    mg = [r for r in rows if r["group"] == "more_general"]
    if mg:
        named = sum(r["reference_is_alternative"] for r in mg)
        amb = sum(r["ambiguous"] for r in mg)
        print(f"\n  more general answers flagged ambiguous: {amb}/{len(mg)}; "
              f"reference class named as the alternative: {named}/{len(mg)}")
        print("  more general answers by reference class:")
        for g, k in Counter(r["gold"] for r in mg).most_common():
            print(f"     {k:>3}  {g}")
    # Cross-tabulation: what does this system answer where another one fails?
    others = sorted({k[:-len("_outcome")] for k in per_term[0]
                     if k.endswith("_outcome") and k != f"{a.system}_outcome"})
    for o in others:
        tab = Counter((OUTCOME_GROUP.get(r[f"{o}_outcome"], r[f"{o}_outcome"]),
                       OUTCOME_GROUP.get(r[f"{a.system}_outcome"], r[f"{a.system}_outcome"]))
                      for r in per_term)
        print(f"\n  {a.system} outcome on the terms where {o} is ...")
        groups = ("exact", "more_specific", "more_general", "incompatible")
        short = {"exact": "exact", "more_specific": "specific",
                 "more_general": "general", "incompatible": "incomp"}
        print(f"     {'':<16}" + "".join(f"{short[g]:>10}" for g in groups))
        for go in groups:
            n = sum(tab[(go, g)] for g in groups)
            if n:
                print(f"     {o[:6] + ' ' + short[go]:<16}"
                      + "".join(f"{tab[(go, g)]:>10}" for g in groups)
                      + f"   (n={n})")

    print(f"\nWritten: {out}  (fill `verdict` for the incompatible rows)")


if __name__ == "__main__":
    main()
