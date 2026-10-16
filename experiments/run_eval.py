"""Expert-free evaluation of the BFO classification agent.

Runs every term of a CSV N times through the backend (/api/classify), with no
user interaction, then computes:

  * agreement of the agent's majority class with each reference annotator
    (exact / agent more general / agent more specific / other branch), with the
    Keet et al. (2023) error types for disagreements;
  * human-human agreement on the same terms (ceiling, no new annotator needed);
  * run-to-run stability (share of runs giving the majority class);
  * trace compliance: valid at first attempt, repair rate, fallback rate;
  * ambiguity rate;
  * effect of the validator: model class vs validated class.

Only the Python standard library is used.

Usage (backend running on port 8000):
    python run_eval.py --csv goro.csv --runs 5 --out results_goro_agent
    python run_eval.py --csv goro.csv --runs 5 --out results_goro_b0 --system baseline_naive
    python run_eval.py --csv goro.csv --runs 5 --out results_goro_b1 --system baseline_diagram

Systems: agent (full pipeline, /api/classify), baseline_naive (B0: zero-shot),
baseline_diagram (B1: zero-shot + Keet diagram, no agent rules, no validator),
and *_cot variants of both (free-text reasoning allowed before the answer).

CSV columns: term, definition (may be empty), domain, and one or more
gold_<annotator> columns holding taxonomy ids (e.g. gold_v1, gold_v2).
Any other column is carried through untouched. Re-running with the same --out
resumes: already completed (term, run) pairs are skipped.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TAXONOMY = HERE.parent / "src" / "app" / "taxonomy.json"


# --------------------------------------------------------------------------
# Taxonomy helpers
# --------------------------------------------------------------------------
class Taxonomy:
    def __init__(self, path: Path):
        tree = json.loads(path.read_text(encoding="utf-8"))
        self.parent: dict[str, str | None] = {}

        def walk(node, parent=None):
            self.parent[node["id"]] = parent
            for child in node.get("children", []):
                walk(child, node["id"])

        walk(tree)

    def ancestors(self, node: str) -> list[str]:
        """node itself first, then up to the root."""
        chain = []
        while node:
            chain.append(node)
            node = self.parent.get(node)
        return chain

    def is_ancestor_or_self(self, a: str, b: str) -> bool:
        return a in self.ancestors(b)

    def top_branch(self, node: str) -> str:
        chain = self.ancestors(node)
        for top in ("continuant", "occurrent"):
            if top in chain:
                return top
        return "entity"

    def depth(self, node: str) -> int:
        return len(self.ancestors(node)) - 1

    def distance(self, a: str, b: str) -> int:
        """Number of edges between a and b through their lowest common ancestor."""
        anc_a = self.ancestors(a)
        anc_b = self.ancestors(b)
        for i, node in enumerate(anc_a):
            if node in anc_b:
                return i + anc_b.index(node)
        return len(anc_a) + len(anc_b)


def compare(tax: Taxonomy, agent: str, gold: str) -> str:
    """Outcome of the agent's class against one reference class.

    Categories follow Keet et al. (2023): exact; too high / not deep enough
    (agent is an ancestor: correct but more general); agent more specific
    than the reference (the reference may itself be too shallow); wrong main
    branch (continuant vs occurrent); other (sibling / wrong sub-branch).
    """
    if not agent or agent == "entity":
        return "no_class"
    if agent == gold:
        return "exact"
    if tax.is_ancestor_or_self(agent, gold):
        return "agent_more_general"
    if tax.is_ancestor_or_self(gold, agent):
        return "agent_more_specific"
    if tax.top_branch(agent) != tax.top_branch(gold):
        return "wrong_main_branch"
    return "other_branch"


COMPATIBLE = {"exact", "agent_more_general", "agent_more_specific"}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def call_classify(api: str, payload: dict, timeout: int, endpoint: str = "/api/classify") -> dict:
    req = urllib.request.Request(
        f"{api.rstrip('/')}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "response": json.loads(resp.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {detail[:500]}"}
    except Exception as e:  # network, timeout, JSON
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def get_health(api: str) -> dict:
    try:
        with urllib.request.urlopen(f"{api.rstrip('/')}/api/health", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def extract(record: dict) -> dict:
    """Keep the fields needed for analysis (the full response stays in raw)."""
    if not record.get("ok"):
        return {"error": record.get("error", "unknown error")}
    r = record["response"]
    validated = r.get("terminal_class") or ""
    return {
        "validated_class": validated,
        # Class the model proposed before any repair/fallback (validator effect).
        "model_class": r.get("model_first_attempt_class") or r.get("model_terminal_class") or validated,
        "confidence": r.get("confidence", ""),
        "ambiguous": bool(r.get("ambiguous")),
        "alternative_class": r.get("alternative_class") or "",
        "trace_valid_first_attempt": bool(r.get("trace_valid_first_attempt")),
        "trace_repair_triggered": bool(r.get("trace_repair_triggered")),
        "trace_validation_status": r.get("trace_validation_status", ""),
        "prompt_version": r.get("prompt_version", ""),
        "assumed_definition": r.get("assumed_definition", ""),
        "genus_verified": r.get("genus_claims_verified", True),
        "source_conflict": bool(r.get("source_conflict")),
        "has_anchor": bool(r.get("inherited_bfo_class")),
    }


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
def load_done(raw_path: Path) -> set[tuple[str, int]]:
    done = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok"):
                done.add((rec["term"], rec["run"]))
    return done


def run_all(rows, args, raw_path: Path) -> None:
    done = load_done(raw_path)
    total = len(rows) * args.runs
    n = 0
    with raw_path.open("a", encoding="utf-8") as out:
        for row in rows:
            for run in range(1, args.runs + 1):
                n += 1
                if (row["term"], run) in done:
                    continue
                payload = {
                    "term": row["term"],
                    "definition": row.get("definition", "") or "",
                    "domain": row.get("domain", "") or "",
                }
                if args.system == "agent":
                    payload["classification_mode"] = args.mode
                    endpoint = "/api/classify"
                else:
                    payload["variant"] = args.system.split("_", 1)[1]
                    endpoint = "/api/baseline"
                t0 = time.time()
                for attempt in range(args.retries + 1):
                    rec = call_classify(args.api, payload, args.timeout, endpoint)
                    err = rec.get("error", "")
                    if rec.get("ok") or attempt == args.retries:
                        break
                    if "429" in err:
                        wait = 30 * (attempt + 1)
                    elif "HTTP 502" in err:
                        wait = 5
                    else:
                        break
                    if attempt == 0:
                        msg = re.search(r"'message': '([^']+)", err)
                        print(f"    error: {msg.group(1) if msg else err[:300]}")
                    print(f"    retry {attempt + 1}/{args.retries} in {wait}s")
                    time.sleep(wait)
                rec.update({"term": row["term"], "run": run, "system": args.system,
                            "seconds": round(time.time() - t0, 1)})
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                status = extract(rec)
                shown = status.get("validated_class") or status.get("error", "")[:80]
                print(f"[{n}/{total}] {row['term']} · run {run} → {shown}")
                time.sleep(args.pause)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def analyse(rows, gold_cols, tax: Taxonomy, raw_path: Path, runs: int):
    by_term: dict[str, list[dict]] = {}
    errors = 0
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("ok"):
            errors += 1
            continue
        by_term.setdefault(rec["term"], {})[rec["run"]] = extract(rec)

    per_term = []
    calib: dict = {}
    for row in rows:
        results = [by_term.get(row["term"], {}).get(r) for r in range(1, runs + 1)]
        results = [r for r in results if r]
        if not results:
            per_term.append({"term": row["term"], "n_runs": 0})
            continue
        counts = Counter(r["validated_class"] for r in results)
        majority, maj_n = counts.most_common(1)[0]
        tie = sum(1 for v in counts.values() if v == maj_n) > 1
        model_counts = Counter(r["model_class"] for r in results)
        model_majority = model_counts.most_common(1)[0][0]
        entry = {
            "term": row["term"],
            "n_runs": len(results),
            "majority_class": majority,
            "stability": round(maj_n / len(results), 3),
            "majority_tie": tie,
            "distinct_classes": len(counts),
            "all_classes": "; ".join(f"{k}×{v}" for k, v in counts.most_common()),
            "model_majority_class": model_majority,
            "valid_first_attempt_rate": round(
                sum(r["trace_valid_first_attempt"] for r in results) / len(results), 3),
            "repair_rate": round(sum(r["trace_repair_triggered"] for r in results) / len(results), 3),
            "fallback_rate": round(
                sum(r["trace_validation_status"] == "fallback_to_valid_prefix" for r in results)
                / len(results), 3),
            "ambiguous_rate": round(sum(r["ambiguous"] for r in results) / len(results), 3),
            "genus_verified_rate": round(sum(bool(r["genus_verified"]) for r in results) / len(results), 3),
            "anchor_rate": round(sum(r["has_anchor"] for r in results) / len(results), 3),
            "source_conflict_rate": round(sum(r["source_conflict"] for r in results) / len(results), 3),
            "validator_changed_class_rate": round(
                sum(r["model_class"] != r["validated_class"] for r in results) / len(results), 3),
            "prompt_version": results[0]["prompt_version"],
        }
        golds = {c: row[c] for c in gold_cols if row.get(c)}
        # Calibration: per run, is the result right more often when confidence is high?
        for r in results:
            outs = [compare(tax, r["validated_class"], g) for g in golds.values()]
            c = calib.setdefault(r["confidence"] or "?", {"runs": 0, "exact": 0, "compatible": 0})
            c["runs"] += 1
            c["exact"] += "exact" in outs
            c["compatible"] += any(o in COMPATIBLE for o in outs)
        for col, gold in golds.items():
            entry[f"{col}"] = gold
            entry[f"vs_{col}"] = compare(tax, majority, gold)
            entry[f"model_vs_{col}"] = compare(tax, model_majority, gold)
            entry[f"dist_{col}"] = tax.distance(majority, gold) if majority else ""
            entry[f"run_exact_{col}"] = round(
                sum(r["validated_class"] == gold for r in results) / len(results), 3)
        outcomes = [entry[f"vs_{c}"] for c in golds]
        entry["exact_with_any"] = "exact" in outcomes
        entry["compatible_with_any"] = any(o in COMPATIBLE for o in outcomes)
        per_term.append(entry)

    evaluated = [e for e in per_term if e.get("n_runs")]
    n = len(evaluated) or 1

    def rate(key):
        return round(sum(bool(e.get(key)) for e in evaluated) / n, 3)

    def mean(key):
        return round(sum(e.get(key, 0) for e in evaluated) / n, 3)

    summary = {
        "terms": len(rows),
        "terms_evaluated": len(evaluated),
        "runs_per_term": runs,
        "failed_calls_total": errors,  # includes calls retried successfully on resume
        "exact_with_any_annotator": rate("exact_with_any"),
        "compatible_with_any_annotator": rate("compatible_with_any"),
        "mean_stability": mean("stability"),
        "fully_stable_terms": round(sum(e["stability"] == 1 for e in evaluated) / n, 3),
        "mean_valid_first_attempt": mean("valid_first_attempt_rate"),
        "mean_repair_rate": mean("repair_rate"),
        "mean_fallback_rate": mean("fallback_rate"),
        "mean_ambiguous_rate": mean("ambiguous_rate"),
        "mean_validator_changed_class_rate": mean("validator_changed_class_rate"),
        "mean_genus_verified_rate": mean("genus_verified_rate"),
        "mean_anchor_rate": mean("anchor_rate"),
        "terms_with_source_conflict": [e["term"] for e in evaluated if e.get("source_conflict_rate")],
        "per_annotator": {},
        "human_human_exact_agreement": {},
    }
    for col in gold_cols:
        outcomes = Counter(e.get(f"vs_{col}") for e in evaluated if e.get(f"vs_{col}"))
        model_outcomes = Counter(e.get(f"model_vs_{col}") for e in evaluated if e.get(f"model_vs_{col}"))
        m = sum(outcomes.values()) or 1
        summary["per_annotator"][col] = {
            "validated": {k: round(v / m, 3) for k, v in outcomes.items()},
            "model_before_validation": {k: round(v / m, 3) for k, v in model_outcomes.items()},
        }
    # Ceiling: how often do the reference annotators agree with each other?
    for a, b in combinations(gold_cols, 2):
        pairs = [(r[a], r[b]) for r in rows if r.get(a) and r.get(b)]
        if pairs:
            summary["human_human_exact_agreement"][f"{a}~{b}"] = round(
                sum(x == y for x, y in pairs) / len(pairs), 3)
    hh = list(summary["human_human_exact_agreement"].values())
    summary["mean_human_human_exact"] = round(sum(hh) / len(hh), 3) if hh else None
    # Fair comparison: the system treated as one more annotator.
    sys_h = []
    sys_h_runs = []
    for col in gold_cols:
        vals = [e.get(f"vs_{col}") == "exact" for e in evaluated if e.get(f"vs_{col}")]
        if vals:
            sys_h.append(sum(vals) / len(vals))
        runs_vals = [e[f"run_exact_{col}"] for e in evaluated if f"run_exact_{col}" in e]
        if runs_vals:
            sys_h_runs.append(sum(runs_vals) / len(runs_vals))
    summary["mean_system_human_exact_majority"] = round(sum(sys_h) / len(sys_h), 3) if sys_h else None
    summary["mean_system_human_exact_per_run"] = round(sum(sys_h_runs) / len(sys_h_runs), 3) if sys_h_runs else None
    # Keet error types, pooled over annotators (majority class).
    pooled = Counter(e.get(f"vs_{c}") for e in evaluated for c in gold_cols if e.get(f"vs_{c}"))
    tot = sum(pooled.values()) or 1
    summary["outcomes_pooled_over_annotators"] = {k: round(v / tot, 3) for k, v in pooled.most_common()}
    summary["ties"] = sum(bool(e.get("majority_tie")) for e in evaluated)
    summary["calibration"] = {
        k: {"runs": v["runs"], "exact_rate": round(v["exact"] / v["runs"], 3),
            "compatible_rate": round(v["compatible"] / v["runs"], 3)}
        for k, v in sorted(calib.items()) if v["runs"]}
    return per_term, summary


def write_outputs(per_term, summary, out_dir: Path, meta: dict) -> None:
    summary["meta"] = meta
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    keys: list[str] = []
    for e in per_term:
        for k in e:
            if k not in keys:
                keys.append(k)
    with (out_dir / "per_term.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(per_term)


def print_summary(per_term, summary) -> None:
    print("\n" + "=" * 72)
    print(f"{'term':<26}{'majority class':<34}{'stab.':>6}  vs annotators")
    print("-" * 72)
    for e in per_term:
        if not e.get("n_runs"):
            print(f"{e['term']:<26}(no successful run)")
            continue
        vs = ", ".join(f"{k[3:]}={v}" for k, v in e.items() if k.startswith("vs_"))
        tie = " (tie)" if e.get("majority_tie") else ""
        print(f"{e['term']:<26}{(e['majority_class'] + tie):<34}{e['stability']:>6}  {vs}")
    print("=" * 72)
    print(f"{'system':<34}{summary['meta'].get('system')}")
    for k in ("mean_system_human_exact_majority", "mean_system_human_exact_per_run",
              "mean_human_human_exact", "exact_with_any_annotator",
              "compatible_with_any_annotator", "mean_stability", "ties",
              "fully_stable_terms", "mean_valid_first_attempt", "mean_repair_rate",
              "mean_fallback_rate", "mean_ambiguous_rate", "mean_validator_changed_class_rate",
              "mean_genus_verified_rate", "mean_anchor_rate",
              "failed_calls_total"):
        print(f"{k:<34}{summary[k]}")
    if summary.get("calibration"):
        print("calibration (confidence -> runs, exact, compatible):")
        for k, v in summary["calibration"].items():
            print(f"  {k:<8} {v['runs']:>4} runs   exact {v['exact_rate']:<6} compatible {v['compatible_rate']}")
    if summary["human_human_exact_agreement"]:
        print("human-human exact agreement (ceiling):")
        for k, v in summary["human_human_exact_agreement"].items():
            print(f"  {k:<32}{v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="input CSV (term, definition, domain, gold_* ...)")
    ap.add_argument("--out", required=True, help="output folder (created; reused to resume)")
    ap.add_argument("--runs", type=int, default=5, help="runs per term (default 5)")
    ap.add_argument("--mode", default="definition_only", choices=["definition_only", "ontology_context"])
    ap.add_argument("--system", default="agent",
                    choices=["agent", "baseline_naive", "baseline_diagram",
                             "baseline_naive_cot", "baseline_diagram_cot", "agent_manual"],
                    help="agent (default), baseline_naive (B0), baseline_diagram (B1), "
                         "or their *_cot variants (reasoning allowed before the answer)")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    ap.add_argument("--timeout", type=int, default=300, help="seconds per call")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between calls")
    ap.add_argument("--analyse-only", action="store_true", help="skip API calls, recompute metrics")
    ap.add_argument("--limit", type=int, default=0, help="only the first N terms of the CSV")
    ap.add_argument("--retries", type=int, default=3,
                    help="retries per call on rate limit (429) or 502 (default 3)")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    with csv_path.open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("term") or "").strip()]
    if args.limit:
        rows = rows[: args.limit]
    gold_cols = [c for c in rows[0] if c.startswith("gold_")] if rows else []
    tax = Taxonomy(Path(args.taxonomy))
    for r in rows:
        for c in gold_cols:
            if r.get(c) and r[c] not in tax.parent:
                raise SystemExit(f"Unknown taxonomy id '{r[c]}' in column {c} for term '{r['term']}'.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.jsonl"
    if raw_path.exists():
        systems = {json.loads(l).get("system", "agent")
                   for l in raw_path.read_text(encoding="utf-8").splitlines() if l.strip()}
        if systems - {args.system}:
            raise SystemExit(f"{raw_path} already holds results of {sorted(systems)}; "
                             f"use another --out folder for --system {args.system}.")

    if args.system == "agent_manual" and not args.analyse_only:
        raise SystemExit("agent_manual results are produced by gpt_session.py; "
                         "use --analyse-only here.")
    health = get_health(args.api)
    if not args.analyse_only:
        if "error" in health:
            raise SystemExit(f"Backend not reachable at {args.api}: {health['error']}")
        print(f"Backend: {health}")
        run_all(rows, args, raw_path)

    per_term, summary = analyse(rows, gold_cols, tax, raw_path, args.runs)
    meta = {"csv": str(csv_path), "system": args.system,
            "mode": args.mode if args.system == "agent" else "n/a", "backend": health,
            "date": time.strftime("%Y-%m-%d %H:%M:%S")}
    summary["meta"] = meta
    print_summary(per_term, summary)
    write_outputs(per_term, summary, out_dir, meta)
    print(f"\nFiles: {out_dir / 'raw.jsonl'}, {out_dir / 'per_term.csv'}, {out_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
