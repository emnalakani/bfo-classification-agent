"""Final comparison of the systems for the paper.

For each dataset:
  1. finds, for every system, the terms it could not answer at all
     (provider safety filter, failed calls, empty outputs);
  2. keeps only the terms answered by ALL systems (paired comparison);
  3. computes exact / compatible / incompatible / stability per system;
  4. runs exact McNemar tests: agent vs each baseline, on "exact" and on
     "incompatible";
  5. reports agent calibration (confidence -> exact, compatible) and the
     validator effect (first model answer vs final answer).

Outputs: printed report, final_table.csv, final_table.tex, excluded_terms.csv.

Default configuration (run from the repository root):
    python evaluation/compare_final.py --out results/paper

Custom configuration, one --run per (dataset, system, csv, folder):
    python evaluation/compare_final.py --run OBI:Agent:datasets/test/obi_sample.csv:results/test/obi_agent ...

The agent must be the system labelled "Agent".
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

DEFAULT_RUNS = [  # paths relative to the repository root
    "OBI:B0:datasets/test/obi_sample.csv:results/test/obi_b0",
    "OBI:B1+CoT:datasets/test/obi_sample.csv:results/test/obi_b1cot",
    "OBI:Agent:datasets/test/obi_sample.csv:results/test/obi_agent",
    "ENVO:B0:datasets/test/envo_sample.csv:results/test/envo_b0",
    "ENVO:B1+CoT:datasets/test/envo_sample.csv:results/test/envo_b1cot",
    "ENVO:Agent:datasets/test/envo_sample.csv:results/test/envo_agent",
]
AGENT = "Agent"
COMPATIBLE = {"exact", "more_specific", "more_general"}
INCOMPATIBLE = {"other_branch", "wrong_main_branch"}
# Worst first: used to break ties conservatively (against the system).
SEVERITY = ["wrong_main_branch", "other_branch", "more_general",
            "more_specific", "exact"]


# ---------------------------------------------------------------- taxonomy
def load_parents(path: Path) -> dict:
    tax = json.loads(path.read_text(encoding="utf-8"))
    parents = {}

    def walk(node, parent=None):
        parents[node["id"]] = parent
        for child in node.get("children", []):
            walk(child, node["id"])

    walk(tax)
    return parents


def ancestors(cls, parents):
    out = []
    while cls:
        out.append(cls)
        cls = parents.get(cls)
    return out


def outcome(pred, gold, parents):
    if pred == gold:
        return "exact"
    if pred in ancestors(gold, parents):
        return "more_general"
    if gold in ancestors(pred, parents):
        return "more_specific"
    branch = lambda c: [b for b in ("continuant", "occurrent")
                        if b in ancestors(c, parents)]
    return "wrong_main_branch" if branch(pred) != branch(gold) else "other_branch"


# ---------------------------------------------------------------- loading
def load_system(folder: Path):
    """term -> {'classes': [...valid run classes], 'reasons': Counter,
    'confidences': [...], 'first': [...]}"""
    data = defaultdict(lambda: {"classes": [], "reasons": Counter(),
                                "confidences": [], "first": []})
    for line in (folder / "raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        entry = data[rec["term"]]
        if not rec.get("ok"):
            err = str(rec.get("error", ""))
            entry["reasons"]["no_json_block" if "JSON block" in err else "failed_call"] += 1
            continue
        resp = rec["response"]
        cls = resp.get("terminal_class") or ""
        if not cls:
            finish = resp.get("finish_reasons") or []
            entry["reasons"]["content_filter" if "content_filter" in finish
                             else "empty_output"] += 1
            continue
        entry["classes"].append(cls)
        entry["confidences"].append((resp.get("confidence") or "", cls))
        entry["first"].append(resp.get("model_first_attempt_class") or cls)
    return data


def majority(classes, gold, parents):
    counts = Counter(classes)
    top = max(counts.values())
    tied = [c for c, n in counts.items() if n == top]
    # Conservative tie-break: the worst outcome for the system.
    tied.sort(key=lambda c: SEVERITY.index(outcome(c, gold, parents)))
    return tied[0], top / len(classes)


# ---------------------------------------------------------------- stats
def mcnemar(b, c):
    """Exact two-sided McNemar (binomial) p-value."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def pct(x):
    return f"{100 * x:.1f}"


# ---------------------------------------------------------------- main
def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append",
                    help="DATASET:SYSTEM:CSV:FOLDER (repeatable)")
    ap.add_argument("--taxonomy",
                    default=str(here.parent / "src" / "app" / "taxonomy.json"))
    ap.add_argument("--out", default="final_comparison")
    args = ap.parse_args()

    parents = load_parents(Path(args.taxonomy))
    runs = defaultdict(dict)       # dataset -> system -> (csv, folder)
    for spec in args.run or DEFAULT_RUNS:
        ds, system, csv_name, folder = spec.split(":", 3)
        if not (Path(folder) / "raw.jsonl").exists():
            print(f"[skip] {ds}/{system}: {folder}/raw.jsonl not found")
            continue
        runs[ds][system] = (csv_name, folder)

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    table_rows, excluded_rows = [], []

    for ds, systems in runs.items():
        csv_name = next(iter(systems.values()))[0]
        gold = {r["term"]: r["gold_ontology"]
                for r in csv.DictReader(open(csv_name, encoding="utf-8"))}
        loaded = {s: load_system(Path(f)) for s, (_, f) in systems.items()}

        # 1-2. terms answered by every system
        common = []
        for term in gold:
            missing = [s for s, d in loaded.items() if not d[term]["classes"]]
            if missing:
                for s in missing:
                    excluded_rows.append({
                        "dataset": ds, "term": term, "system": s,
                        "reason": ", ".join(f"{k}x{v}" for k, v in
                                            loaded[s][term]["reasons"].items())
                        or "not run"})
            else:
                common.append(term)

        print("=" * 78)
        print(f"{ds}: {len(gold)} terms, {len(common)} answered by all systems "
              f"({len(gold) - len(common)} excluded)")
        for s, d in loaded.items():
            n_out = sum(1 for t in gold if not d[t]["classes"])
            print(f"   {s:<10} unanswered terms: {n_out}")

        # 3. per-system metrics on common terms
        per_term = {}
        for s, d in loaded.items():
            res = {}
            for t in common:
                cls, stab = majority(d[t]["classes"], gold[t], parents)
                res[t] = {"class": cls, "stability": stab,
                          "outcome": outcome(cls, gold[t], parents)}
            per_term[s] = res

        n = len(common)
        print(f"\n   {'system':<10} {'exact':>7} {'compat':>7} {'incomp':>7} "
              f"{'general':>8} {'specific':>9} {'stab':>6}")
        for s in systems:
            res = per_term[s]
            oc = Counter(r["outcome"] for r in res.values())
            row = {
                "dataset": ds, "system": s, "terms": n,
                "exact": oc["exact"] / n,
                "compatible": sum(oc[k] for k in COMPATIBLE) / n,
                "incompatible": sum(oc[k] for k in INCOMPATIBLE) / n,
                "more_general": oc["more_general"] / n,
                "more_specific": oc["more_specific"] / n,
                "stability": sum(r["stability"] for r in res.values()) / n,
                "p_exact_vs_agent": "", "p_incompatible_vs_agent": "",
            }
            table_rows.append(row)
            print(f"   {s:<10} {pct(row['exact']):>7} {pct(row['compatible']):>7} "
                  f"{pct(row['incompatible']):>7} {pct(row['more_general']):>8} "
                  f"{pct(row['more_specific']):>9} {row['stability']:>6.3f}")

        # 4. McNemar: agent vs each baseline
        if AGENT in per_term:
            print("\n   McNemar (exact binomial), agent vs baseline:")
            for s in systems:
                if s == AGENT:
                    continue
                for label, test in (("exact", lambda o: o == "exact"),
                                    ("incompatible", lambda o: o in INCOMPATIBLE)):
                    a = [test(per_term[AGENT][t]["outcome"]) for t in common]
                    b_ = [test(per_term[s][t]["outcome"]) for t in common]
                    only_agent = sum(x and not y for x, y in zip(a, b_))
                    only_base = sum(y and not x for x, y in zip(a, b_))
                    p = mcnemar(only_agent, only_base)
                    for row in table_rows:
                        if row["dataset"] == ds and row["system"] == s:
                            row[f"p_{label}_vs_agent"] = p
                    print(f"   {s:<10} {label:<13} only agent: {only_agent:>3}   "
                          f"only {s}: {only_base:>3}   p = {p:.2g}")

            # 5a. calibration of the agent's computed confidence (run level)
            print("\n   Agent calibration (runs on common terms):")
            calib = defaultdict(lambda: [0, 0, 0])
            for t in common:
                for conf, cls in loaded[AGENT][t]["confidences"]:
                    o = outcome(cls, gold[t], parents)
                    c = calib[conf or "?"]
                    c[0] += 1
                    c[1] += o == "exact"
                    c[2] += o in COMPATIBLE
            for conf in ("high", "medium", "low", "?"):
                if conf in calib:
                    k, e, c = calib[conf]
                    print(f"   {conf:<7} runs {k:>4}   exact {pct(e / k):>5}   "
                          f"compatible {pct(c / k):>5}")

            # 5b. validator effect (run level)
            changed = total = inc_first = inc_final = 0
            for t in common:
                for first, (_, final) in zip(loaded[AGENT][t]["first"],
                                             loaded[AGENT][t]["confidences"]):
                    total += 1
                    changed += first != final
                    inc_first += outcome(first, gold[t], parents) in INCOMPATIBLE
                    inc_final += outcome(final, gold[t], parents) in INCOMPATIBLE
            if total:
                print(f"\n   Validator: class changed in {pct(changed / total)}% "
                      f"of runs; incompatible runs {inc_first} before -> "
                      f"{inc_final} after validation")

        with open(out_dir / f"per_term_{ds}.csv", "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["term", "gold"] + [f"{s}_{k}" for s in systems
                                           for k in ("class", "outcome", "stability")])
            for t in common:
                w.writerow([t, gold[t]] + [per_term[s][t][k] for s in systems
                                           for k in ("class", "outcome", "stability")])

    # ------------------------------------------------------------ files
    if table_rows:
        with open(out_dir / "final_table.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table_rows[0]))
            w.writeheader()
            w.writerows(table_rows)

        fmt_p = lambda p: "--" if p == "" else (
            r"$<$0.001" if p < 0.001 else f"{p:.3f}")
        lines = [r"\begin{table}[t]", r"\centering",
                 r"\caption{Classification results on the terms answered by all "
                 r"systems (majority of 3 runs). Exact McNemar tests against the agent.}",
                 r"\label{tab:main}", r"\small",
                 r"\begin{tabular}{llrrrrrr}", r"\toprule",
                 r"Dataset & System & Exact & Comp. & Incomp. & Stab. & "
                 r"$p_{\text{exact}}$ & $p_{\text{incomp}}$ \\", r"\midrule"]
        last_ds = None
        for r in table_rows:
            if last_ds and r["dataset"] != last_ds:
                lines.append(r"\midrule")
            ds_cell = f"{r['dataset']} ($n$={r['terms']})" if r["dataset"] != last_ds else ""
            last_ds = r["dataset"]
            bold = (lambda x: rf"\textbf{{{x}}}") if r["system"] == AGENT else (lambda x: x)
            lines.append(
                f"{ds_cell} & {bold(r['system'])} & {pct(r['exact'])} & "
                f"{pct(r['compatible'])} & {pct(r['incompatible'])} & "
                f"{r['stability']:.2f} & {fmt_p(r['p_exact_vs_agent'])} & "
                f"{fmt_p(r['p_incompatible_vs_agent'])} \\\\")
        lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
        (out_dir / "final_table.tex").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8")

    with open(out_dir / "excluded_terms.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["dataset", "term", "system", "reason"])
        w.writeheader()
        w.writerows(excluded_rows)

    print("=" * 78)
    print(f"Files written to {out_dir}/: final_table.csv, final_table.tex, "
          f"excluded_terms.csv, per_term_<dataset>.csv")


if __name__ == "__main__":
    main()
