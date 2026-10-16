import json
import re
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAIError
from pydantic import BaseModel, Field

import os

from .openai_client import (BASE_URL, CORS_ORIGINS, KEY_HINT, MAX_TOKENS, MODEL, client,
                            create_chat_completion, temperature_status)
from .prompts import BFO_TREE, FLAT_NODES, NODE_IDS, PROMPT_VERSION, build_system_prompt
from .baselines import run_baseline
from .genus_claims import verify_genus_claims
from .keet_tree import QUESTIONS as KEET_QUESTIONS
from .keet_tree import feedback_message as keet_feedback_message
from .keet_tree import validate_trace as keet_validate_trace

SYSTEM_PROMPT = build_system_prompt()

_NODE_LABEL_TO_ID = {
    re.sub(r"[^a-z0-9]+", "_", node["label"].lower()).strip("_"): node["id"]
    for node in FLAT_NODES
}
_NODE_ID_TO_LABEL = {node["id"]: node["label"] for node in FLAT_NODES}

# Definition-level BFO lexical aliases used only for deterministic genus
# resolution. These normalize established BFO wording/traditional labels to
# the canonical taxonomy ids used by this app; they are not domain gold labels.
_DEFINITION_GENUS_ALIASES = {
    # BFO lexical variants / traditional labels used in ontology definitions.
    # These are ontology-independent BFO normalizations, not dataset-specific rules.
    "fiat point": "zero_dimensional_continuant_fiat_boundary",
    "fiat line": "one_dimensional_continuant_fiat_boundary",
    "fiat surface": "two_dimensional_continuant_fiat_boundary",
    "temporal interval": "one_dimensional_temporal_region",
    # BFO's Material Entity elucidation is based on having a portion/amount of matter.
    # We accept these only when they occur in the definitional genus head.
    "amount of matter": "material_entity",
    "portion of matter": "material_entity",
}

# The LLM sometimes inserts conceptual grouping labels that are useful in prose
# but are NOT actual nodes in this app's BFO taxonomy.  They must never make an
# otherwise valid classification fail serialization.  In particular, BFO 2.x
# has Specifically Dependent Continuant and Generically Dependent Continuant as
# direct children of Continuant in the taxonomy used here; "dependent_continuant"
# is therefore a non-taxonomy helper label, not a legal path node.
_NON_TAXONOMY_PATH_MARKERS = {
    "dependent_continuant",
}

# Genus resolution deliberately uses a CLOSED vocabulary.  No web search,
# embedding search, external ontology lookup, or LLM-proposed superclass is
# allowed here.  In ontology_context mode the domain candidates are exactly the
# term labels supplied in the current batch.  In definition_only mode even those
# batch labels are excluded; only BFO labels/controlled BFO lexical aliases remain.
GENUS_VOCABULARY_POLICY = "closed_batch_bfo"


def _build_closed_genus_vocabulary(
    context_by_lower: dict,
    *,
    include_domain_terms: bool,
):
    """Build the only labels the deterministic genus resolver may consider.

    Allowed candidates:
      1. user-supplied batch term labels (ontology_context mode only);
      2. canonical BFO class labels from taxonomy.json;
      3. a small controlled table of BFO lexical aliases.

    Nothing else can enter the search space.  This makes genus extraction
    auditable and prevents hidden use of model/world knowledge.
    """
    batch_terms = (
        sorted({item["term"] for item in context_by_lower.values() if _clean_ws(item.get("term", ""))})
        if include_domain_terms
        else []
    )
    bfo_classes = sorted(set(_NODE_ID_TO_LABEL.values()))
    bfo_aliases = sorted(_DEFINITION_GENUS_ALIASES.keys())

    # Preserve one representative spelling for matching; de-duplicate
    # case-insensitively while retaining source priority.
    candidates = []
    seen = set()
    for label in [*batch_terms, *bfo_classes, *bfo_aliases]:
        key = _clean_ws(label).lower()
        if key and key not in seen:
            seen.add(key)
            candidates.append(label)

    audit = {
        "policy": GENUS_VOCABULARY_POLICY,
        "external_lookup": False,
        "batch_terms_enabled": include_domain_terms,
        "batch_term_count": len(batch_terms),
        "bfo_class_count": len(bfo_classes),
        "bfo_alias_count": len(bfo_aliases),
        "candidate_count": len(candidates),
    }
    return candidates, audit


def _normalize_bfo_node_id(value):
    """Normalize an exact taxonomy id or taxonomy label to its canonical id.

    Unknown machine-output classes are rejected instead of being silently
    attached to Entity.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value in NODE_IDS:
        return value

    key = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if key in NODE_IDS:
        return key
    return _NODE_LABEL_TO_ID.get(key)


def _build_parent_map(tree):
    parents = {}

    def walk(node, parent=None):
        if parent is not None:
            parents[node["id"]] = parent
        for child in node.get("children", []):
            walk(child, node["id"])

    walk(tree)
    return parents


_PARENT_BY_ID = _build_parent_map(BFO_TREE)


def _build_children_map(tree):
    children = {}

    def walk(node):
        children[node["id"]] = [child["id"] for child in node.get("children", [])]
        for child in node.get("children", []):
            walk(child)

    walk(tree)
    return children


_CHILDREN_BY_ID = _build_children_map(BFO_TREE)


# Keet et al. (2023) v2 direct-child distinctions.  These are ontology-general
# BFO criteria, never domain-term exceptions.  The local refinement checker is
# allowed to inspect ONLY the current class and its direct children.
_DIRECT_CHILD_REFINEMENT_GUIDANCE = {
    "entity": (
        "Is the entity present at different times as the whole entity, or does it occur/unfold in time?",
        {
            "continuant": "persists through time as the whole entity",
            "occurrent": "occurs or unfolds in time",
        },
    ),
    "continuant": (
        "Can it exist on its own, or is it dependent on bearer(s); if dependent, can it be shared/copied between bearers?",
        {
            "independent_continuant": "can exist on its own",
            "specifically_dependent_continuant": "depends on specific bearer(s) and is not shareable/copyable between bearers",
            "generically_dependent_continuant": "may be shared/copied between multiple bearers",
        },
    ),
    "independent_continuant": (
        "Does it always have some matter (space and mass) as a part that persists in time?",
        {
            "material_entity": "always has some matter as a persistent part",
            "immaterial_entity": "never has matter as a persistent part",
        },
    ),
    "material_entity": (
        "Is it a collection of disjoint self-standing objects; otherwise is it a proper part of an object rather than a member?",
        {
            "object_aggregate": "collection of disjoint self-standing single objects",
            "fiat_object_part": "proper part of an object rather than a member",
            "object": "single material object; neither an object aggregate nor a fiat object part",
        },
    ),
    "immaterial_entity": (
        "Is it bounded by material entity/entities or is it a portion in space, and what dimensionality is explicitly established?",
        {
            "site": "three-dimensional immaterial entity bounded wholly or partly by material entity/entities",
            "continuant_fiat_boundary": "0-2 dimensional continuant boundary bounded by material entity/entities",
            "spatial_region": "portion in space / spatial projection of a portion of space-time",
        },
    ),
    "continuant_fiat_boundary": (
        "How many dimensions does the boundary have?",
        {
            "zero_dimensional_continuant_fiat_boundary": "0 dimensions",
            "one_dimensional_continuant_fiat_boundary": "1 dimension",
            "two_dimensional_continuant_fiat_boundary": "2 dimensions",
        },
    ),
    "spatial_region": (
        "How many dimensions does the spatial region have?",
        {
            "zero_dimensional_spatial_region": "0 dimensions",
            "one_dimensional_spatial_region": "1 dimension",
            "two_dimensional_spatial_region": "2 dimensions",
            "three_dimensional_spatial_region": "3 dimensions",
        },
    ),
    "specifically_dependent_continuant": (
        "Does the entity require some further process to be realised?",
        {
            "quality": "does not require a further process to be realised",
            "realizable_entity": "requires a further process to be realised",
        },
    ),
    "quality": (
        "Does the quality require at least two distinct bearers to exist?",
        {"relational_quality": "requires at least two distinct bearers"},
    ),
    "realizable_entity": (
        "Does it exist because the bearer is in a specific situation?",
        {
            "role": "exists because the bearer is in a specific situation / context",
            "disposition": "not merely situation-dependent; has an internal basis",
        },
    ),
    "disposition": (
        "Does the disposition exist by evolution or design to realise specific processes?",
        {"function": "exists by evolution or design to realise specific processes"},
    ),
    "occurrent": (
        "What is its relation to the universal spatiotemporal region: portion of space-time, what happens during that time, or part of time?",
        {
            "spatiotemporal_region": "is a portion of space and time",
            "process_boundary": "is the boundary of what is occurring",
            "process": "is what happens during that time and is not merely its boundary",
            "temporal_region": "is a part of time / temporal projection",
        },
    ),
    "process": (
        "Does it describe a process with a substantial change; if so, is it the actual change or the rate/profile of change?",
        {
            "history": "describes the actual substantial change(s) occurring",
            "process_profile": "describes the rate/profile of change rather than the actual change",
        },
    ),
    "temporal_region": (
        "Is it a moment or does it take longer?",
        {
            "zero_dimensional_temporal_region": "a moment / temporal instant",
            "one_dimensional_temporal_region": "takes longer than a moment / temporal interval",
        },
    ),
}


def _is_same_or_descendant(node_id: str, ancestor_id: str) -> bool:
    current = node_id
    while current:
        if current == ancestor_id:
            return True
        current = _PARENT_BY_ID.get(current)
    return False


def _canonical_path_to(node_id: str) -> list[str]:
    """Return the taxonomy path from the top BFO branch to node_id.

    Entity itself is omitted because the frontend already renders it as the root.
    This is used only when deterministic validation must correct a model output.
    """
    path = []
    current = node_id
    while current and current != "entity":
        path.append(current)
        current = _PARENT_BY_ID.get(current)
    return list(reversed(path))


def _normalized_evidence_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _quote_is_in_evidence(quote: str, evidence_texts: list[str]) -> bool:
    q = _normalized_evidence_text(quote).strip(' "\'')
    if not q:
        return False
    return any(q in _normalized_evidence_text(text) for text in evidence_texts if text)


def _mark_not_established(parsed: dict, reason: str) -> dict:
    """Abstain instead of exporting the BFO root Entity as a useful alignment."""
    parsed["terminal_class"] = ""
    parsed["path"] = []
    parsed["classification_unresolved"] = True
    parsed["needs_review"] = True
    parsed["confidence"] = "low"
    parsed["classification_validation_status"] = "not_established"
    parsed["not_established_reason"] = reason
    parsed["narrative"] = (parsed.get("narrative") or "") + (
        "\n\n---\nSYSTEM VALIDATION NOTE: No informative BFO class below Entity "
        "could be established from the supplied evidence. The term is therefore "
        "left NOT_ESTABLISHED rather than being aligned to Entity. Reason: " + reason
    )
    return parsed


def _validate_alternative_class(parsed: dict, hard_constraint: Optional[str]) -> dict:
    """Keep the model's second-best reading only when it is a safe UI option.

    Strict, evidence-bounded policy (same as the primary class):
      - only offered when the result is flagged ambiguous;
      - must be a valid taxonomy id, not Entity, and differ from the final class;
      - if an explicit genus anchor exists and the alternative lies outside
        it, the alternative is KEPT but flagged as a conflict with the genus:
        it then signals a possible inconsistency in the source ontology
        (e.g. a label or hierarchy saying "disposition" while the definition
        says "role"). The final class itself always stays anchor-compatible.
    The alternative is never applied automatically; it is only shown to the user.
    """
    alternative = parsed.get("alternative_class") or ""
    terminal = parsed.get("terminal_class") or ""
    reason = ""

    if not alternative:
        reason = "none_proposed" if not parsed.get("alternative_class_raw") else "unknown_class"
    elif not parsed.get("ambiguous"):
        reason = "not_ambiguous"
    elif not terminal:
        reason = "primary_not_established"
    elif alternative == "entity":
        reason = "entity_not_informative"
    elif alternative == terminal:
        reason = "same_as_primary"
    conflict = bool(
        not reason and hard_constraint
        and not _is_same_or_descendant(alternative, hard_constraint)
    )

    parsed["alternative_class_status"] = (
        "rejected" if reason else ("accepted_conflicts_with_genus" if conflict else "accepted")
    )
    parsed["alternative_class_rejection_reason"] = reason
    parsed["source_conflict"] = conflict
    if conflict:
        note = (f"Note: the alternative '{alternative}' contradicts the genus of the "
                f"supplied definition ('{hard_constraint}'). This may indicate an "
                "inconsistency in the source ontology (label or hierarchy vs definition).")
        msg = (parsed.get("ambiguity_message") or "").strip()
        parsed["ambiguity_message"] = f"{msg} {note}".strip()
    if reason:
        parsed["alternative_class"] = ""
        parsed["alternative_path"] = []
    else:
        parsed["alternative_path"] = _canonical_path_to(alternative)
    return parsed


def _parse_refinement_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = list(reversed(fenced))
    if text.startswith("{") and text.endswith("}"):
        candidates.append(text)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def _attempt_direct_child_refinement(
    parsed: dict,
    *,
    term: str,
    target_definition: str,
    context_lines: list[str],
) -> dict:
    """Optional one-level refinement that can never leave the current BFO branch.

    The primary v4.1 classification remains authoritative. This second call may
    choose only ONE direct child of the current terminal class, and only when an
    exact quote from user-supplied evidence explicitly supports the Keet v2
    distinguishing criterion. Otherwise the current parent is preserved.

    This is deliberately one-level-only: it cannot jump to siblings, cousins, or
    deeper descendants, so it cannot reproduce the branch drift seen in v4.2.
    """
    parent_id = parsed.get("terminal_class") or ""
    direct_children = _CHILDREN_BY_ID.get(parent_id, [])
    guidance = _DIRECT_CHILD_REFINEMENT_GUIDANCE.get(parent_id)
    if not parent_id or not direct_children or not guidance:
        parsed.setdefault("local_refinement_status", "not_applicable")
        return parsed

    question, criteria = guidance
    evidence_texts = [target_definition or ""] + [line for line in context_lines if line]
    evidence_block = "\n".join(
        [f"TARGET TERM: {term}", f"TARGET DEFINITION: {target_definition or '[missing]'}"]
        + (["ONTOLOGY CONTEXT:"] + context_lines if context_lines else [])
    )
    child_rows = "\n".join(
        f"- {child_id}: {_NODE_ID_TO_LABEL[child_id]} — {criteria.get(child_id, '')}"
        for child_id in direct_children
    )

    prompt = f"""You are a strictly constrained BFO direct-child refinement checker.

CURRENT BFO CLASS:
- {parent_id}: {_NODE_ID_TO_LABEL[parent_id]}

ALLOWED RESULT IDS:
- keep_parent
{chr(10).join('- ' + c for c in direct_children)}

KEET ET AL. (2023) V2 DISTINCTION FOR THIS LOCAL FORK:
{question}

DIRECT CHILD CRITERIA:
{child_rows}

SUPPLIED EVIDENCE ONLY:
{evidence_block}

RULES:
1. Do NOT reclassify from scratch. The current BFO class is already accepted.
2. You may choose ONLY one DIRECT child listed above, never a sibling/cousin/deeper class.
3. Use ONLY the supplied evidence. Do not use the term label alone or outside/world knowledge.
4. Choose a child only when the supplied evidence explicitly establishes its distinguishing criterion.
5. If evidence is insufficient, negative only by absence, ambiguous, or needs background knowledge, choose keep_parent.
6. If choosing a child, evidence_quote MUST be an exact verbatim quote from the supplied definition/context.
7. Return JSON only:
{{
  "decision": "keep_parent" | "<direct_child_id>",
  "status": "established" | "not_established",
  "evidence_quote": "",
  "reason": "<=30 words"
}}
"""

    try:
        response = create_chat_completion(
            model=MODEL,
            max_tokens=min(MAX_TOKENS, 700),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Follow the local BFO fork exactly. Never leave the supplied parent branch. "
                        "Prefer keep_parent whenever the evidence does not explicitly establish a direct child."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
    except OpenAIError:
        parsed["local_refinement_status"] = "skipped_api_error"
        return parsed

    raw = response.choices[0].message.content or ""
    decision = _parse_refinement_json(raw)
    if not decision:
        parsed["local_refinement_status"] = "skipped_invalid_json"
        return parsed

    chosen = str(decision.get("decision", "keep_parent")).strip()
    status = str(decision.get("status", "not_established")).strip().lower()
    quote = str(decision.get("evidence_quote", "") or "")
    reason = str(decision.get("reason", "") or "")

    parsed["local_refinement_from"] = parent_id
    parsed["local_refinement_candidate"] = chosen
    parsed["local_refinement_reason"] = reason
    parsed["local_refinement_evidence_quote"] = quote

    if chosen == "keep_parent" or status != "established":
        parsed["local_refinement_status"] = "not_established"
        return parsed

    if chosen not in direct_children:
        parsed["local_refinement_status"] = "rejected_non_child"
        return parsed

    if not _quote_is_in_evidence(quote, evidence_texts):
        parsed["local_refinement_status"] = "rejected_untraceable_evidence"
        return parsed

    # Structural safety: exactly one edge below the accepted parent.
    parsed["terminal_class"] = chosen
    parsed["path"] = _canonical_path_to(chosen)
    parsed["local_refinement_status"] = "established"
    parsed["classification_validation_status"] = "accepted_with_direct_child_refinement"
    parsed["narrative"] = (parsed.get("narrative") or "") + (
        "\n\n---\nLOCAL REFINEMENT NOTE: The accepted class "
        f"**{_NODE_ID_TO_LABEL[parent_id]}** was refined by exactly one taxonomy edge to "
        f"**{_NODE_ID_TO_LABEL[chosen]}**. Evidence: \"{quote}\". "
        "No sibling/cousin/deeper jump was permitted."
    )
    return parsed


def _refinement_is_evidence_bounded(
    inherited_id: str, terminal_id: str, trace_report: dict
) -> bool:
    """Strict anchor-preserving policy for an explicitly established BFO genus.

    If the target's own definition or a recursively supplied domain genus reaches
    a BFO class, that class is the strongest *explicitly established* anchor.
    A more specific terminal class is not accepted merely because it is a legal
    descendant.  If the target definition explicitly names a more specific BFO
    genus, the resolver itself will establish that more specific class as the
    anchor, so no post-hoc refinement is needed here.

    This rule is intentionally ontology-independent and prevents systematic
    over-specialization (e.g. Material Entity -> Object, Process -> History)
    without hard-coding any domain term.
    """
    # v3.3: the anchor is a lower bound on generality, not a ceiling. A descent
    # below the anchor is accepted only when the model's Keet trace replays
    # deterministically and every technical descent is graded STATED/ENTAILED.
    if terminal_id == inherited_id:
        return True
    return _is_same_or_descendant(terminal_id, inherited_id) and bool(
        trace_report.get("valid")
    )


def _attach_trace_audit(parsed: dict, first: dict, final: dict, attempt: int) -> None:
    """Audit fields for evaluation (compliance before/after repair)."""
    parsed["prompt_version"] = PROMPT_VERSION
    parsed["trace_valid_first_attempt"] = bool(first.get("valid"))
    parsed["trace_first_attempt_errors"] = list(first.get("errors") or [])
    parsed["trace_repair_triggered"] = attempt > 0
    parsed["trace_validation_status"] = (
        "valid" if final.get("valid") else "invalid_after_repair"
    )
    parsed["trace_validation_errors"] = list(final.get("errors") or [])
    parsed["trace_warnings"] = list(final.get("warnings") or [])
    parsed["trace_replayed_position"] = final.get("replayed_position", "")
    if not final.get("valid"):
        parsed["needs_review"] = True
    # Readable question text for the UI, canonical id kept for analysis.
    for step in parsed.get("steps") or []:
        qid = str(step.get("question") or "").strip()
        if qid in KEET_QUESTIONS:
            step["question_id"] = qid
            step["question"] = f"{qid} — {KEET_QUESTIONS[qid][1]}"


def _apply_genus_fallback(
    parsed: dict,
    hard_constraint: str,
    genus_chain: list[str],
    attempt: int,
    reason: str,
) -> dict:
    """Use explicit inherited BFO evidence as an auditable deterministic fallback.

    This prevents a term from disappearing from the exported hierarchy when the
    LLM violates the hard genus constraint after the automatic retry. It also
    prevents unsupported refinement below an explicitly inherited superclass.
    """
    label = _NODE_ID_TO_LABEL[hard_constraint]
    parsed["terminal_class"] = hard_constraint
    parsed["path"] = _canonical_path_to(hard_constraint)
    parsed["inherited_bfo_class"] = hard_constraint
    parsed["genus_chain"] = genus_chain
    parsed["genus_validation_retry_count"] = attempt
    parsed["genus_validation_override"] = True
    parsed["genus_validation_reason"] = reason
    parsed["classification_validation_status"] = "deterministic_genus_fallback"
    parsed["confidence"] = "high"
    note = (
        "\n\n---\nSYSTEM VALIDATION NOTE: The model output was deterministically corrected "
        f"to **{label}** from the explicit supplied genus chain "
        f"({' → '.join(genus_chain)}). Reason: {reason}. "
        "No gold/reference BFO label was used."
    )
    parsed["narrative"] = (parsed.get("narrative") or "") + note
    return parsed


def _clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _normalize_definition_text(text: str) -> str:
    """Normalize harmless punctuation variants without changing semantics."""
    return (
        _clean_ws(text)
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


def _definitional_head(definition: str, target_term: str) -> Optional[str]:
    """Return the nominal genus-bearing head of a definitional sentence.

    Supported forms are deliberately structural rather than ontology-specific:
      * A/An <genus> ...
      * <target> is/are <genus phrase> ...
      * It/This entity/This class is <genus phrase> ...

    The head stops before the first relative clause (that/which/who/whose/where),
    so later relation fillers are not mistaken for the genus.
    """
    text = _normalize_definition_text(definition)
    if not text:
        return None

    payload = None
    m = re.match(r"^(?:A|An)\s+(.+)$", text, flags=re.IGNORECASE)
    if m:
        payload = m.group(1)
    else:
        subjects = [r"it", r"this\s+(?:entity|class|term|concept)"]
        target = _clean_ws(target_term)
        if target:
            subjects.insert(0, re.escape(target))
        subject_re = "(?:" + "|".join(subjects) + ")"
        m = re.match(
            rf"^{subject_re}\s+(?:is|are)\s+(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            payload = m.group(1)

    if payload is None:
        return None

    # "A <X> is a <genus> ...": the genus follows the copula when the copula
    # comes before any relative pronoun and the subject is a short noun phrase.
    rel = re.search(r"\b(?:that|which|who|whose|where)\b", payload, flags=re.IGNORECASE)
    cop = re.search(r"\s(?:is|are)\s+(?:a|an|the)?\s*", payload, flags=re.IGNORECASE)
    if cop and (not rel or cop.start() < rel.start()) and len(payload[:cop.start()].split()) <= 8:
        payload = payload[cop.end():]

    # Remove a leading article after the copula: "X is an earth material ...".
    payload = re.sub(r"^(?:a|an)\s+", "", payload, flags=re.IGNORECASE)

    # Only the definitional noun phrase is eligible for genus matching.
    payload = re.split(
        r"\b(?:that|which|who|whose|where)\b",
        payload,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return payload.strip(" ,;:.-")


# Words that end the genus noun phrase: prepositions and participles introduce
# relation fillers ("a role OF a material entity", "a role BORNE BY ...",
# "a function TO assemble ..."), which must never be mistaken for the genus.
_GENUS_NP_BOUNDARY = re.compile(
    r"\b(?:of|by|borne|in|on|for|to|from|with|within|into|at|as|between|during|"
    r"via|when|if|such|used|realized|realised|inhering|inheres|consisting|"
    r"comprising|containing|having|produced|obtained|derived|is|are)\b",
    flags=re.IGNORECASE,
)


def _genus_noun_phrase(head: str) -> str:
    """Leading noun phrase of a definitional head (before the first boundary)."""
    m = _GENUS_NP_BOUNDARY.search(head)
    return head[: m.start()].strip() if m else head


def _find_explicit_genus(
    definition: str,
    candidate_names: list[str],
    target_term: str = "",
) -> Optional[str]:
    """Find an explicitly stated known genus in the definitional head.

    This is a conservative lexical resolver, not a semantic guesser.  It only
    returns a label already known from the batch/BFO vocabulary (or a documented
    BFO lexical alias) and only when that label occurs in a syntactically
    genus-bearing noun phrase.
    """
    head = _definitional_head(definition, target_term)
    if not head:
        return None

    # Quotes in ontology definitions often mark referenced ontology terms.
    full_norm = re.sub(r"['\"]", "", head).lower()
    # Only the leading noun phrase can hold the genus; a candidate label that
    # itself contains a preposition ("amount of matter") is accepted only when
    # the head starts with it.
    head_norm = _genus_noun_phrase(full_norm)
    target_norm = re.sub(r"['\"]", "", _clean_ws(target_term)).lower()

    matches = []
    for name in {n for n in candidate_names if _clean_ws(n)}:
        name_norm = re.sub(r"['\"]", "", _clean_ws(name)).lower()
        if not name_norm or name_norm == target_norm:
            continue
        m = re.search(rf"(?<![a-z0-9_]){re.escape(name_norm)}(?![a-z0-9_])", head_norm)
        if not m and re.match(rf"{re.escape(name_norm)}(?![a-z0-9_])", full_norm):
            m = re.match(re.escape(name_norm), full_norm)
        if m:
            # Prefer the most specific/longest known label; if tied, prefer the
            # one occurring later in the nominal phrase (typically the head noun).
            matches.append((len(name_norm.split()), len(name_norm), m.start(), name))

    if not matches:
        return None
    matches.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return matches[0][3]


def _resolve_genus_chain(
    term: str,
    definition: str,
    ontology_context: list[dict],
    *,
    allow_context: bool = True,
):
    """Deterministically resolve a recursive genus chain from supplied definitions.

    Returns (display_chain, inherited_bfo_id). The resolver uses only explicit
    genus-bearing text, known batch labels, BFO labels, and documented BFO
    lexical aliases. It never uses gold/reference classifications.

    With allow_context=False, only the target definition can establish a BFO
    anchor; domain-parent definitions are not followed. This is the scientific
    "definition-only" evaluation mode.
    """
    context_source = ontology_context if allow_context else []
    context_by_lower = {
        _clean_ws(item.get("term", "")).lower(): {
            "term": _clean_ws(item.get("term", "")),
            "definition": item.get("definition", "") or "",
        }
        for item in context_source
        if _clean_ws(item.get("term", ""))
    }

    # Ensure the currently classified term/definition is authoritative.
    context_by_lower[_clean_ws(term).lower()] = {
        "term": _clean_ws(term),
        "definition": definition or "",
    }

    bfo_labels = {_NODE_ID_TO_LABEL[node_id].lower(): node_id for node_id in NODE_IDS}
    candidates, vocabulary_audit = _build_closed_genus_vocabulary(
        context_by_lower,
        include_domain_terms=allow_context,
    )

    chain = [_clean_ws(term)]
    current_definition = definition or ""
    seen = {_clean_ws(term).lower()}

    for _ in range(16):
        genus = _find_explicit_genus(current_definition, candidates, chain[-1])
        if not genus:
            return chain, None, vocabulary_audit

        chain.append(genus)
        genus_lower = _clean_ws(genus).lower()

        # Exact BFO taxonomy label.
        if genus_lower in bfo_labels:
            return chain, bfo_labels[genus_lower], vocabulary_audit

        # BFO lexical alias / traditional definitional synonym.
        alias_id = _DEFINITION_GENUS_ALIASES.get(genus_lower)
        if alias_id:
            chain.append(_NODE_ID_TO_LABEL[alias_id])
            return chain, alias_id, vocabulary_audit

        # Domain parent: recurse only if its supplied definition exists.
        parent = context_by_lower.get(genus_lower)
        if not parent or not _clean_ws(parent.get("definition", "")):
            return chain, None, vocabulary_audit
        if genus_lower in seen:
            return chain, None, vocabulary_audit
        seen.add(genus_lower)
        current_definition = parent["definition"]

    return chain, None, vocabulary_audit


# Matches the LAST ```json ... ``` fenced block in the response.
# Number of repair passes after a failed Keet trace validation (default 2).
TRACE_MAX_REPAIRS = max(0, int(os.getenv("TRACE_MAX_REPAIRS", "2")))

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

app = FastAPI(title="BFO Classifier API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OntologyContextTerm(BaseModel):
    term: str
    definition: str = ""


class ClassifyRequest(BaseModel):
    term: str
    definition: str = ""
    ontology_context: list[OntologyContextTerm] = Field(default_factory=list)
    classification_mode: Literal["definition_only", "ontology_context"] = "ontology_context"
    # Optional user-supplied domain descriptor (e.g. "African wildlife").
    # Background only: it may disambiguate the sense of the term but is never
    # evidence for a genus, a BFO class, or a local refinement.
    domain: str = Field(default="", max_length=300)


@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL, "max_tokens": MAX_TOKENS, "temperature": temperature_status(),
            "base_url": BASE_URL or "https://api.openai.com/v1 (default)", "key": KEY_HINT}


def _parse_and_validate_model_response(narrative: str, finish_reason: str):
    matches = list(JSON_BLOCK_RE.finditer(narrative))
    if not matches:
        hint = (
            " The response was cut off before the end (finish_reason='length') — "
            "raise LLM_MAX_TOKENS in backend/.env."
            if finish_reason == "length"
            else " The model did not append the required final JSON block — try again."
        )
        raise HTTPException(status_code=502, detail="Summary JSON block not found." + hint)

    try:
        parsed = json.loads(matches[-1].group(1))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail="The summary JSON block was not syntactically valid — try again.",
        )

    parsed.setdefault("assumed_definition", "")
    parsed.setdefault("genus_chain", [])
    parsed.setdefault("inherited_bfo_class", "")
    parsed.setdefault("steps", [])
    parsed.setdefault("path", [])
    parsed.setdefault("ambiguous", False)
    parsed.setdefault("ambiguity_message", "")
    parsed.setdefault("alternative_class", "")
    parsed.setdefault("confidence", "medium")

    # Second-best reading (Rule C). Normalized here; its compatibility with the
    # final class and the explicit genus anchor is checked later in finalize().
    raw_alternative = parsed.get("alternative_class") or ""
    parsed["alternative_class_raw"] = raw_alternative
    parsed["alternative_class"] = (
        _normalize_bfo_node_id(raw_alternative) or "" if raw_alternative else ""
    )

    raw_path = parsed.get("path") or []
    normalized_path = []
    ignored_virtual_path_nodes = []
    for raw_node in raw_path:
        node_id = _normalize_bfo_node_id(raw_node)
        if node_id is None:
            raw_key = re.sub(r"[^a-z0-9]+", "_", str(raw_node).lower()).strip("_")
            if raw_key in _NON_TAXONOMY_PATH_MARKERS:
                # Keep the classification instead of failing because the model
                # inserted a conceptual grouping node that is not in taxonomy.json.
                ignored_virtual_path_nodes.append(raw_key)
                continue
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Model returned unknown BFO path class {raw_node!r}. "
                    "Check taxonomy.json and the machine-readable prompt."
                ),
            )
        normalized_path.append(node_id)

    raw_terminal = parsed.get("terminal_class")
    if not raw_terminal and normalized_path:
        raw_terminal = normalized_path[-1]

    # An EMPTY terminal is handled separately from an UNKNOWN non-empty id.
    # This matters when Rule A fires: the model may correctly propose an
    # assumed definition first, but intentionally leave the final class blank
    # until the user confirms/edits that definition.  Do not turn that UI flow
    # into a false 502 error.
    if raw_terminal in (None, ""):
        parsed["path"] = []
        parsed["terminal_class"] = ""
        parsed["path_normalization_applied"] = bool(ignored_virtual_path_nodes or normalized_path)
        parsed["ignored_virtual_path_nodes"] = ignored_virtual_path_nodes
        parsed["machine_terminal_missing"] = True

        raw_inherited = parsed.get("inherited_bfo_class", "")
        if raw_inherited:
            normalized_inherited = _normalize_bfo_node_id(raw_inherited)
            parsed["inherited_bfo_class"] = normalized_inherited or ""
        else:
            parsed["inherited_bfo_class"] = ""

        parsed["narrative"] = narrative
        return parsed

    terminal_class = _normalize_bfo_node_id(raw_terminal)
    if terminal_class is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Model returned unknown terminal BFO class {raw_terminal!r}. "
                "The result was NOT assigned to Entity. Check taxonomy.json "
                "and the machine-readable prompt."
            ),
        )

    # `terminal_class` is the authoritative machine classification.  Once it is
    # validated, reconstruct the path deterministically from taxonomy.json rather
    # than trusting an LLM-authored serialization path.  This prevents harmless
    # invented intermediate labels (e.g. dependent_continuant), omitted ancestors,
    # or out-of-order path nodes from causing false classification failures.
    canonical_path = _canonical_path_to(terminal_class)
    parsed["path"] = canonical_path
    parsed["terminal_class"] = terminal_class
    parsed["path_normalization_applied"] = bool(
        ignored_virtual_path_nodes or normalized_path != canonical_path
    )
    parsed["ignored_virtual_path_nodes"] = ignored_virtual_path_nodes

    raw_inherited = parsed.get("inherited_bfo_class", "")
    if raw_inherited:
        normalized_inherited = _normalize_bfo_node_id(raw_inherited)
        parsed["inherited_bfo_class"] = normalized_inherited or ""
    else:
        parsed["inherited_bfo_class"] = ""

    parsed["narrative"] = narrative
    return parsed


class ClassificationFailed(Exception):
    """Raised when no usable classification could be produced."""


def _bfo_label_map() -> dict[str, str]:
    labels = {}
    for node in FLAT_NODES:
        labels[node["label"]] = node["id"]
        labels[node["id"].replace("_", " ")] = node["id"]
    return labels


_BFO_LABELS = _bfo_label_map()


def build_user_message(req: "ClassifyRequest") -> str:
    """The single user message sent to the model (API or manual test)."""
    use_context = req.classification_mode == "ontology_context"
    lines = [
        f"TERM: {req.term}",
        f"DEFINITION: {req.definition.strip() if req.definition.strip() else '(none supplied — write an assumed definition, Principle 1)'}",
        f"CLASSIFICATION MODE: {req.classification_mode}",
        f"DOMAIN (background only, never evidence): {_clean_ws(req.domain or '') or '(none supplied)'}",
    ]
    if use_context:
        ctx = [f"- {c.term}: {c.definition}" for c in req.ontology_context
               if c.term.strip() and c.definition.strip()]
        lines.append("ONTOLOGY CONTEXT (definitions of other batch terms; usable for the genus chain only):")
        lines.extend(ctx or ["(none)"])
    return "\n".join(lines)


def evaluate_model_output(narrative: str, finish_reason: str, req: "ClassifyRequest") -> dict:
    """Parse and verify one model answer. Pure, deterministic, no LLM call.

    Returns {"parsed", "errors", "awaiting_definition", "anchor", "trace"}.
    """
    use_context = req.classification_mode == "ontology_context"
    context_map = {c.term: c.definition for c in req.ontology_context if c.term.strip()}
    out = {"parsed": None, "errors": [], "awaiting_definition": False, "anchor": None, "trace": {}}
    try:
        parsed = _parse_and_validate_model_response(narrative, finish_reason)
    except HTTPException as e:
        out["errors"].append(str(e.detail))
        return out
    out["parsed"] = parsed
    terminal = parsed.get("terminal_class") or ""
    if not terminal:
        if parsed.get("assumed_definition"):
            out["awaiting_definition"] = True
        else:
            out["errors"].append("terminal_class is empty: give the class reached by your decision path.")
        return out

    # Principle 2: the model quotes the genus, the code verifies the quote.
    genus = verify_genus_claims(parsed.get("genus_claims"), req.term, req.definition or "",
                                context_map, use_context, _BFO_LABELS)
    out["errors"] += genus["errors"]
    anchor = genus["anchor"] if genus["verified"] else None
    out["anchor"] = anchor
    parsed["genus_chain"] = genus["chain"]
    parsed["inherited_bfo_class"] = anchor or ""
    parsed["genus_claims_verified"] = genus["verified"]
    if anchor and not _is_same_or_descendant(terminal, anchor):
        out["errors"].append(
            f"terminal_class '{terminal}' lies outside the verified genus anchor '{anchor}'. "
            "The anchor is a lower bound (Principle 2): keep the anchor-compatible class and "
            "report the competing reading as an ambiguity.")

    # Principle 1 and 3: replay the trace through the Keet diagram.
    evidence = [req.definition or ""] + (list(context_map.values()) if use_context else [])
    trace = keet_validate_trace(parsed, _is_same_or_descendant, set(NODE_IDS),
                                evidence_texts=evidence, anchor=anchor)
    out["trace"] = trace
    out["errors"] += trace["errors"]
    return out


def _confidence_from_grades(parsed: dict) -> str:
    """Deterministic confidence: high = all answers STATED/ENTAILED; medium = some
    COMMON or a justified stop (CONTESTED); low = ambiguity or failed validation."""
    if parsed.get("ambiguous") or parsed.get("trace_validation_status") != "valid":
        return "low"
    grades = {str(st.get("grade", "")).lower() for st in parsed.get("steps") or []}
    if grades & {"common", "contested"}:
        return "medium"
    return "high"


def run_classification(req: "ClassifyRequest", llm_call) -> dict:
    """The whole pipeline: one model answer, deterministic verification, repair.

    `llm_call(system_prompt, user_message) -> (text, finish_reason)` is the only
    thing that differs between the API and a manual test session.
    """
    user_message = build_user_message(req)
    feedback = ""
    first: Optional[dict] = None
    last: Optional[dict] = None
    max_attempts = 1 + TRACE_MAX_REPAIRS
    for attempt in range(max_attempts):
        content = user_message
        if feedback:
            content += ("\n\nVALIDATION FEEDBACK — your previous answer failed deterministic "
                        "checks. Fix ONLY these problems and give the full answer again:\n- "
                        + "\n- ".join(feedback))
        text, finish_reason = llm_call(SYSTEM_PROMPT, content)
        out = evaluate_model_output(text, finish_reason, req)
        if out["awaiting_definition"]:
            parsed = out["parsed"]
            parsed["classification_validation_status"] = "awaiting_definition_confirmation"
            parsed["classification_unresolved"] = True
            parsed["needs_reclassification"] = True
            parsed["domain"] = _clean_ws(req.domain or "")
            return parsed
        if first is None:
            first = out
        last = out
        if out["errors"] and attempt < max_attempts - 1:
            feedback = out["errors"]
            continue
        break

    parsed = last["parsed"]
    if parsed is None or not parsed.get("terminal_class"):
        raise ClassificationFailed(
            "No usable classification after repairs: " + "; ".join(last["errors"][:3]))

    first_parsed = first["parsed"] or {}
    parsed["prompt_version"] = PROMPT_VERSION
    parsed["classification_mode"] = req.classification_mode
    parsed["domain"] = _clean_ws(req.domain or "")
    parsed["model_first_attempt_class"] = first_parsed.get("terminal_class") or ""
    parsed["trace_valid_first_attempt"] = not first["errors"]
    parsed["trace_first_attempt_errors"] = list(first["errors"])
    parsed["trace_repair_triggered"] = first is not last
    parsed["trace_validation_errors"] = list(last["errors"])
    parsed["trace_warnings"] = list(last["trace"].get("warnings") or [])
    parsed["trace_validation_status"] = "valid" if not last["errors"] else "fallback_to_valid_prefix"
    for step in parsed.get("steps") or []:
        qid = str(step.get("question") or "").strip()
        if qid in KEET_QUESTIONS:
            step["question_id"] = qid
            step["question"] = f"{qid} — {KEET_QUESTIONS[qid][1]}"
            step.setdefault("options", list(KEET_QUESTIONS[qid][2]) + ["not_established"])

    anchor = last["anchor"]
    if last["errors"]:
        # Never export a class reached through an unverified step: keep the deepest
        # class reached before the first error (raised to the verified anchor).
        safe = last["trace"].get("safe_class") or "entity"
        if anchor and not _is_same_or_descendant(safe, anchor):
            safe = anchor
        parsed["model_terminal_class"] = parsed.get("terminal_class")
        parsed["terminal_class"] = safe
        parsed["path"] = _canonical_path_to(safe) if safe != "entity" else []
        parsed["needs_review"] = True

    parsed["confidence"] = _confidence_from_grades(parsed)
    parsed["classification_validation_status"] = (
        "accepted_model_output" if not last["errors"] else "deterministic_fallback")
    if parsed.get("terminal_class") == "entity":
        parsed = _mark_not_established(parsed, "no class below Entity was established")
    return _validate_alternative_class(parsed, anchor)


@app.post("/api/classify")
def classify(req: ClassifyRequest):
    if not req.term.strip():
        raise HTTPException(status_code=400, detail="The 'term' field is empty.")

    def llm_call(system_prompt: str, user_message: str):
        try:
            response = create_chat_completion(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
        except OpenAIError as e:
            raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")
        choice = response.choices[0]
        return choice.message.content or "", choice.finish_reason

    try:
        return run_classification(req, llm_call)
    except ClassificationFailed as e:
        raise HTTPException(status_code=502, detail=str(e))


class BaselineRequest(BaseModel):
    term: str
    definition: str = ""
    domain: str = Field(default="", max_length=300)
    variant: Literal["naive", "diagram", "naive_cot", "diagram_cot"] = "naive"


@app.post("/api/baseline")
def baseline(req: BaselineRequest):
    """Evaluation baselines B0 (naive) and B1 (Keet diagram only). See baselines.py."""
    if not req.term.strip():
        raise HTTPException(status_code=400, detail="The 'term' field is empty.")
    try:
        return run_baseline(
            create_chat_completion, MODEL, 800, req.variant,
            req.term.strip(), req.definition or "", req.domain or "",
        )
    except OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

