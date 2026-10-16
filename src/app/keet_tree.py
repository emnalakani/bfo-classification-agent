"""Deterministic model of the Keet et al. (2023) v2 decision diagram.

Single source of truth for:
  * the canonical question ids and answer ids the model must use in the JSON
    summary (injected into the system prompt by prompts.py);
  * the deterministic trace validator used by main.py (no LLM, no gold label,
    no domain-specific rule).

The validator replays the model's `steps` through the diagram and checks:
  1. every question id / answer id is canonical and asked at the right place;
  2. the replayed position is consistent with `terminal_class`;
  3. no silent stop on a node that still has a Keet question (tree completeness);
  4. evidence grades are legal, CONTESTED only on the last step, and
     BFO-technical descents are never graded COMMON;
  5. the dependence answer is consistent with the reported T1/T2 tests;
  6. an ambiguous result names a usable alternative class.
"""
from __future__ import annotations

from typing import Optional

NOT_ESTABLISHED = "not_established"
GRADES = {"stated", "entailed", "common", "contested"}
GRADE_SYNONYMS = {"assumed": "common", "assumption": "common"}

# Virtual positions: points in the diagram that are not BFO classes
# (e.g. "dependent, but shared or not yet decided"). Each maps to the nearest
# real BFO class, which is the correct result if the next question is CONTESTED.
VIRTUAL_PARENT = {
    "dependent": "continuant",
    "material_not_collection": "material_entity",
    "bounded_immaterial": "immaterial_entity",
    "portion_in_space": "immaterial_entity",
    "happening": "occurrent",
    "changing_process": "process",
}

# question id -> (position where it is asked, question text, {answer id: next position})
QUESTIONS: dict[str, tuple[str, str, dict[str, str]]] = {
    "Q_CONT_OCC": ("entity",
        "Is the entity present at different times (as a whole), or is it occurring in time?",
        {"persists": "continuant", "occurs": "occurrent"}),
    "Q_DEPENDENCE": ("continuant",
        "Can it exist on its own, or is it a property of / dependent on at least one bearer?",
        {"independent": "independent_continuant", "dependent": "dependent"}),
    "Q_SHARED": ("dependent",
        "May the entity be shared/copied between a number of bearers?",
        {"shared": "generically_dependent_continuant",
         "not_shared": "specifically_dependent_continuant"}),
    "Q_MATTER": ("independent_continuant",
        "Does it always have some matter (space and mass) as a part that persists in time?",
        {"always": "material_entity", "never": "immaterial_entity"}),
    "Q_COLLECTION": ("material_entity",
        "Is it a collection of disjoint self-standing single objects?",
        {"yes": "object_aggregate", "no": "material_not_collection"}),
    "Q_PROPER_PART": ("material_not_collection",
        "Is it a proper part of an object, rather than a member of?",
        {"yes": "fiat_object_part", "no": "object"}),
    "Q_BOUNDED_OR_PORTION": ("immaterial_entity",
        "Is it bounded, partially or wholly, by a material entity, or is it a portion in space?",
        {"bounded": "bounded_immaterial", "portion": "portion_in_space"}),
    "Q_BOUNDED_DIM": ("bounded_immaterial",
        "How many dimensions does this bounded immaterial entity have (0-2 or 3)?",
        {"0_2d": "continuant_fiat_boundary", "3d": "site"}),
    "Q_CFB_DIM": ("continuant_fiat_boundary",
        "How many dimensions does this continuant fiat boundary have?",
        {"0d": "zero_dimensional_continuant_fiat_boundary",
         "1d": "one_dimensional_continuant_fiat_boundary",
         "2d": "two_dimensional_continuant_fiat_boundary"}),
    "Q_PROJECTION": ("portion_in_space",
        "Is the entity a spatial projection of a portion of space-time?",
        {"yes": "spatial_region"}),
    "Q_SR_DIM": ("spatial_region",
        "How many dimensions does this spatial region have?",
        {"0d": "zero_dimensional_spatial_region",
         "1d": "one_dimensional_spatial_region",
         "2d": "two_dimensional_spatial_region",
         "3d": "three_dimensional_spatial_region"}),
    "Q_REALISATION": ("specifically_dependent_continuant",
        "Does the entity require some further process to be realised?",
        {"no": "quality", "yes": "realizable_entity"}),
    "Q_TWO_BEARERS": ("quality",
        "Does this entity require at least 2 bearers to exist?",
        {"yes": "relational_quality", "no": "quality"}),
    "Q_SITUATION": ("realizable_entity",
        "Does this entity exist because the bearer is in a specific situation?",
        {"yes": "role", "no": "disposition"}),
    "Q_FUNCTION": ("disposition",
        "Does this entity exist (by evolution or design) to realise specific processes?",
        {"yes": "function", "no": "disposition"}),
    "Q_ST_RELATION": ("occurrent",
        "What is this entity's relation to the universal spatiotemporal region?",
        {"portion_of": "spatiotemporal_region", "inhabits": "happening",
         "projection_of": "temporal_region"}),
    "Q_PROCESS_BOUNDARY": ("happening",
        "Is this the boundary of what is occurring?",
        {"yes": "process_boundary", "no": "process"}),
    "Q_SUBSTANTIAL_CHANGE": ("process",
        "Does this describe a process whereby there has been a substantial change?",
        {"yes": "changing_process", "no": "process"}),
    "Q_CHANGE_OR_RATE": ("changing_process",
        "Does this describe the rate of change or the actual changes that are occurring?",
        {"change": "history", "rate": "process_profile"}),
    "Q_MOMENT": ("temporal_region",
        "Is this a moment or does it take longer?",
        {"moment": "zero_dimensional_temporal_region",
         "longer": "one_dimensional_temporal_region"}),
}

QUESTION_AT = {pos: qid for qid, (pos, _, _) in QUESTIONS.items()}

# (question, answer) pairs that descend into a BFO-technical distinction and
# therefore can never be graded COMMON. "Stay" answers (e.g. Q_FUNCTION = no)
# are conservative and may be COMMON. The dependence question is governed by
# the T1/T2 consistency check instead.
TECHNICAL_ANSWERS: dict[str, set[str]] = {
    "Q_BOUNDED_OR_PORTION": {"bounded", "portion"},
    "Q_BOUNDED_DIM": {"0_2d", "3d"},
    "Q_CFB_DIM": {"0d", "1d", "2d"},
    "Q_PROJECTION": {"yes"},
    "Q_SR_DIM": {"0d", "1d", "2d", "3d"},
    "Q_MOMENT": {"moment", "longer"},
    "Q_FUNCTION": {"yes"},
    "Q_SUBSTANTIAL_CHANGE": {"yes"},
    "Q_CHANGE_OR_RATE": {"change", "rate"},
}

# Positions where the Keet diagram stops by itself after a "stay" answer.
STAY_ANSWERS = {("Q_TWO_BEARERS", "no"), ("Q_FUNCTION", "no"),
                ("Q_SUBSTANTIAL_CHANGE", "no")}

# v3.1 BFO conformance check: Keet's History / Process Profile branch may be
# overridden back to Process (documented agent-level extension).
CONFORMANCE_OVERRIDE = {"changing_process", "history", "process_profile"}

T_VALUES = {"yes", "no", "unclear"}

# Questions whose descent may be reverted to 'process' by the conformance check.
OVERRIDABLE_QUESTIONS = {"Q_SUBSTANTIAL_CHANGE", "Q_CHANGE_OR_RATE"}


def real_class(position: str) -> str:
    return VIRTUAL_PARENT.get(position, position)


def question_table_for_prompt() -> str:
    """Human/LLM-readable table injected into the JSON instructions."""
    lines = []
    for qid, (pos, text, answers) in QUESTIONS.items():
        tech = TECHNICAL_ANSWERS.get(qid)
        tech_note = f"  [technical answers, never COMMON: {', '.join(sorted(tech))}]" if tech else ""
        lines.append(
            f"- {qid} (asked at: {pos}) — {text}\n"
            f"  answers: {', '.join(answers)} | or {NOT_ESTABLISHED} (grade contested){tech_note}"
        )
    return "\n".join(lines)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().replace("\u2019", "'").split())


def validate_trace(
    parsed: dict,
    is_descendant,
    valid_ids: set[str],
    evidence_texts: Optional[list[str]] = None,
    anchor: Optional[str] = None,
) -> dict:
    """Replay the JSON steps through the Keet diagram.

    Returns {"valid": bool, "errors": [...], "warnings": [...],
             "replayed_position": str, "safe_class": str,
             "conformance_override": bool}.
    `is_descendant(a, b)` must return True if a == b or a is below b.
    `evidence_texts`: the SUPPLIED definition and context lines (never an
    assumed definition). A STATED step must quote them verbatim.
    `anchor`: BFO class established by the explicit genus chain, if any. A
    technical answer may be ENTAILED only when the anchor lies at or below
    the class that answer leads to.
    `safe_class`: the deepest class reached before the first error; used by
    main.py as a deterministic fallback when repairs fail.
    """
    supplied = [_norm(t) for t in (evidence_texts or []) if str(t or "").strip()]

    # Implied steps: if the model skips a question but its next question can
    # only be reached through ONE answer of the skipped one, that answer is
    # implied by the model itself. It is made explicit (and reported), never
    # invented. Generic over the whole diagram, applied hop by hop.
    warnings_pre = make_implied_steps_explicit(parsed)
    safe_position: Optional[str] = None
    errors: list[str] = []
    warnings: list[str] = list(warnings_pre)
    steps = parsed.get("steps") or []
    terminal = parsed.get("terminal_class") or ""
    position = "entity"
    last_answer: Optional[tuple[str, str]] = None
    contested_at: Optional[str] = None
    replay_broken = False

    if not steps:
        errors.append("No decision steps were returned; the Keet path cannot be checked.")

    for i, step in enumerate(steps, start=1):
        qid = str(step.get("question_id") or step.get("question") or "").strip()
        answer = str(step.get("answer") or "").strip().lower()
        grade = str(step.get("grade") or "").strip().lower()
        # The narrative format writes COMMON answers as "Assumed: ...", so the
        # model sometimes copies that word into the JSON grade. Normalize it.
        grade = GRADE_SYNONYMS.get(grade, grade)
        step["grade"] = grade
        label = f"Step {i} ({qid or '?'})"
        errors_before = len(errors)
        position_before = position

        if contested_at is not None:
            errors.append(f"{label}: a step follows a CONTESTED/not_established answer; "
                          "descent must stop at the first contested question.")
            replay_broken = True
            if safe_position is None:
                safe_position = position
            break
        if qid not in QUESTIONS:
            errors.append(f"{label}: unknown question id. Use only the canonical ids "
                          f"({', '.join(QUESTIONS)}).")
            replay_broken = True
            if safe_position is None:
                safe_position = position
            break
        expected = QUESTION_AT.get(position)
        asked_at, _, answers = QUESTIONS[qid]
        if asked_at != position:
            errors.append(f"{label}: asked out of order. At position '{position}' the next "
                          f"Keet question is {expected or '(none: this is a leaf)'}.")
            replay_broken = True
            if safe_position is None:
                safe_position = position
            break
        if grade not in GRADES:
            errors.append(f"{label}: grade '{grade}' is not one of {sorted(GRADES)}.")
        if answer == NOT_ESTABLISHED or grade == "contested":
            if not (answer == NOT_ESTABLISHED and grade == "contested"):
                errors.append(f"{label}: answer '{NOT_ESTABLISHED}' and grade 'contested' "
                              "must always be used together.")
            contested_at = position
            last_answer = (qid, NOT_ESTABLISHED)
            continue
        if answer not in answers:
            errors.append(f"{label}: answer '{answer}' is not a valid option "
                          f"({', '.join(answers)}).")
            replay_broken = True
            if safe_position is None:
                safe_position = position
            break
        if grade == "common" and answer in TECHNICAL_ANSWERS.get(qid, set()):
            if qid in OVERRIDABLE_QUESTIONS and terminal == "process":
                # The descent into History / Process Profile is discarded by the
                # BFO conformance check, so its grade does not affect the result.
                warnings.append(f"{label}: COMMON technical answer ignored because the "
                                "BFO conformance check kept the result at 'process'.")
            else:
                errors.append(f"{label}: '{answer}' is a BFO-technical distinction and cannot be "
                              "graded COMMON. Grade it STATED/ENTAILED with evidence, or answer "
                              f"{NOT_ESTABLISHED} (contested).")
        if qid == "Q_DEPENDENCE":
            t1 = str(step.get("t1") or "").strip().lower()
            t2 = str(step.get("t2") or "").strip().lower()
            t3 = str(step.get("t3") or "no").strip().lower()
            specific = t1 == "yes" and (t2 == "yes" or (t2 == "unclear" and parsed.get("ambiguous")))
            generic = t3 == "yes"
            if t1 not in T_VALUES or t2 not in T_VALUES or t3 not in T_VALUES:
                errors.append(f"{label}: the dependence step must report \"t1\", \"t2\" and "
                              "\"t3\" (yes/no/unclear) from the dependence tests.")
            elif answer == "dependent" and not (specific or generic):
                errors.append(f"{label}: 'dependent' requires T1=yes and T2=yes (specific "
                              "dependence) or T3=yes (generic dependence: content that exists "
                              f"only as carried by some bearer); got T1={t1}, T2={t2}, T3={t3}.")
            elif answer == "independent" and (generic or (t1 == "yes" and t2 == "yes")):
                errors.append(f"{label}: the tests establish dependence (T1={t1}, T2={t2}, "
                              f"T3={t3}), but the answer is 'independent'.")
            elif t1 == "yes" and t2 == "unclear" and not generic and not parsed.get("ambiguous"):
                errors.append(f"{label}: T1=yes with T2=unclear is an ambiguity; "
                              "set ambiguous=true and give the alternative class.")
        if grade == "stated":
            quote = _norm(step.get("quote") or "")
            if not supplied:
                errors.append(f"{label}: grade 'stated' is impossible here: no definition or "
                              "context was supplied (an assumed definition is not evidence).")
            elif len(quote) < 3 or not any(quote in t for t in supplied):
                errors.append(f"{label}: grade 'stated' requires a \"quote\" copied verbatim "
                              "from the supplied definition or context.")
        if grade == "entailed" and answer in TECHNICAL_ANSWERS.get(qid, set()):
            target = real_class(answers[answer])
            if not (anchor and is_descendant(anchor, target)):
                errors.append(f"{label}: '{answer}' can only be ENTAILED by an explicit genus "
                              "anchor at or below that class; none is established.")
        if safe_position is None and len(errors) > errors_before:
            safe_position = position_before
        position = answers[answer]
        last_answer = (qid, answer)

    if not replay_broken and steps:
        if contested_at is not None:
            expected_terminal = real_class(contested_at)
            if terminal != expected_terminal:
                errors.append(f"The last question was contested at '{contested_at}', so "
                              f"terminal_class must be '{expected_terminal}', not '{terminal}'.")
        elif position in VIRTUAL_PARENT:
            if position == "changing_process" and terminal == "process":
                warnings.append("BFO conformance override: Keet path reached "
                                "'changing_process', terminal kept at 'process'.")
            else:
                errors.append(f"Silent stop at '{position}': the next Keet question "
                              f"({QUESTION_AT.get(position)}) must be answered or marked "
                              f"{NOT_ESTABLISHED} (contested).")
        elif terminal != position:
            if terminal == "process" and position in CONFORMANCE_OVERRIDE:
                warnings.append("BFO conformance override: Keet path reached "
                                f"'{position}', terminal kept at 'process'.")
            else:
                errors.append(f"The replayed Keet path ends at '{position}' but "
                              f"terminal_class is '{terminal}'.")
        elif position in QUESTION_AT and last_answer not in STAY_ANSWERS:
            errors.append(f"Silent stop at '{position}': this class still has the Keet "
                          f"question {QUESTION_AT[position]}. Answer it, or mark it "
                          f"{NOT_ESTABLISHED} (contested).")

    if parsed.get("ambiguous"):
        alt = parsed.get("alternative_class") or ""
        if not alt:
            errors.append("ambiguous=true but alternative_class is empty: name the BFO class "
                          "of the competing reading.")
        elif alt not in valid_ids or alt == "entity":
            errors.append(f"alternative_class '{alt}' is not a valid informative taxonomy id.")
        elif alt == terminal:
            errors.append("alternative_class must differ from terminal_class.")

    if safe_position is None:
        safe_position = contested_at if contested_at is not None else position
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "replayed_position": position,
        "safe_class": real_class(safe_position),
        "conformance_override": any("conformance" in w for w in warnings),
    }


def make_implied_steps_explicit(parsed: dict) -> list[str]:
    steps = parsed.get("steps") or []
    notes: list[str] = []
    position = "entity"
    i = 0
    while i < len(steps):
        step = steps[i]
        qid = str(step.get("question_id") or step.get("question") or "").strip()
        answer = str(step.get("answer") or "").strip().lower()
        if qid not in QUESTIONS:
            break
        asked_at = QUESTIONS[qid][0]
        if asked_at != position:
            skipped = QUESTION_AT.get(position)
            if not skipped:
                break
            leading = [a for a, nxt in QUESTIONS[skipped][2].items() if nxt == asked_at]
            if len(leading) != 1:
                break
            grade = str(step.get("grade") or "common").lower()
            steps.insert(i, {
                "question": skipped, "answer": leading[0],
                "grade": "common" if grade == "contested" else grade,
                "options": list(QUESTIONS[skipped][2]),
                "reasoning": "Implied by the model's next question.",
                "inserted_implicit_step": True,
            })
            notes.append(f"Implied step {skipped}={leading[0]} made explicit "
                         f"(the model asked {qid} directly).")
            continue
        if answer == NOT_ESTABLISHED or answer not in QUESTIONS[qid][2]:
            break
        position = QUESTIONS[qid][2][answer]
        i += 1
    parsed["steps"] = steps
    return notes


def feedback_message(errors: list[str]) -> str:
    return (
        "Your machine-readable trace failed deterministic validation against the Keet "
        "decision diagram. Fix ONLY these problems, keep the same term, definition and "
        "evidence, and produce the full Steps 0–5 + ASCII tree + JSON again:\n- "
        + "\n- ".join(errors)
    )
