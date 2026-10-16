"""Write the exact prompts of the three configurations to prompts/.

The prompts are built by the code in src/app/; this script only renders them
so that they can be read without running the system. No LLM call is made.

    python experiments/export_prompts.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LLM_API_KEY", "not-used-for-export")  # the client is never called

from app.baselines import COT_SUFFIX, NAIVE_SYSTEM, diagram_system, user_message  # noqa: E402
from app.main import ClassifyRequest, build_user_message  # noqa: E402
from app.prompts import PROMPT_VERSION, build_system_prompt  # noqa: E402

EXAMPLE = dict(term="forested area", definition="An area with a high density of trees.",
               domain="environment and ecology")

out = ROOT / "prompts"
out.mkdir(exist_ok=True)
files = {
    "agent_system_prompt.md": build_system_prompt(),
    "agent_user_message_example.txt": build_user_message(
        ClassifyRequest(classification_mode="definition_only", **EXAMPLE)),
    "b0_system_prompt.md": NAIVE_SYSTEM,
    "b1cot_system_prompt.md": diagram_system() + COT_SUFFIX,
    "baseline_user_message_example.txt": user_message(**EXAMPLE),
}
for name, text in files.items():
    (out / name).write_text(text + "\n", encoding="utf-8")
    print(f"prompts/{name}: {len(text)} characters")
print(f"agent prompt version: {PROMPT_VERSION}")
