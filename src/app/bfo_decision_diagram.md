# BFO Classifier Decision Diagram — Keet et al. (2023), Version 2

## Purpose of this file

This file is a source-faithful textual transcription of the **revised BFO Classifier decision diagrams shown in Figure 3 (Continuant branch) and Figure 4 (Occurrent branch)** of:

Cesar Bernabé, C. Maria Keet, Zubeida C. Khan, and Zola Mahlaza, *A method to improve alignments between domain and foundational ontologies* (January 2023).

**Important:** this file contains the Keet et al. decision-diagram content only. It does **not** add LLM-agent extensions such as recursive genus resolution, NOT_ESTABLISHED, confidence strikes, label-independence rules, or custom post-processing.

The hierarchy and branch wording below preserve the wording and examples visible in Figures 3 and 4. Markdown indentation and arrows are editorial formatting only.

---

# PART A — CONTINUANT BRANCH (Figure 3)

## Entry: entity

**Question:**
> Is the entity present at different times (the whole object (not only some of its parts)), or is it occurring in time?

- **Persist in time**, e.g., a ball → **continuant** → continue with Part A.
- **Occurs in time**, e.g., kicking → **occurrent** → see Part B.

---

## From continuant

**Question:**
> Can this entity exist on its own or is this entity a property of another entity or depends on at least 1 other entity (a bearer)

- **Exist on its own**, e.g., a sandwich → **independent continuant**.
- **Dependent on another**, e.g., the scent of a perfume → ask:

### Shared/copied between bearers?

**Question:**
> May the entity be shared/ copied between a number of bearers?

- **Yes**, e.g., mp4 video file of Star trek → **generically dependent continuant**.
- **No**, e.g., the role of a reader that cannot be shared among people (bearer) → **specifically dependent continuant**.

---

# INDEPENDENT CONTINUANT

## Matter as a persistent part

**Question:**
> Does this entity always have some matter (space and mass) as a part that persists in time?

- **Never**, e.g., a cavity in a tooth → **immaterial entity**.
- **Always**, e.g., a coffee mug → **material entity**.

---

# IMMATERIAL ENTITY

## Bounded by material entity or portion in space?

**Question:**
> Is this entity bounded, partially or wholly, by a material entity or is it a portion in space?

### Branch A — Bounded

- **Bounded**, e.g., the cavity of a tooth → ask:

**Question:**
> How many dimensions does this entity have?

- **0–2 dimensions** → **continuant fiat boundary** → ask its dimensionality below.
- **3 dimensions**, e.g., the cavity of a tooth → **site**.

### Continuant fiat boundary refinement

**Question:**
> How many dimensions does this entity have?

- **0 dimensions**, e.g., the point where Egypt, Libya, and Sudan meets is a 0d continuant fiat boundary → **zero-dimensional continuant fiat boundary**.
- **1 dimension**, e.g., the border between Namibia and Zimbabwe is a 1d continuant fiat boundary → **one-dimensional continuant fiat boundary**.
- **2 dimensions**, e.g., the side of a cube, the surface of a sweater are 2D continuant fiat boundaries → **two-dimensional continuant fiat boundary**.

### Branch B — A portion in space

- **A portion**, e.g., a point in space → ask:

**Question:**
> Is the entity a spatial projection of a portion of space-time?

- **Yes** → **spatial region** → ask its dimensionality below.

### Spatial region refinement

**Question:**
> How many dimensions does this entity have?

- **0 dimensions**, e.g., a point in space → **zero-dimensional spatial region**.
- **1 dimension**, e.g., an edge of a cubed space portion in space → **one-dimensional spatial region**.
- **2 dimensions**, e.g., the surface of a sphere-shaped part of space → **two-dimensional spatial region**.
- **3 dimensions**, e.g., a sphere-shaped region of space → **three-dimensional spatial region**.

---

# MATERIAL ENTITY

## Collection or single object?

**Question:**
> Is it a collection of disjoint self-standing single objects?

- **Yes**, e.g., pack of lions, a collection of marbles → **object aggregate**.
- **No** → ask:

**Question:**
> Is it a proper part of an object, rather than a member of?

- **Yes**, e.g., the upper portion of a cake → **fiat object part**.
- **No**, e.g., a plate → **object**.

Figure note attached to **object**:
> * The entity is a maximal instance of a causal unity which it necessarily manifests.

---

# SPECIFICALLY DEPENDENT CONTINUANT

## Requires a further process to be realised?

**Question:**
> Does the entity require some further process to be realised?

- **No**, e.g., the colour of the berry → **quality**.
- **Yes**, e.g., the researcher has to do research → **realizable entity**.

### Quality refinement

From **quality**, ask:

**Question:**
> Does this entity require at least 2 bearers to exist?

- **Yes**, e.g., the family history where the 2 bearers are the patient and the family → **relational quality**.

(The figure shows Quality as the current class and the Yes refinement to Relational Quality.)

### Realizable entity refinement

From **realizable entity**, ask:

**Question:**
> Does this entity exist because the bearer is in a specific situation?

- **Yes**, e.g., a professor → **role**.
- **No**, e.g., a person has a disposition to anemia → **disposition**.

From **disposition**, ask:

**Question:**
> Does this entity exist (by evolution or design) to realise specific processes?

- **Yes**, e.g., writing function — the function of a pen to write → **function**.

(The figure shows Disposition as the current class and the Yes refinement to Function.)

---

# PART B — OCCURRENT BRANCH (Figure 4)

## Entry: entity

**Question:**
> Is the entity present at different times (the whole object (not only some of its parts)), or is it occurring in time?

- **Persist in time**, e.g., a ball → **continuant** → see Part A.
- **Occurs in time**, e.g., kicking → **occurrent** → continue below.

---

## From occurrent

**Question:**
> What is this entity's relation to the universal spatiotemporal region?

### Branch A — Portion of space and time

- **this entity is a portion of space and time (portion of)**, e.g., the spatiotemporal region occupied by a pet → **spatiotemporal region**.

### Branch B — What happens during that time

- **what happens during that time (inhabits)**, e.g., the process of stirring a drink → ask:

**Question:**
> Is this the boundary of what is occurring?

- **Yes**, e.g., the first or last step of a Kizomba dancing session → **process boundary**.
- **No**, e.g., the process of dancing during a Kizomba dancing session → **process**.

From **process**, ask:

**Question:**
> Does this describe a process whereby there has been a substantial change?

- **Yes**, e.g., a motor vehicle accident → ask:

**Question:**
> Does this describe the rate of change or the actual changes that are occurring?

- **change**, e.g., urbanisation → **history**.
- **rate of change**, e.g., rate of urbanisation in South Africa → **process profile**.

(The figure shows Process as the current class and the Yes refinement toward History / Process Profile.)

### Branch C — Part of time

- **this entity is a part of time (projection of)**, e.g., the hours of rolling blackouts → **temporal region**.

From **temporal region**, ask:

**Question:**
> Is this a moment or does it take longer?

- **a moment**, e.g., the moment the electricity switches off → **zero-dimensional temporal region**.
- **longer**, e.g., the duration of a Kizomba dancing session → **one-dimensional temporal region**.

---

# BFO LEAF CLASSES SHOWN IN GREEN IN KEET ET AL. FIGURES 3–4

The paper states that the green/shaded boxes in Figures 3 and 4 are leaf nodes in BFO v2. The visible green/shaded outputs include:

## Continuant-side leaves

- generically dependent continuant
- object aggregate
- fiat object part
- object
- site
- zero-dimensional continuant fiat boundary
- one-dimensional continuant fiat boundary
- two-dimensional continuant fiat boundary
- zero-dimensional spatial region
- one-dimensional spatial region
- two-dimensional spatial region
- three-dimensional spatial region
- relational quality
- role
- function

## Occurrent-side leaves

- spatiotemporal region
- process boundary
- history
- process profile
- zero-dimensional temporal region
- one-dimensional temporal region

---

# SOURCE BOUNDARY

This file intentionally does **not** include the following agent-level mechanisms because they are not part of Figures 3–4 of Keet et al. (2023) v2:

- recursive genus resolution
- hard genus constraints
- `NOT_ESTABLISHED`
- evidence-priority rules
- label-independence rules
- confidence/strike calibration
- custom ambiguity/decomposition rules
- custom post-diagram refinements
- domain-specific reference tables added by the LLM agent

Those mechanisms should be kept in the **agent prompt/instructions**, separate from this source-faithful decision-diagram file.
