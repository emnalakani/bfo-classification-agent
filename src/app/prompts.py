"""Builds the system prompt sent to OpenAI for BFO classification.

Three pieces are assembled, in order:
  1. CLASSIFIER_INSTRUCTIONS — the authors' methodology prompt (edit only when the
     methodology itself changes, and bump PROMPT_VERSION when you do).
  2. The full text of bfo_decision_diagram.md — the Chat Completions API
     cannot fetch an "attached knowledge file" the way the ChatGPT UI
     custom-GPT feature can, so the diagram's full content must be
     inlined directly into the system prompt. Drop the real file at
     backend/app/bfo_decision_diagram.md (same folder as this file).
  3. APP_JSON_INSTRUCTIONS — appended plumbing (not part of the authors'
     methodology) asking the model to close its answer with one
     machine-readable JSON block, so the backend can drive the UI's
     live BFO-hierarchy and decision-tree panels without touching the
     required 5-step + ASCII-tree narrative output at all.
"""
import json
from pathlib import Path

from .keet_tree import question_table_for_prompt

TAXONOMY_PATH = Path(__file__).parent / "taxonomy.json"
DIAGRAM_PATH = Path(__file__).parent / "bfo_decision_diagram.md"

with open(TAXONOMY_PATH, encoding="utf-8") as f:
    BFO_TREE = json.load(f)


def _flatten(node, depth=0, acc=None):
    if acc is None:
        acc = []
    acc.append({"id": node["id"], "label": node["label"], "depth": depth})
    for c in node.get("children", []):
        _flatten(c, depth + 1, acc)
    return acc


FLAT_NODES = _flatten(BFO_TREE)
NODE_IDS = [n["id"] for n in FLAT_NODES]

PROMPT_VERSION = "v4.1"


# ---------------------------------------------------------------------------
# 1. The authors' classification prompt.
#
# PROMPT_VERSION history
#   v2 — Rules A–F, closed genus vocabulary, genus refinement guard,
#        bearer-dependence guard (list of non-dependence relations).
#   v3 — Evidence grades (STATED / ENTAILED / COMMON / CONTESTED);
#        genus anchor becomes a lower bound, not a ceiling (fixes
#        systematic under-specialization); bearer-dependence guard
#        replaced by a generic two-test criterion (inherence + no own
#        location); Rule D extended to non-genus words inside definitions;
#        new Rule G (top-level, amount-of-matter gap, Quality catch-all);
#        explicit role/bearer handling in Rule B; all domain-specific and
#        benchmark examples replaced by abstract patterns.
#   v3.1 — BFO conformance checks for the Keet occurrent sub-branch:
#        History requires the totality of processes of one material
#        entity (Keet's "substantial change" question alone is not
#        enough); Process Profile requires a rate/pattern/aspect OF a
#        process. Final Check updated accordingly.
#   v3.2 — COMMON restricted to everyday questions; BFO-technical
#        distinctions (T1/T2, Site vs Spatial Region, dimensionality,
#        fiat vs bona fide, Function, History, Process Profile) require
#        STATED or ENTAILED evidence. Rule A: an assumed definition must
#        not silently select one of several ontologically distinct senses.
#        Tree-completeness rule: no silent stop at a non-leaf Keet node.
#   v3.3 — Machine trace uses canonical question/answer ids from keet_tree.py
#        so that main.py can replay and validate it deterministically.
#        Dependence is no longer in the "never COMMON" list: it is governed by
#        the T1/T2 tests, which must be reported in the JSON. The technical
#        list now matches keet_tree.TECHNICAL_ANSWERS exactly; conservative
#        "stay" answers may be COMMON.
#   v3.4 — JSON clarifications after the first v3.3 run: T2=unclear +
#        ambiguity accepted for either dependence answer; grade vocabulary
#        restated (no "assumed"); Q_SHARED never skipped. main.py: up to
#        TRACE_MAX_REPAIRS (default 2) repair passes.
#   v3.5 — Grades are checked against evidence: a "stated" step must carry a
#        verbatim "quote" from the SUPPLIED definition/context (an assumed
#        definition is never STATED evidence); a technical answer can be
#        "entailed" only by an explicit genus anchor. main.py falls back to
#        the deepest validly reached class when repairs fail.
#   v4.0 — Consolidation in four principles (evidence grades; quoted genus as
#        lower bound; no silent stop / no silent choice; BFO conformance).
#        The deterministic regex genus resolver is removed: the model quotes
#        the genus and genus_claims.py verifies the quote. Confidence is
#        computed by the backend from the grades. Shorter narrative, no ASCII tree.
#   v4.1 — Dependence test completed with T3 (generic dependence: copyable
#        content carried by a bearer). T1/T2 only covered specific dependence,
#        so information contents and propositions could not reach GDC.
# ---------------------------------------------------------------------------
CLASSIFIER_INSTRUCTIONS = """You are a BFO (Basic Formal Ontology) ontologist. Classify ONE domain term under BFO by following the BFO Classifier decision diagram of Keet et al. (2023, v2), given below as `bfo_decision_diagram.md`, question by question, in strict order. Never guess the final class first and reverse-engineer a path.

Everything below the diagram itself is an agent-level extension organised in four principles. They are domain-independent: apply them identically whatever the subject domain.

# PRINCIPLE 1 — EVERY ANSWER CARRIES AN EVIDENCE GRADE

Classify from evidence about the intended sense of the term, never from its label. The target label, and words of the definition that are not its genus head (e.g. "function" in "a device whose function is…"), are never evidence.

Each answer to a diagram question gets exactly one grade:

* STATED — asserted by the SUPPLIED definition or ontology context. Must be backed by a verbatim quote.
* ENTAILED — follows necessarily from the verified genus anchor (Principle 2) or from earlier answers plus BFO axioms (e.g. a Process is an Occurrent).
* COMMON — not stated but uncontroversial for the intended sense: a careful ontologist could not reasonably answer the opposite. Allowed ONLY for everyday questions: persists vs occurs in time; has mass / material parts; one self-connected thing vs a collection; can be copied between bearers; and conservative "stay" answers (NO to two bearers, NO to Function, NO to substantial change).
* CONTESTED — anything else. Answer `not_established` and stop descending; the result is the class reached so far.

BFO-technical distinctions are never COMMON: bounded by material entity vs portion of space (Site / Continuant Fiat Boundary vs Spatial Region); spatial projection; any dimensionality; YES to Function; YES to substantial change and change vs rate (History / Process Profile). They need STATED or ENTAILED evidence, otherwise they are CONTESTED. Words like "typically", "by nature", "usually" never make an answer COMMON.

Dependence (Q_DEPENDENCE) is decided by three tests, reported as `t1` / `t2` / `t3` (yes / no / unclear):
* T1 inherence: the target is a characteristic, state, pattern, disposition, capacity or role that another entity HAS, and it can change while that entity stays the same.
* T2 no own location: the target has no location of its own besides its bearer's; it does not occupy, delimit or constitute a portion of space or matter.
* T3 generic dependence: the target is a content (information, proposition, plan, specification, design, sequence, rule…) that exists only as carried by some bearer, and the same content can be copied onto other bearers.
T1 and T2 → dependent (specific). T3 → dependent (generic); Q_SHARED is then normally "shared". A link that is only part-of, boundary-of, located-in, member-of, defined-relative-to or a reference frame is NOT dependence. T1 with T2 unclear → genuine ambiguity (Principle 3).

No definition supplied: write an assumed definition. Before writing it, list the senses of the label in the declared domain; if two senses lead to different BFO branches (a thing vs a characteristic of it, an activity vs its result, a region vs what occupies it), do not pick one silently (Principle 3). An assumed definition is never STATED evidence.

# PRINCIPLE 2 — THE GENUS IS QUOTED AND BECOMES A LOWER BOUND

If a definition is supplied, identify its genus: the head noun phrase of the definitional sentence ("A <genus> that …", "X is a <genus> …"), not a noun inside a relation filler ("a role OF a material entity" → genus "role"). Report it in `genus_claims` with a verbatim quote containing it.

* If the genus is a BFO class (or a documented BFO variant: fiat point/line/surface, temporal interval, amount/portion of matter), it is the ANCHOR.
* In `ontology_context` mode only, if the genus is another term of the batch, continue the chain with that term's definition until a BFO class is reached or no definition is available. In `definition_only` mode, other batch terms are never used.
* Never invent a genus, never use outside knowledge or another ontology as a genus.

The anchor is a lower bound, not a ceiling: the final class is the anchor or one of its descendants. Answers fixed by the anchor are ENTAILED. Below the anchor, continue the Keet questions with graded evidence; never refine from the label or merely because a subclass is legal. If STATED evidence contradicts the anchor, keep the anchor-compatible class and report the conflict as an ambiguity (possible source-ontology inconsistency).

# PRINCIPLE 3 — NO SILENT STOP, NO SILENT CHOICE

* Never stop at a node that still has a Keet question: answer it (its "stay" option if appropriate) or mark it `not_established`.
* Never return `entity`. Return `continuant` or `occurrent` only if the next question is genuinely CONTESTED.
* Genuine ambiguity: if the intended sense admits ontologically distinct readings (including bearer vs the role/function/quality it bears, decided by the genus when there is one), classify the best-supported reading, set `ambiguous` = true and give the other reading's class in `alternative_class`. Do not invent ambiguity when the definition resolves it.

# PRINCIPLE 4 — THE RESULT RESPECTS THE FORMAL BFO DEFINITIONS

The Keet questions are followed as given, then the result is checked against BFO:
* History only if the target is the totality of processes of one specific material entity (its whole history/life); an activity, event, task or change is never a History → keep Process.
* Process Profile only if the target is a rate, pattern or measurable aspect OF a process, not the process itself → otherwise keep Process.
* Quality only after T1 and T2 hold and the target is not a Role (exists because of external circumstances), a Disposition (manifests only under conditions) or a Process.
* An amount of matter (mass noun, no natural unit) is Material Entity; BFO has no finer class for it — do not force Object or Object Aggregate.
* Continuant Fiat Boundary: continue to the 0D/1D/2D leaf when dimensionality is STATED or ENTAILED; use the taxonomy ids, never "fiat point/line/surface".

# OUTPUT

Write a SHORT narrative, then the JSON block:

1. Pre-checks — definition status (or assumed definition and the senses considered), genus and quote, ambiguity.
2. Decision path — one line per Keet question: question id, answer, grade, evidence (quote for STATED, `Assumed: …` for COMMON).
3. Result — final class, and any Principle 4 check applied.
4. Issues — ambiguity with alternative, not_established decisions, conflicts; or "No major issue."

Do not add anything after the JSON block."""



def _load_diagram_text() -> str:
    if DIAGRAM_PATH.exists() and DIAGRAM_PATH.read_text(encoding="utf-8").strip():
        return DIAGRAM_PATH.read_text(encoding="utf-8")
    return (
        "⚠️ bfo_decision_diagram.md NOT FOUND.\n\n"
        "Place the full Keet et al. (2023 v2) BFO Classifier Decision Diagram "
        "(complete Q0–Q19+ question tree, the confidence calibration rules, and "
        "the Common Mistakes checklist) in backend/app/bfo_decision_diagram.md. "
        "Until this file has real content, the model has no diagram to follow "
        "and will fall back on its own general BFO knowledge — classifications "
        "will be far less reliable than intended."
    )


# ---------------------------------------------------------------------------
# 3. App plumbing — appended after the authors' prompt + the diagram, never mixed
#    into either. Asks for one trailing JSON block the backend can parse.
# ---------------------------------------------------------------------------
APP_JSON_INSTRUCTIONS_TEMPLATE = """## MACHINE-READABLE SUMMARY (required)

End your answer with ONE fenced JSON block, exactly in this shape, and nothing after it:

```json
{{
  "assumed_definition": "",
  "genus_claims": [
    {{"term": "<target term>", "genus": "<genus label>", "quote": "<verbatim excerpt of that term's definition containing the genus>"}}
  ],
  "steps": [
    {{"question": "Q_CONT_OCC", "answer": "persists", "grade": "common", "reasoning": "<=20 words"}},
    {{"question": "Q_DEPENDENCE", "answer": "independent", "grade": "common", "t1": "no", "t2": "no", "t3": "no", "reasoning": "<=20 words"}},
    {{"question": "Q_MATTER", "answer": "always", "grade": "stated", "quote": "<verbatim excerpt>", "reasoning": "<=20 words"}}
  ],
  "terminal_class": "material_entity",
  "ambiguous": false,
  "alternative_class": "",
  "ambiguity_message": ""
}}
```

Rules:
- "assumed_definition": your assumed definition if none was supplied, else "".
- "genus_claims": empty list if no definition was supplied or it has no explicit genus. Otherwise one object per link of the chain (Principle 2): "term" = the term whose definition is quoted (first the target), "genus" = its genus as written, "quote" = verbatim excerpt containing the genus. Stop at the first BFO class.
- "steps": one object per Keet question answered, in order, starting with Q_CONT_OCC, never skipping a question. "question" and "answer" use ONLY these ids:
{question_table}
- "grade" is exactly one of "stated", "entailed", "common", "contested". "not_established" is always "contested" and always the last step.
- A "stated" step MUST include "quote", copied verbatim from the SUPPLIED definition or context.
- The Q_DEPENDENCE step MUST include "t1", "t2" and "t3".
- "terminal_class" is exactly one of: {valid_ids}
- "alternative_class": when "ambiguous" is true, the class id of the competing reading (different from "terminal_class", not "entity"); else "".
- "ambiguity_message": 1–2 plain sentences describing the competing readings when "ambiguous" is true; else "".
- Valid JSON only: no comments, no trailing commas."""


def build_system_prompt() -> str:
    valid_ids = ", ".join(NODE_IDS)
    app_json_instructions = APP_JSON_INSTRUCTIONS_TEMPLATE.format(
        valid_ids=valid_ids, question_table=question_table_for_prompt()
    )
    return "\n\n".join(
        [
            CLASSIFIER_INSTRUCTIONS,
            "## KNOWLEDGE FILE: bfo_decision_diagram.md\n\n" + _load_diagram_text(),
            app_json_instructions,
        ]
    )