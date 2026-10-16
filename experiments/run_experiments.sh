#!/usr/bin/env bash
# Re-run the LLM experiments of the paper (costly: 3 configurations x 279 terms
# x 3 runs). Requires the classification server to be running (see README) and
# writes to results/rerun/ so that the original outputs in results/test/ are
# never overwritten. Re-running the same command resumes an interrupted run.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=results/rerun
for ds in obi envo; do
  CSV=datasets/test/${ds}_sample.csv
  python experiments/run_eval.py --csv "$CSV" --runs 3 --out "$OUT/${ds}_b0"    --system baseline_naive
  python experiments/run_eval.py --csv "$CSV" --runs 3 --out "$OUT/${ds}_b1cot" --system baseline_diagram_cot
  python experiments/run_eval.py --csv "$CSV" --runs 3 --out "$OUT/${ds}_agent" --system agent --mode definition_only
done
echo "Done. To recompute the paper's numbers on these outputs, see README (section 'Using new outputs')."
