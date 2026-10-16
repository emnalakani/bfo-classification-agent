"""Remove baseline answers that have no class from raw.jsonl, so that
run_eval.py re-runs only those (term, run) pairs. A backup is kept.

    python remove_empty.py test_obi_b1cot test_envo_b1cot
"""
import json
import shutil
import sys
from pathlib import Path

for folder in sys.argv[1:]:
    path = Path(folder) / "raw.jsonl"
    shutil.copy(path, path.with_name("raw_before_rerun.jsonl"))
    kept, removed = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("ok") and not rec["response"].get("terminal_class"):
            removed += 1
            continue
        kept.append(line)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"{folder}: {removed} empty answers removed, {len(kept)} kept "
          f"(backup: raw_before_rerun.jsonl)")
