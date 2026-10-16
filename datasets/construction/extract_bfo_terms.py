"""Extract benchmark terms from a BFO-aligned OWL ontology.

For each native class (IRI prefix of the ontology): label, textual definition
(IAO:0000115), and the reference BFO class = the most specific BFO class reached
by following asserted named rdfs:subClassOf links. Terms with no definition, no
BFO ancestor, several incompatible BFO anchors, or deprecated are excluded and
counted. Output: <prefix>_terms.csv + <prefix>_report.json.

    python extract_bfo_terms.py obi.owl --prefix OBI --taxonomy taxonomy.json
"""
import argparse, csv, json, re
from collections import Counter, deque
from pathlib import Path
from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL

OBO = "http://purl.obolibrary.org/obo/"
DEF = URIRef(OBO + "IAO_0000115")
DEPRECATED = OWL.deprecated
BFO_PREFIX = OBO + "BFO_"

def norm_label(s): return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

def load_taxonomy(path):
    tree = json.loads(Path(path).read_text(encoding="utf-8"))
    parent, by_label = {}, {}
    def walk(n, p=None):
        parent[n["id"]] = p; by_label[norm_label(n["label"])] = n["id"]; by_label[n["id"]] = n["id"]
        for c in n.get("children", []): walk(c, n["id"])
    walk(tree)
    return parent, by_label

def ancestors(parent, n):
    out = []
    while n: out.append(n); n = parent.get(n)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("owl"); ap.add_argument("--prefix", required=True)
    ap.add_argument("--taxonomy", required=True); ap.add_argument("--domain", default="")
    ap.add_argument("--out", default=".")
    ap.add_argument("--imports", nargs="*", default=[],
                    help="extra OWL files merged for hierarchy/labels only (e.g. BFO, IAO)")
    a = ap.parse_args()
    parent, by_label = load_taxonomy(a.taxonomy)
    g = Graph(); g.parse(a.owl)
    for extra in a.imports:
        g.parse(extra)

    def label(u):
        for o in g.objects(u, RDFS.label):
            if isinstance(o, Literal) and (o.language in (None, "en")): return str(o).strip()
        return ""
    # BFO IRI -> taxonomy id (via BFO labels present in the file)
    bfo_map = {}
    for s in set(g.subjects(RDF.type, OWL.Class)):
        if isinstance(s, URIRef) and str(s).startswith(BFO_PREFIX):
            tid = by_label.get(norm_label(label(s)))
            if tid: bfo_map[s] = tid
    supers = {}
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            supers.setdefault(s, set()).add(o)

    def bfo_anchors(c):
        """Most specific BFO classes reachable via named superclasses."""
        found, seen, q = set(), {c}, deque([c])
        while q:
            x = q.popleft()
            for p in supers.get(x, ()):
                if p in seen: continue
                seen.add(p)
                if p in bfo_map: found.add(bfo_map[p])   # stop at first BFO class on this path
                else: q.append(p)
        # keep the most specific ones
        return {t for t in found if not any(t != u and t in ancestors(parent, u) for u in found)}

    native = OBO + a.prefix + "_"
    rows, why = [], Counter()
    classes = [s for s in set(g.subjects(RDF.type, OWL.Class)) if isinstance(s, URIRef) and str(s).startswith(native)]
    for c in classes:
        if any(str(o).lower() == "true" for o in g.objects(c, DEPRECATED)): why["deprecated"] += 1; continue
        lab = label(c)
        if not lab: why["no_label"] += 1; continue
        if lab.lower().startswith("obsolete"): why["obsolete_label"] += 1; continue
        defs = [str(o).strip() for o in g.objects(c, DEF) if str(o).strip()]
        if not defs: why["no_definition"] += 1; continue
        if len(defs[0]) < 25 or re.search(r"\b(to do|todo|revisit|tbd|placeholder)\b", defs[0], re.I):
            why["placeholder_definition"] += 1; continue
        anc = bfo_anchors(c)
        if not anc: why["no_bfo_ancestor"] += 1; continue
        if len(anc) > 1: why["several_bfo_anchors"] += 1; continue
        gold = anc.pop()
        parents = sorted(label(p) or str(p).rsplit("/", 1)[-1] for p in supers.get(c, ()))
        d = defs[0]
        rows.append({
            "term": lab, "definition": d, "domain": a.domain,
            "gold_ontology": gold, "iri": str(c), "source": a.prefix,
            "direct_parents": " | ".join(parents),
            "direct_bfo_parent": any(p in bfo_map for p in supers.get(c, ())),
            "definition_names_bfo_class": bool(re.search(r"\b(" + "|".join(
                re.escape(label(b)) for b in bfo_map if label(b)) + r")\b", d, re.I)),
        })
    why["kept"] = len(rows)
    rows.sort(key=lambda r: (r["gold_ontology"], r["term"].lower()))
    out = Path(a.out); out.mkdir(exist_ok=True)
    with (out / f"{a.prefix.lower()}_terms.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    report = {"ontology": a.prefix, "native_classes": len(classes), "filters": dict(why),
              "per_bfo_class": dict(Counter(r["gold_ontology"] for r in rows).most_common()),
              "direct_bfo_parent_share": round(sum(r["direct_bfo_parent"] for r in rows) / len(rows), 3),
              "definition_names_bfo_class_share": round(sum(r["definition_names_bfo_class"] for r in rows) / len(rows), 3),
              "bfo_classes_mapped": len(bfo_map)}
    (out / f"{a.prefix.lower()}_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__": main()
