# Evidence-constrained LLM classification under BFO — reproducibility package

This repository accompanies the paper *Beyond Plausible Classification:
Evidence-Constrained LLM Reasoning for BFO Alignment* (ACM SAC 2027, KNLP
track, under review). It contains the code, prompts, datasets, raw model
outputs, manual labels and scripts needed to reproduce every table and number
reported in the paper. It is anonymised for double-blind review.

## 1. Research problem

Aligning a domain class to the Basic Formal Ontology (BFO) means deciding what
kind of entity it denotes: an object, a quality, a role, a process, and so on.
Large language models (LLMs) can read the textual definitions on which this
decision depends, but they may return a plausible class that the definition
does not support. The BFO Classifier of Keet and colleagues (version 2) turns
the decision into a sequence of questions (a decision tree), but it does not
say on what basis each question should be answered.

## 2. Approach

The **agent** asks an LLM to answer the questions of the existing BFO decision
tree one by one, under four evidence principles proposed in the paper:

1. **Evidence grades.** Every answer is graded *stated* (said by the definition,
   and then quoted word for word), *entailed* (follows from the genus of the
   definition or from earlier answers), *common* (obvious to any reader, allowed
   only for a fixed set of everyday questions) or *contested* (the agent answers
   *not established* and stops).
2. **Genus anchoring.** When the genus of the definition ("a *G* that ...") names
   a BFO class, it becomes an anchor: the result must be the anchor or one of
   its subclasses.
3. **No silent stops or choices.** The agent may stop only explicitly; an
   ambiguous definition is reported with the class of the other reading.
4. **Consistency with the formal definitions of BFO**, which are stricter than the
   tree at a few points (notably *history*).

A **deterministic validator** (no LLM call) rebuilds the path, checks eight
conditions on the model's structured trace (question order, answer vocabulary,
quotations, grade rules, dependence tests, stopping, genus, ambiguity), sends
failed checks back for at most two corrections, falls back to the deepest
valid class if the trace remains invalid, and computes the confidence (*high*,
*medium*, *low*) from the evidence grades, never from the model.

## 3. Compared configurations

All three use the same LLM, the same input (term, definition, domain) and the
same list of BFO classes.

| Paper name | `--system` value         | What the model receives |
|------------|--------------------------|-------------------------|
| B0         | `baseline_naive`         | A request to classify the term under BFO directly. |
| B1+CoT     | `baseline_diagram_cot`   | The full text of the BFO Classifier v2 decision tree, with the instruction to answer its questions one by one before giving the class. |
| Agent      | `agent`                  | The decision tree, the four evidence principles, and the deterministic validator with repairs. |

The exact prompts are in `prompts/` (regenerate them with
`python experiments/export_prompts.py`).

## 4. Datasets

### Test sets (results reported in the paper)

| File | Source | Release | Native classes | Kept | Sample | BFO classes |
|------|--------|---------|---------------:|-----:|-------:|------------:|
| `datasets/test/obi_sample.csv`  | Ontology for Biomedical Investigations (OBI) | 2026-05-08 | 4,286 | 4,201 | 140 | 9 |
| `datasets/test/envo_sample.csv` | Environment Ontology (ENVO)                  | 2026-06-26 | 4,317 | 3,467 | 139 | 14 |

Both ontologies are OBO Foundry ontologies
(<http://purl.obolibrary.org/obo/obi.owl>, <http://purl.obolibrary.org/obo/envo.owl>);
the releases of the dates above were used. For each ontology, the native
classes (identified by their IRI prefix) were kept, except deprecated classes
and classes without a real definition or without a BFO ancestor. The
**reference class** (`gold_ontology`) of a term is the most specific BFO class
it inherits through the ontology's own asserted hierarchy. Filtering counts are
in `datasets/construction/{obi,envo}_report.json`.

A stratified sample was drawn with a fixed seed (42): at most 25 terms per BFO
class for OBI and 15 for ENVO, keeping all terms of rarer classes.

Construction scripts (`datasets/construction/`):

```bash
python datasets/construction/extract_bfo_terms.py envo.owl --prefix ENVO \
       --taxonomy src/app/taxonomy.json --domain "environment and ecology"
python datasets/construction/stratified_sample.py envo_terms.csv --cap 15 --seed 42 --out envo_sample.csv
```

`datasets/construction/envo_terms.csv` (the full extracted ENVO list) is
included, and the ENVO sample can be regenerated exactly from it with the
command above. The full extracted OBI list is not included; it is regenerated
from the OBI release with `--prefix OBI --domain "biomedical investigations"`
and sampled with `--cap 25 --seed 42`.

### Development sets (methodology only, no result reported)

`datasets/development/awo.csv` (African Wildlife Ontology, 7 terms) and
`goro.csv` (Goal-Oriented Requirements Ontology, 12 terms), with the original
alignment and the alignments obtained with versions 1 and 2 of the BFO
Classifier, taken from the published data of Bernabé et al. (FOIS 2023). These
terms have no textual definition, so the agent writes an assumed definition.
They were used to design the principles before the agent was frozen; their
outputs are in `results/development/` for transparency only.

## 5. Repository structure

```
bfo-classification/
├── README.md, LICENSE, requirements.txt
├── configs/llm.env.example      settings of the reported runs (copy to .env)
├── src/app/                     agent, baselines and validator (FastAPI server)
│   ├── main.py                  classification pipeline, validator, confidence, repairs, fallback
│   ├── prompts.py               agent system prompt (four principles)
│   ├── keet_tree.py             machine-readable decision tree (20 questions) and trace checks
│   ├── genus_claims.py          genus quotation check
│   ├── baselines.py             B0 and B1+CoT prompts and calls
│   ├── openai_client.py         LLM client (OpenAI-compatible API)
│   ├── bfo_decision_diagram.md  text of the BFO Classifier v2 decision tree given to the model
│   └── taxonomy.json            BFO 2020 class hierarchy used by all scripts
├── prompts/                     rendered prompts of the three configurations
├── datasets/
│   ├── test/                    OBI and ENVO samples (reference classes)
│   ├── development/             AWO and GORO
│   └── construction/            extraction and sampling scripts, filtering reports
├── experiments/
│   ├── run_eval.py              runs a configuration on a CSV through the server
│   ├── run_experiments.sh       re-runs the three configurations on both test sets
│   ├── remove_empty.py          re-run helper for empty baseline answers (see §8)
│   └── export_prompts.py        writes prompts/
├── results/
│   ├── test/{obi,envo}_{b0,b1cot,agent}/   original outputs: raw.jsonl, per_term.csv, summary.json
│   ├── development/             AWO and GORO agent outputs
│   ├── manual_labels/           origin of each conflicting answer (Table 4)
│   └── paper/                   tables and reports recomputed by evaluation/reproduce_paper.sh
└── evaluation/
    ├── reproduce_paper.sh       recomputes every number of the paper from results/test/
    ├── compare_final.py         Table 2, McNemar tests, excluded terms
    ├── classify_disagreements.py  disagreements of the agent, cross-tabulation for Fisher's test
    ├── rq2_stats.py             stability, checks, Table 3
    ├── paper_numbers.py         remaining numbers (Table 1, Fisher, RQ3, Table 4, history, disjointness)
    └── bfo2020_disjoint_pairs.json  the 26 disjointness axioms of BFO 2020
```

## 6. Installation

Python 3.10 or later (the reported runs used Python 3.14.4).

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Recomputing the paper's numbers (§7.1) needs only the Python standard library.

## 7. Reproducing the results

All commands are run from the repository root.

### 7.1 Recompute every table and number from the saved outputs (no LLM call)

```bash
bash evaluation/reproduce_paper.sh
```

This reads only `results/test/` and `results/manual_labels/`, takes a few
seconds, is deterministic, and writes its reports to `results/paper/`:

| Paper item | Report in `results/paper/` |
|------------|----------------------------|
| Table 1, BFO classes covered | `report_other_numbers.txt` |
| Table 2, McNemar tests agent vs baselines, excluded terms | `report_table2_mcnemar.txt`, `final_table.csv`, `excluded_terms.csv` |
| McNemar tests B0 vs B1+CoT | `report_other_numbers.txt` |
| Fisher's exact tests (agent more general where a baseline conflicts) | `report_disagreements_envo.txt`, `report_other_numbers.txt` |
| Stability, unstable terms, first-attempt validity, repairs, fallbacks, effect of the checks | `report_rq2_table3.txt` |
| Table 3 (confidence levels), refining and conflicting runs by level | `report_rq2_table3.txt` |
| Disagreements of the agent (Section 5.3) | `disagreements_{OBI,ENVO}_Agent.csv`, `report_other_numbers.txt` |
| Table 4 and manual origins | `report_other_numbers.txt`, `results/manual_labels/conflicting_answers.csv` |
| *history* answers (*ice age*) | `report_other_numbers.txt` |
| Conflicting answers disjoint from the reference (Discussion) | `report_other_numbers.txt` |

Note on the two aggregation levels: Table 2 and the statistical tests use the
paired terms (answered by all configurations) and the majority class of three
runs; stability and Table 3 use all runs of all terms.

### 7.2 Re-run the LLM experiments

Re-running calls the model 2,511 times (3 configurations × 279 terms × 3 runs)
and produces new outputs, which may differ from the original ones because LLM
outputs are not deterministic (see §8).

```bash
cp configs/llm.env.example .env        # then set LLM_API_KEY in .env
cd src && uvicorn app.main:app --port 8000      # terminal 1: classification server
bash experiments/run_experiments.sh             # terminal 2, from the repository root
```

New outputs are written to `results/rerun/`, never over `results/test/`. To
recompute the paper's measures on them, pass them explicitly:

```bash
python evaluation/compare_final.py --out results/rerun/paper \
  --run OBI:B0:datasets/test/obi_sample.csv:results/rerun/obi_b0 \
  --run OBI:B1+CoT:datasets/test/obi_sample.csv:results/rerun/obi_b1cot \
  --run OBI:Agent:datasets/test/obi_sample.csv:results/rerun/obi_agent \
  --run ENVO:B0:datasets/test/envo_sample.csv:results/rerun/envo_b0 \
  --run ENVO:B1+CoT:datasets/test/envo_sample.csv:results/rerun/envo_b1cot \
  --run ENVO:Agent:datasets/test/envo_sample.csv:results/rerun/envo_agent
python evaluation/rq2_stats.py results/rerun/obi_agent results/rerun/obi_b0 results/rerun/obi_b1cot \
                               results/rerun/envo_agent results/rerun/envo_b0 results/rerun/envo_b1cot
```

A single term can be classified with
`curl -X POST localhost:8000/api/classify -H "Content-Type: application/json" -d '{"term": "forested area", "definition": "An area with a high density of trees.", "domain": "environment and ecology", "classification_mode": "definition_only"}'`.

## 8. Experimental settings and provenance

| Setting | Value |
|---------|-------|
| Model | `claude-sonnet-5-5`, through the provider's OpenAI-compatible API (`https://api.anthropic.com/v1/`) |
| Dates of the runs | 5–8 October 2026 (recorded in each `summary.json`) |
| Runs per term | 3, aggregated by majority vote; ties are broken towards the least favourable outcome |
| Input | term, definition and domain only (`definition_only` mode); no ontology context, no web access, no tools |
| Agent output limit | 4,000 tokens; at most 2 repairs after failed checks (`TRACE_MAX_REPAIRS=2`) |
| Agent prompt version | `v4.1` (recorded in every agent record) |
| Baseline output limit | first call 800 tokens for B0 and 4,000 tokens for B1+CoT; an empty, truncated or unreadable answer is repeated once with 8,000 tokens (`src/app/baselines.py`) |
| Sampling seed | 42 |

Points that a re-run should take into account, all recorded in the output files:

* **Temperature.** The client requests temperature 0. The provider rejected this
  parameter for some runs, in which case it was dropped and the provider
  default applied. Temperature 0 was sent for OBI B0 and OBI B1+CoT; the
  provider default applied for OBI agent, ENVO B0, ENVO B1+CoT and ENVO agent
  (field `meta.backend.temperature` of each `summary.json`).
* **Baseline code version.** Each baseline record carries `baseline_version`.
  B0 runs used `b1.3`, the version in this repository. B1+CoT runs used `b1.1`,
  and the B1+CoT answers that were empty in the first pass were re-run once
  with `b1.2` (`experiments/remove_empty.py`, then `run_eval.py` on the same
  folder). The first-pass outputs are kept in
  `results/test/{obi,envo}_b1cot/raw_before_rerun.jsonl`.
* **Safety filter.** The provider's safety filter blocked outputs on some
  harmless terms (10 OBI and 2 ENVO terms for B1+CoT, 2 OBI terms for B0, none
  for the agent). These terms are excluded from the paired comparisons and
  listed in `results/paper/excluded_terms.csv`.

The outputs in `results/test/` are the original outputs of the reported runs;
they were not regenerated or edited, except that the API-key hint in
`summary.json` (`meta.backend.key`) was replaced by `<redacted>`.

## 9. Manual labels

Each conflicting answer of the agent was examined by reading the definition,
the place of the term in its ontology and the agent's trace, and assigned one
of four origins: *internal* (the definition contradicts the place of the term
in the same ontology), *alignment* (that place contradicts a definition of
BFO), *vague* (the definition allows both classes) or *agent* (otherwise).
The labels and a short justification are in
`results/manual_labels/conflicting_answers.csv`.

## 10. Licences

Code: MIT (see `LICENSE`). The test and development data are derived from OBI,
ENVO and the published data of Bernabé et al. (FOIS 2023), and remain under the
licences of their sources. `evaluation/bfo2020_disjoint_pairs.json` lists axioms
of BFO 2020 (ISO/IEC 21838-2), extracted from `bfo-core.owl`.
