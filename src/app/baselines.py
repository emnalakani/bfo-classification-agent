"""Baselines for the evaluation of the BFO classification agent.

Both baselines use the SAME model, temperature and taxonomy as the agent, and
the same inputs (term, optional definition, optional domain). They differ from
the agent only in what is given to the model:

  B0 "naive"   — zero-shot: "classify this term under BFO", list of class ids.
  B1 "diagram" — zero-shot + the same Keet et al. (2023) v2 decision diagram
                 file the agent uses, with the instruction to follow it.
                 No agent rules (evidence grades, Rules A–G, T1/T2), no trace,
                 no validator, no repair.
  "*_cot" variants — same prompts, but the model may reason step by step in
                 free text before the final JSON (fair comparison with the
                 agent, which always reasons before answering).

Comparing B0 → B1 → agent separates the contribution of (i) the Keet diagram
and (ii) the agent's rules + deterministic validation.
"""
from __future__ import annotations

import json

from .prompts import FLAT_NODES, NODE_IDS, _load_diagram_text

BASELINE_VERSION = "b1.3"

_CLASS_LIST = "\n".join(
    f"{'  ' * n['depth']}- {n['id']} ({n['label']})" for n in FLAT_NODES
)

_OUTPUT_RULES = f"""Answer ONLY with a JSON object of the form:
{{"terminal_class": "<one id from the list>", "justification": "<one sentence>"}}

"terminal_class" must be exactly one of these BFO class ids (indentation shows the hierarchy):
{_CLASS_LIST}
"""

NAIVE_SYSTEM = (
    "You are an ontology engineer. Classify the given domain term under the "
    "Basic Formal Ontology (BFO). Choose the most specific appropriate BFO class.\n\n"
    + _OUTPUT_RULES
)


def diagram_system() -> str:
    return (
        "You are an ontology engineer. Classify the given domain term under the "
        "Basic Formal Ontology (BFO) by following the BFO Classifier decision "
        "diagram of Keet et al. (2023, v2) given below, question by question, "
        "and choose the class it leads to.\n\n"
        "## DECISION DIAGRAM\n\n" + _load_diagram_text() + "\n\n" + _OUTPUT_RULES
    )


def user_message(term: str, definition: str, domain: str) -> str:
    lines = [f"TERM: {term}"]
    lines.append(f"DEFINITION: {definition.strip()}" if definition.strip() else "DEFINITION: (none supplied)")
    lines.append(f"DOMAIN: {domain.strip()}" if domain.strip() else "DOMAIN: (none supplied)")
    return "\n".join(lines)


COT_SUFFIX = (
    "\n\nFirst reason step by step in plain text (for the diagram variant: answer "
    "each diagram question in order). Then end your answer with the JSON object "
    "described above, alone on the last line."
)


def _last_json_object(text: str) -> dict:
    """Parse the last {...} object in a free-text answer."""
    end = text.rfind("}")
    while end != -1:
        start = text.rfind("{", 0, end)
        while start != -1:
            try:
                obj = json.loads(text[start:end + 1])
                if isinstance(obj, dict) and "terminal_class" in obj:
                    return obj
            except json.JSONDecodeError:
                pass
            start = text.rfind("{", 0, start)
        end = text.rfind("}", 0, end)
    return {}


def _call_once(create_chat, model, max_tokens, system, user, cot):
    # No response_format: the Anthropic OpenAI-compatible endpoint rejects
    # {"type": "json_object"}. The JSON object is read from the text instead,
    # for every variant (same parsing as the *_cot variants).
    response = create_chat(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    choice = response.choices[0]
    text = choice.message.content or ""
    data = _last_json_object(text)
    if not data:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
    return text, data, str(getattr(choice, "finish_reason", "") or "")


def run_baseline(create_chat, model: str, max_tokens: int,
                 variant: str, term: str, definition: str, domain: str) -> dict:
    """One baseline classification.

    If the answer is empty, cut off, or has no readable class, the same call is
    repeated once with a larger output budget. This is a transport fix, not a
    second chance to reason differently: the prompt is identical, and every
    attempt is recorded (attempts, finish_reasons, output lengths).
    """
    cot = variant.endswith("_cot")
    base = variant[:-4] if cot else variant
    system = NAIVE_SYSTEM if base == "naive" else diagram_system()
    if cot:
        system += COT_SUFFIX
        max_tokens = max(max_tokens, 4000)
    user = user_message(term, definition, domain)

    budgets = [max_tokens, max(2 * max_tokens, 8000)]
    finish_reasons, lengths = [], []
    text, data = "", {}
    for budget in budgets:
        text, data, finish = _call_once(create_chat, model, budget, system, user, cot)
        finish_reasons.append(finish)
        lengths.append(len(text))
        if str(data.get("terminal_class") or "").strip() in NODE_IDS:
            break

    raw_class = str(data.get("terminal_class") or "").strip()
    valid = raw_class in NODE_IDS
    return {
        "system": f"baseline_{variant}",
        "baseline_version": BASELINE_VERSION,
        "terminal_class": raw_class if valid else "",
        "raw_class": raw_class,
        "valid_class_id": valid,
        "justification": str(data.get("justification") or ""),
        "term": term,
        "domain": domain,
        "reasoning": text if cot else "",
        "raw_output": "" if cot else text,
        "attempts": len(finish_reasons),
        "finish_reasons": finish_reasons,
        "output_lengths": lengths,
    }
