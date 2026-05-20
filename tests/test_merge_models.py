"""Tests de `graph.merge_models` — la fusion des `.ttl` frères d'un même
dossier de version en un unique `ParsedModel` agrégé (Feature 12).

Fixtures synthétiques : on construit des `ParsedModel` à la main, sans passer
par rdflib ni par le disque, pour pouvoir tester la logique de fusion en
isolation (cas IDTA Aspect + helpers, cas Catena-X multi-Aspect,
recalcul de `unused_property_urns` à l'échelle du groupe).
"""

from __future__ import annotations

from sldt_analyzer.graph import merge_models
from sldt_analyzer.parser import (
    Dependency, Edge, Element, ExternalRef, ParsedModel,
)


def _mk(path, source="catenax", name="batch", version="1.0.0",
        elements=None, edges=None, dependencies=None,
        used_property_urns=None, unused_property_urns=None,
        aspects_empty_properties=None):
    """ParsedModel factory with sensible defaults — only the fields we
    actually need for the merge tests."""
    return ParsedModel(
        path=path,
        namespace=f"urn:samm:io.example.{name}:{version}#",
        meta_model="SAMM 2.1.0",
        model_family="SAMM",
        model_name=name,
        model_version=version,
        source=source,
        status="release",
        elements=elements or [],
        edges=edges or [],
        dependencies=dependencies or [],
        used_property_urns=used_property_urns or [],
        unused_property_urns=unused_property_urns or [],
        aspects_empty_properties=aspects_empty_properties or [],
    )


def _el(local, kind="Property"):
    """Element factory — URN built consistently from a local name."""
    return Element(
        urn=f"urn:samm:io.example.batch:1.0.0#{local}",
        kind=kind,
        name=local,
    )


class TestTrivial:
    def test_single_model_passes_through(self):
        m = _mk("data/x/1.0.0/X.ttl", elements=[_el("Aspect", "Aspect")])
        result = merge_models([m])
        # Single model in a group : returned as-is (same identity even).
        assert len(result) == 1
        assert result[0] is m

    def test_empty_list(self):
        assert merge_models([]) == []


class TestIdtaPattern:
    """Le pattern dominant côté IDTA : 1 Aspect dans `Foo.ttl` + 1 _shared.ttl
    qui contient toutes les Properties, partageant le même namespace."""

    def test_two_files_one_namespace_merge_into_one(self):
        aspect = _el("Foo", "Aspect")
        prop = _el("bar", "Property")
        m1 = _mk("data/x/1.0.0/Foo.ttl", source="idta",
                 elements=[aspect],
                 used_property_urns=[prop.urn])  # Aspect references prop
        m2 = _mk("data/x/1.0.0/Foo_shared.ttl", source="idta",
                 elements=[prop],
                 unused_property_urns=[prop.urn])  # parser of _shared can't
                                                    # see the reference from Foo.ttl

        result = merge_models([m1, m2])
        assert len(result) == 1
        merged = result[0]
        # Union d'éléments par URN.
        kinds = {e.kind for e in merged.elements}
        assert kinds == {"Aspect", "Property"}
        assert len(merged.elements) == 2
        # CRITICAL : la Property du _shared est référencée par l'Aspect du .ttl
        # frère -> elle ne doit PLUS apparaître comme `unused_property`.
        assert merged.unused_property_urns == []
        # Le path du leader = porteur d'Aspect (Foo.ttl, pas Foo_shared.ttl).
        assert merged.path == "data/x/1.0.0/Foo.ttl"

    def test_property_truly_unused_remains_flagged(self):
        # Une Property qui n'est référencée par AUCUN .ttl du groupe doit
        # rester `unused_property` après fusion (vrai positif).
        aspect = _el("Foo", "Aspect")
        used = _el("bar", "Property")
        orphan = _el("dead", "Property")
        m1 = _mk("data/x/1.0.0/Foo.ttl",
                 elements=[aspect],
                 used_property_urns=[used.urn])
        m2 = _mk("data/x/1.0.0/Foo_shared.ttl",
                 elements=[used, orphan],
                 unused_property_urns=[used.urn, orphan.urn])

        merged = merge_models([m1, m2])[0]
        # `used` est référencée par Foo.ttl -> retirée de unused.
        # `orphan` ne l'est par personne -> reste flagged.
        assert merged.unused_property_urns == [orphan.urn]

    def test_dependencies_deduped_by_name_version(self):
        m1 = _mk("data/x/1.0.0/A.ttl", dependencies=[
            Dependency("SAMM", "shared", "3.1.0", "idta"),
        ])
        m2 = _mk("data/x/1.0.0/B.ttl", dependencies=[
            Dependency("SAMM", "shared", "3.1.0", "idta"),       # duplicate
            Dependency("SAMM", "pcf", "8.0.0", "catenax"),        # new
        ])
        merged = merge_models([m1, m2])[0]
        keys = sorted((d.name, d.version) for d in merged.dependencies)
        assert keys == [("pcf", "8.0.0"), ("shared", "3.1.0")]

    def test_edges_deduped_by_triple(self):
        m1 = _mk("data/x/1.0.0/A.ttl",
                 edges=[Edge("a", "b", "properties", False)])
        m2 = _mk("data/x/1.0.0/B.ttl",
                 edges=[
                     Edge("a", "b", "properties", False),  # duplicate
                     Edge("b", "c", "characteristic", False),  # new
                 ])
        merged = merge_models([m1, m2])[0]
        triples = sorted((e.source, e.target, e.label) for e in merged.edges)
        assert triples == [("a", "b", "properties"),
                           ("b", "c", "characteristic")]


class TestCatenaxMultiAspect:
    """Cas `material_accounting@1.0.0` : 6 .ttl, chacun avec son propre
    Aspect, partageant le namespace. La fusion produit 1 modèle à 6 Aspects."""

    def test_six_aspects_merge_into_one(self):
        aspects = [_el(f"Aspect{i}", "Aspect") for i in range(6)]
        models = [
            _mk(f"data/io.catenax.x/1.0.0/Aspect{i}.ttl",
                name="x", version="1.0.0", elements=[aspects[i]])
            for i in range(6)
        ]
        result = merge_models(models)
        assert len(result) == 1
        merged = result[0]
        aspect_names = sorted(e.name for e in merged.elements
                              if e.kind == "Aspect")
        assert aspect_names == [f"Aspect{i}" for i in range(6)]
        # Leader = 1er ParsedModel avec Aspect (= models[0]).
        assert merged.path == "data/io.catenax.x/1.0.0/Aspect0.ttl"


class TestNoAspect:
    """Cas `shared.*` : pas d'Aspect dans le groupe. Le leader retombe sur
    le premier ParsedModel."""

    def test_no_aspect_fallback_to_first(self):
        m1 = _mk("data/x/1.0.0/A.ttl",
                 elements=[_el("foo", "Property")])
        m2 = _mk("data/x/1.0.0/B.ttl",
                 elements=[_el("bar", "Entity")])
        merged = merge_models([m1, m2])[0]
        assert merged.path == "data/x/1.0.0/A.ttl"
        assert {e.kind for e in merged.elements} == {"Property", "Entity"}


class TestExternalRefsPromotion:
    """Feature 23 — quand un .ttl du groupe référence un élément défini
    dans un .ttl frère du même namespace, le parser le voit comme
    `external_ref` (cible absente de `seen` localement) ; après fusion,
    la cible est dans le modèle agrégé et la ref doit être **promue en
    Edge**. Cas dominant IDTA (Aspect dans Foo.ttl pointe vers une
    Property de Foo_shared.ttl)."""

    def test_internal_ref_promoted_to_edge(self):
        aspect = _el("Foo", "Aspect")
        prop = _el("bar", "Property")
        # Aspect dans Foo.ttl, Property dans Foo_shared.ttl. Au parse de
        # Foo.ttl, la cible :bar n'est PAS dans `seen` -> external_ref.
        aspect.external_refs.append(ExternalRef(
            predicate="properties", target_urn=prop.urn,
            target_local="bar", target_model="batch@1.0.0",
            target_source="catenax",
        ))
        m1 = _mk("data/x/1.0.0/Foo.ttl", elements=[aspect])
        m2 = _mk("data/x/1.0.0/Foo_shared.ttl", elements=[prop])

        merged = merge_models([m1, m2])[0]
        # The ref must have been promoted to an actual edge.
        labels = sorted((e.source, e.target, e.label) for e in merged.edges)
        assert labels == [(aspect.urn, prop.urn, "properties")]
        # And removed from external_refs (no longer "external").
        merged_aspect = next(e for e in merged.elements
                              if e.kind == "Aspect")
        assert merged_aspect.external_refs == []

    def test_true_external_ref_kept(self):
        # Ref vers une cible qui n'est PAS dans le groupe -> conserved
        # as external_ref (true cross-model bridge).
        aspect = _el("Foo", "Aspect")
        aspect.external_refs.append(ExternalRef(
            predicate="characteristic",
            target_urn="urn:samm:io.admin-shell.idta.shared:3.1.0#SomeChar",
            target_local="SomeChar", target_model="shared@3.1.0",
            target_source="idta",
        ))
        m1 = _mk("data/x/1.0.0/Foo.ttl", elements=[aspect])
        m2 = _mk("data/x/1.0.0/Foo_shared.ttl",
                 elements=[_el("filler", "Property")])

        merged = merge_models([m1, m2])[0]
        # No edge created (target not in the group).
        assert merged.edges == []
        # Ref preserved on the Aspect.
        merged_aspect = next(e for e in merged.elements
                              if e.kind == "Aspect")
        assert len(merged_aspect.external_refs) == 1
        assert merged_aspect.external_refs[0].target_local == "SomeChar"


class TestGrouping:
    """Vérifie que la clé de groupement est bien `(source, name, version)` :
    deux modèles avec le même `name@version` mais des sources différentes
    ne doivent PAS être fusionnés (cas hypothétique mais important pour
    l'isolation des référentiels)."""

    def test_same_name_version_different_source_not_merged(self):
        m1 = _mk("data/cx/x/1.0.0/X.ttl", source="catenax",
                 name="x", version="1.0.0",
                 elements=[_el("Aspect", "Aspect")])
        m2 = _mk("data/idta/x/1.0.0/X.ttl", source="idta",
                 name="x", version="1.0.0",
                 elements=[_el("Aspect", "Aspect")])
        result = merge_models([m1, m2])
        assert len(result) == 2
        assert {r.source for r in result} == {"catenax", "idta"}
