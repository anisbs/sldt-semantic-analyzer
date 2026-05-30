"""Tests du module `standards` (lien standards Catena-X ↔ modèles).

Couvre les helpers purs (clé modèle, titre, regex URN/CX, parsing des sections)
et un `build_standards` de bout en bout sur une mini Standard Library fabriquée
sous `tmp_path` (pas de réseau, pas de vrai clone).
"""

from __future__ import annotations

from sldt_analyzer.standards import (
    CX_RE, URN_RE, _derive_title, _model_key, _sections, _section_body,
    build_standards,
)


class TestModelKey:
    def test_strip_catenax(self):
        assert _model_key("io.catenax.batch", "4.0.0") == "batch@4.0.0"

    def test_strip_idta(self):
        assert _model_key("io.admin-shell.idta.shared", "3.1.0") == "shared@3.1.0"

    def test_unknown_org_kept(self):
        assert _model_key("io.BatteryPass.x", "1.0.0") == "io.BatteryPass.x@1.0.0"


class TestUrnRegex:
    def test_basic(self):
        # `findall` -> (scheme, ns, version).
        assert URN_RE.findall(
            "urn:samm:io.catenax.part_type_information:1.0.0#PartTypeInformation"
        ) == [("samm", "io.catenax.part_type_information", "1.0.0")]

    def test_captures_bamm_scheme(self):
        # Le méta-modèle legacy `urn:bamm:` doit être capté (groupe = scheme).
        assert URN_RE.findall("urn:bamm:io.catenax.serial_part:1.0.1#SerialPart") == [
            ("bamm", "io.catenax.serial_part", "1.0.1")
        ]

    def test_tolerates_space_typo(self):
        # Coquille amont observée (CX-0154) : espace après le schéma.
        assert URN_RE.findall("urn:samm: io.catenax.foo:2.3.4#X") == [
            ("samm", "io.catenax.foo", "2.3.4")
        ]

    def test_requires_full_version(self):
        # Pas de version X.Y.Z complète -> pas de match (évite les faux URN).
        assert URN_RE.findall("urn:samm:io.catenax.foo:bnode#X") == []


class TestDeriveTitle:
    def test_h1_with_cxid_wins(self):
        secs = [(1, "CX-0126 Industry Core: Part Type 2.1.1", "")]
        assert _derive_title("CX-0126-IndustryCorePartType", "CX-0126", secs) == (
            "CX-0126 Industry Core: Part Type 2.1.1"
        )

    def test_fallback_decamelcase(self):
        # Pas de H1 contenant le CX-id -> dérivé du nom de dossier.
        secs = [(1, "3. Application Programming Interfaces", "")]
        assert _derive_title(
            "CX-0143-UseCaseCircularEconomyStandard", "CX-0143", secs
        ) == "CX-0143 Use Case Circular Economy Standard"


class TestSections:
    def test_section_body_bounds(self):
        md = "# T\n## A\nbody a\n## B\nbody b\n"
        secs = _sections(md)
        assert _section_body(secs, lambda u: u == "A") == "body a"
        assert _section_body(secs, lambda u: u == "B") == "body b"


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_library(root):
    """Mini library : 1 release Saturn, 2 standards, 1 déprécié en Jupiter."""
    (root / "versions.json").write_text('["Saturn", "Jupiter"]', encoding="utf-8")
    sat = root / "versioned_docs" / "version-Saturn" / "standards"
    # CX-0100 : 1 modèle, référence CX-0013 (déprécié) + CX-0200 (actif).
    _write(sat / "CX-0100-Foo" / "CX-0100-Foo.md", (
        "# CX-0100 Foo Standard 1.0.0\n\n"
        "## 2 Normative References\n"
        "- CX-0200 Bar Standard\n\n"
        "## 3 Aspect Models\n"
        "`urn:samm:io.catenax.foo:1.0.0#Foo`\n\n"
        "Also see CX-0013 Identity (deprecated).\n"
    ))
    # CX-0200 : pas de modèle, multi-fichiers (titre via fallback).
    _write(sat / "CX-0200-BarBaz" / "intro.md", "# Introduction\n\ntext\n")
    _write(sat / "CX-0200-BarBaz" / "Changelog.md", "# Changelog\nignored CX-9999\n")
    # Changelog Saturn : pas de dépréciation. Jupiter : CX-0013 déprécié.
    _write(sat / "changelog.md", "# Changelog\n## A) Added\n- nothing\n")
    jup = root / "versioned_docs" / "version-Jupiter" / "standards"
    _write(jup / "changelog.md", (
        "# Changelog\n## C) Deprecated Standards\n\n"
        "| CX-Nr. | Standard Name | Reason for Deprecation |\n"
        "|---|---|---|\n"
        "| CX-0013 | Identity of Member Company | consolidation |\n"
    ))


class TestBuildStandards:
    def test_absent_library(self, tmp_path):
        assert build_standards(tmp_path / "nope") is None

    def test_end_to_end(self, tmp_path):
        _make_library(tmp_path)
        out = build_standards(tmp_path)
        assert out is not None
        assert out["release"] == "Saturn"
        assert set(out["standards"]) == {"CX-0100", "CX-0200"}

        foo = out["standards"]["CX-0100"]
        assert foo["title"] == "CX-0100 Foo Standard 1.0.0"
        assert foo["semantic_models"] == ["foo@1.0.0"]
        assert foo["normative"] == ["CX-0200"]
        # non_normative = tout CX cité hors normative : CX-0013 (cité en texte
        # libre) y tombe désormais (ancien "other" fusionné).
        assert foo["non_normative"] == ["CX-0013"]
        # plus de champ referenced_standards (schéma simplifié).
        assert "referenced_standards" not in foo
        # CX-0013 est déprécié (depuis Jupiter) -> deprecated_refs.
        assert foo["deprecated_refs"] == ["CX-0013"]

        # Changelog.md d'un standard est ignoré (pas de CX-9999 capté).
        assert "CX-9999" not in out["standards"]["CX-0200"]["non_normative"]
        # Titre fallback (pas de H1 avec le CX-id).
        assert out["standards"]["CX-0200"]["title"] == "CX-0200 Bar Baz"

        # Table dépréciés + carte inverse modèle -> standards.
        assert out["deprecated_standards"]["CX-0013"]["deprecated_in"] == "Jupiter"
        assert out["models"] == {"foo@1.0.0": ["CX-0100"]}

    def test_standard_issues(self, tmp_path):
        """Issues standard : `deprecated_models` reste catalogue-driven (statut).
        `bamm_models` vient du schéma `urn:bamm:` (autoritatif) — un modèle cité
        en `urn:samm:` n'est JAMAIS BAMM, même si le catalogue le prétend.
        `standard_issue_types` est exposé."""
        _make_library(tmp_path)
        # foo est cité en `urn:samm:` -> pas BAMM ; sans catalogue, pas de
        # statut connu -> pas d'issue modèle, mais les champs sont exposés.
        bare = build_standards(tmp_path)
        assert bare["standards"]["CX-0100"]["deprecated_models"] == []
        assert bare["standards"]["CX-0100"]["bamm_models"] == []
        assert [t["id"] for t in bare["standard_issue_types"]] == [
            "deprecated-standard-ref", "deprecated-model-ref", "bamm-model-ref"]
        # Catalogue : statut déprécié -> deprecated_models. La `family` BAMM du
        # catalogue est IGNORÉE pour bamm_models (foo est cité en samm).
        catalog = {"foo@1.0.0": {"status": "deprecated", "family": "BAMM"}}
        out = build_standards(tmp_path, catalog=catalog)
        foo = out["standards"]["CX-0100"]
        assert foo["deprecated_models"] == ["foo@1.0.0"]
        assert foo["bamm_models"] == []
        # Modèle hors catalogue (statut inconnu) -> jamais flaggé déprécié.
        catalog2 = {"other@9.9.9": {"status": "deprecated"}}
        out2 = build_standards(tmp_path, catalog=catalog2)
        assert out2["standards"]["CX-0100"]["deprecated_models"] == []

    def test_bamm_model_ref_from_scheme(self, tmp_path):
        """Un modèle cité via `urn:bamm:` tombe dans `bamm_models` ET
        `semantic_models`, sans aucun catalogue (le schéma est autoritatif)."""
        (tmp_path / "versions.json").write_text('["Saturn"]', encoding="utf-8")
        sat = tmp_path / "versioned_docs" / "version-Saturn" / "standards"
        _write(sat / "CX-0400-Legacy" / "CX-0400-Legacy.md", (
            "# CX-0400 Legacy Standard 1.0.0\n\n"
            "## 3 Aspect Models\n"
            "`urn:samm:io.catenax.modern:2.0.0#Modern`\n"
            "`urn:bamm:io.catenax.legacy:1.0.0#Legacy`\n"
        ))
        out = build_standards(tmp_path)
        std = out["standards"]["CX-0400"]
        assert std["semantic_models"] == ["legacy@1.0.0", "modern@2.0.0"]
        assert std["bamm_models"] == ["legacy@1.0.0"]

    def test_non_ascii_dash_in_cx_id(self, tmp_path):
        """Coquille amont (vue en réel sur CX-0005 : « CX–0002 ») : un id CX
        écrit avec un en-dash U+2013 au lieu du tiret ASCII doit être capté ET
        normalisé en `CX-XXXX` dans la section normative."""
        endash = chr(0x2013)
        (tmp_path / "versions.json").write_text('["Saturn"]', encoding="utf-8")
        sat = tmp_path / "versioned_docs" / "version-Saturn" / "standards"
        _write(sat / "CX-0300-Dash" / "CX-0300-Dash.md", (
            "# CX-0300 Dash Standard 1.0.0\n\n"
            "## 2 Normative References\n"
            f"- CX{endash}0002 EnDash Ref\n"
            "- CX-0018 Ascii Ref\n"
        ))
        out = build_standards(tmp_path)
        std = out["standards"]["CX-0300"]
        assert std["normative"] == ["CX-0002", "CX-0018"]
