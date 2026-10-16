"""Verification of the genus chain claimed by the model.

Principle: the LLM proposes, the code verifies. The model identifies the genus
of the definition (a semantic judgement) and quotes the passage it relies on.
This module never interprets language; it only checks that the claim is
grounded in the supplied text and in the closed vocabulary:

  1. the quote appears verbatim in the definition of the claimed term;
  2. the claimed genus appears verbatim in that quote;
  3. the genus is a BFO class label / documented BFO alias, or (ontology_context
     mode only) another term of the batch, whose own definition then continues
     the chain;
  4. the chain is consistent (each link starts from the previous genus).

The anchor is the BFO class reached at the end of a verified chain.
"""
from __future__ import annotations

import re
from typing import Optional

# Documented BFO lexical variants used in ontology definitions (not domain data).
BFO_LEXICAL_ALIASES = {
    "fiat point": "zero_dimensional_continuant_fiat_boundary",
    "fiat line": "one_dimensional_continuant_fiat_boundary",
    "fiat surface": "two_dimensional_continuant_fiat_boundary",
    "temporal interval": "one_dimensional_temporal_region",
    "amount of matter": "material_entity",
    "portion of matter": "material_entity",
}


def _norm(text: str) -> str:
    text = str(text or "").lower()
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
            .replace("\u201c", '"').replace("\u201d", '"'))
    text = re.sub(r"['\"`]", "", text)
    text = re.sub(r"[-_]", " ", text)
    return " ".join(text.split())


def verify_genus_claims(
    claims,
    term: str,
    definition: str,
    context: dict[str, str],
    use_context: bool,
    bfo_labels: dict[str, str],
) -> dict:
    """Return {"anchor": id|None, "chain": [labels], "errors": [...], "verified": bool}.

    `bfo_labels` maps normalized BFO labels (and ids) to taxonomy ids.
    `context` maps batch term labels to their definitions.
    """
    errors: list[str] = []
    chain = [term]
    if not claims:
        return {"anchor": None, "chain": chain, "errors": [], "verified": True}
    if not isinstance(claims, list):
        return {"anchor": None, "chain": chain, "verified": False,
                "errors": ['"genus_claims" must be a list of {"term", "genus", "quote"} objects.']}

    ctx = {_norm(k): (k, v) for k, v in context.items()} if use_context else {}
    vocab = {_norm(k): v for k, v in bfo_labels.items()}
    vocab.update({_norm(k): v for k, v in BFO_LEXICAL_ALIASES.items()})

    current_term, current_def = term, definition
    anchor: Optional[str] = None
    for i, claim in enumerate(claims, start=1):
        label = f"genus_claims[{i}]"
        if not isinstance(claim, dict):
            errors.append(f"{label} must be an object with term, genus and quote.")
            break
        c_term = str(claim.get("term") or "")
        genus = str(claim.get("genus") or "")
        quote = str(claim.get("quote") or "")
        if _norm(c_term) != _norm(current_term):
            errors.append(f"{label}: expected the chain to continue from '{current_term}', "
                          f"got '{c_term}'.")
            break
        if not _norm(current_def):
            errors.append(f"{label}: no supplied definition for '{current_term}' to quote from.")
            break
        if len(_norm(quote)) < 3 or _norm(quote) not in _norm(current_def):
            errors.append(f"{label}: the quote is not a verbatim excerpt of the supplied "
                          f"definition of '{current_term}'.")
            break
        if not _norm(genus) or _norm(genus) not in _norm(quote):
            errors.append(f"{label}: the genus '{genus}' does not appear in the quoted passage.")
            break
        chain.append(genus)
        if _norm(genus) in vocab:
            anchor = vocab[_norm(genus)]
            if i != len(claims):
                errors.append(f"{label}: the chain reached a BFO class; no further link is allowed.")
            break
        if _norm(genus) in ctx:
            current_term, current_def = ctx[_norm(genus)]
            continue
        # A genus outside the closed vocabulary ends the chain without an anchor.
        if i != len(claims):
            errors.append(f"{label}: '{genus}' is neither a BFO class nor a term of the batch "
                          "(ontology_context mode); the chain cannot continue from it.")
        break

    return {"anchor": anchor, "chain": chain, "errors": errors, "verified": not errors}
