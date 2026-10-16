#!/usr/bin/env bash
# Recompute every table and number of the paper from the saved outputs in
# results/test/ (no LLM call, a few seconds). Reports are written to
# results/paper/.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=results/paper
mkdir -p "$OUT"
python evaluation/compare_final.py --out "$OUT"                      | tee "$OUT/report_table2_mcnemar.txt"
python evaluation/classify_disagreements.py --dataset OBI  --agent results/test/obi_agent  --dir "$OUT" | tee "$OUT/report_disagreements_obi.txt"
python evaluation/classify_disagreements.py --dataset ENVO --agent results/test/envo_agent --dir "$OUT" | tee "$OUT/report_disagreements_envo.txt"
python evaluation/rq2_stats.py results/test/obi_agent results/test/obi_b0 results/test/obi_b1cot \
                               results/test/envo_agent results/test/envo_b0 results/test/envo_b1cot | tee "$OUT/report_rq2_table3.txt"
python evaluation/paper_numbers.py                                   | tee "$OUT/report_other_numbers.txt"
