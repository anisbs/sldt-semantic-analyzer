"""Tests du check `property_without_characteristic` (et de l'helper `has_out`
dans `issues._element_issues`) : une Property « a » une characteristic dans
TROIS cas, et ne doit être flaggée que si AUCUN ne s'applique.

  1. Edge résolu (`samm:characteristic` vers un élément du même modèle).
  2. `external_ref` (cible cross-fichier, ex. `samm:characteristic shared:Xxx`
     qui pointe vers un modèle `shared.*` jamais fusionné).
  3. Cible ANONYME inline (`samm:characteristic [ a … ]`), captée en
     `inline_predicates` faute de nœud nommé.

Fixtures synthétiques : `ParsedModel` construits à la main (pas de rdflib /
disque), comme `test_merge_models`.
"""

from __future__ import annotations

from sldt_analyzer.issues import _element_issues
from sldt_analyzer.parser import Edge, Element, ExternalRef, ParsedModel

_NS = "urn:samm:io.example.batch:1.0.0#"


def _el(local, kind="Property", **kw):
    return Element(urn=f"{_NS}{local}", kind=kind, name=local, **kw)


def _model(elements, edges=None):
    return ParsedModel(
        path="data/x/1.0.0/X.ttl",
        namespace=_NS,
        meta_model="SAMM 2.1.0",
        model_family="SAMM",
        model_name="batch",
        model_version="1.0.0",
        source="catenax",
        status="release",
        elements=elements,
        edges=edges or [],
    )


def _flagged(model):
    """Noms des Property remontées en `property_without_characteristic`."""
    res = _element_issues(model)
    return {d["name"] for d in res["property_without_characteristic"]}


class TestPropertyWithoutCharacteristic:
    def test_resolved_edge_not_flagged(self):
        prop = _el("foo")
        char = _el("FooChar", "Characteristic")
        edge = Edge(prop.urn, char.urn, "characteristic")
        assert _flagged(_model([prop, char], [edge])) == set()

    def test_external_ref_not_flagged(self):
        # `samm:characteristic shared:DocumentIdentifierSet` -> cible hors
        # fichier, conservée en external_ref (cas Circularity / IDTA shared).
        prop = _el("dismantlingAndRemovalInformation")
        prop.external_refs.append(ExternalRef(
            predicate="characteristic",
            target_urn="urn:samm:io.admin-shell.idta.shared:3.1.0#DocumentIdentifierSet",
            target_local="DocumentIdentifierSet",
            target_model="shared@3.1.0",
            target_source="idta",
        ))
        assert _flagged(_model([prop])) == set()

    def test_inline_anonymous_characteristic_not_flagged(self):
        # `samm:characteristic [ a samm-c:SingleEntity ; … ]` -> cible bnode
        # anonyme, captée en inline_predicates (cas BusinessPartnerCertificate).
        prop = _el("type", inline_predicates=["characteristic"])
        assert _flagged(_model([prop])) == set()

    def test_genuinely_missing_is_flagged(self):
        # Aucun des trois cas : vrai positif, on flagge.
        prop = _el("orphan")
        assert _flagged(_model([prop])) == {"orphan"}


def _flagged_no_dt(model):
    """Noms des Characteristic remontées en `characteristic_without_datatype`."""
    res = _element_issues(model)
    return {d["name"] for d in res["characteristic_without_datatype"]}


class TestCharacteristicWithoutDataType:
    def test_collection_with_element_characteristic_not_flagged(self):
        # `ExtinguishingAgentsList a samm-c:List ;
        #    samm-c:elementCharacteristic :ExtinguishingAgent` -> typée par
        # son élément, pas de `samm:dataType` direct (cas Circularity).
        coll = _el("ExtinguishingAgentsList", "Characteristic",
                   types=["List"])
        elem = _el("ExtinguishingAgent", "Characteristic", types=["Characteristic"])
        edges = [Edge(coll.urn, elem.urn, "elementCharacteristic")]
        # elem lui-même a un dataType (sinon il serait flaggé) :
        edges.append(Edge(elem.urn, f"{_NS}_string", "dataType"))
        assert _flagged_no_dt(_model([coll, elem], edges)) == set()

    def test_collection_without_element_or_datatype_is_flagged(self):
        # Collection ni dataType ni elementCharacteristic : vrai positif.
        coll = _el("EmptyList", "Characteristic", types=["List"])
        assert _flagged_no_dt(_model([coll])) == {"EmptyList"}

    def test_pure_characteristic_without_datatype_is_flagged(self):
        # Characteristic « pur » sans dataType : vrai positif inchangé.
        char = _el("FooChar", "Characteristic", types=["Characteristic"])
        assert _flagged_no_dt(_model([char])) == {"FooChar"}

    def test_either_not_flagged(self):
        # `samm-c:Either` se type via samm-c:left / samm-c:right, jamais
        # `samm:dataType` (cas SubstanceCharacteristic, *Either).
        either = _el("SubstanceCharacteristic", "Characteristic",
                     types=["Either"])
        assert _flagged_no_dt(_model([either])) == set()
