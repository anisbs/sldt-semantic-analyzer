"""Tests des helpers de parsing (pas de FS, pas de rdflib lourde).

On vérifie surtout que les fonctions pures `_parse_model_urn` et
`_source_for_ns_segment` extraient correctement (family, name, version,
source) depuis les différents formats d'URN observés en amont (Catena-X
SAMM/BAMM, IDTA SAMM, BatteryPass external), sans crasher sur les entrées
mal formées.
"""

from __future__ import annotations

import pytest

from sldt_analyzer.parser import (
    _parse_model_urn, _read_status, _source_for_ns_segment,
)


class TestParseModelUrn:
    def test_catenax_samm(self):
        assert _parse_model_urn("urn:samm:io.catenax.batch:4.0.0#") == (
            "SAMM", "batch", "4.0.0", "catenax",
        )

    def test_catenax_bamm_legacy(self):
        assert _parse_model_urn("urn:bamm:io.catenax.batch:1.0.2#") == (
            "BAMM", "batch", "1.0.2", "catenax",
        )

    def test_idta_samm(self):
        assert _parse_model_urn(
            "urn:samm:io.admin-shell.idta.batterypass.circularity:1.0.0#"
        ) == ("SAMM", "batterypass.circularity", "1.0.0", "idta")

    def test_idta_shared(self):
        assert _parse_model_urn(
            "urn:samm:io.admin-shell.idta.shared:3.1.0#"
        ) == ("SAMM", "shared", "3.1.0", "idta")

    def test_external_batterypass(self):
        # Référentiel tiers connu (cf. README IDTA) — surfacé en `external`.
        assert _parse_model_urn(
            "urn:samm:io.BatteryPass.Performance:1.2.1#"
        ) == ("SAMM", "Performance", "1.2.1", "external")

    def test_unknown_org_prefix(self):
        # URN bien formée mais préfixe d'org inconnu -> source unknown,
        # `name` reste le segment complet.
        family, name, version, source = _parse_model_urn(
            "urn:samm:com.example.foo:1.0.0#"
        )
        assert (family, version, source) == ("SAMM", "1.0.0", "unknown")
        assert name == "com.example.foo"

    def test_malformed_urn_does_not_crash(self):
        # Entrée invalide : repli sûr (jamais d'exception).
        family, name, version, source = _parse_model_urn("garbage")
        assert (family, version, source) == ("", "", "unknown")

    def test_empty_input(self):
        assert _parse_model_urn("") == ("", "", "", "unknown")

    def test_none_input(self):
        # `None` est traité comme une chaîne vide (repli sûr).
        assert _parse_model_urn(None) == ("", "", "", "unknown")  # type: ignore[arg-type]


class TestSourceForNsSegment:
    @pytest.mark.parametrize("segment,expected_source,expected_name", [
        ("io.catenax.batch", "catenax", "batch"),
        ("io.catenax.battery.battery_pass", "catenax", "battery.battery_pass"),
        ("io.admin-shell.idta.shared", "idta", "shared"),
        ("io.admin-shell.idta.batterypass.circularity", "idta",
         "batterypass.circularity"),
        ("io.BatteryPass.Performance", "external", "Performance"),
        ("com.example.foo", "unknown", "com.example.foo"),
    ])
    def test_known_prefixes(self, segment, expected_source, expected_name):
        source, name = _source_for_ns_segment(segment)
        assert source == expected_source
        assert name == expected_name


class TestReadStatus:
    """Statut lu du metadata.json voisin, avec défaut par source : IDTA ne
    publie que du released et n'a pas de metadata.json -> `release` ; les autres
    sources restent `undefined`. Un statut explicite et valide prime toujours."""

    def test_idta_defaults_release_when_absent(self, tmp_path):
        ttl = tmp_path / "Foo.ttl"
        ttl.write_text("", encoding="utf-8")
        assert _read_status(ttl, "idta") == "release"

    def test_other_sources_default_undefined_when_absent(self, tmp_path):
        ttl = tmp_path / "Foo.ttl"
        ttl.write_text("", encoding="utf-8")
        assert _read_status(ttl, "catenax") == "undefined"
        assert _read_status(ttl) == "undefined"   # défaut source 'unknown'

    def test_explicit_status_wins_over_source_default(self, tmp_path):
        ttl = tmp_path / "Foo.ttl"
        ttl.write_text("", encoding="utf-8")
        (tmp_path / "metadata.json").write_text(
            '{"status": "deprecated"}', encoding="utf-8")
        # Même pour IDTA, un statut explicite prime sur le défaut `release`.
        assert _read_status(ttl, "idta") == "deprecated"

    def test_malformed_metadata_falls_back_to_source_default(self, tmp_path):
        ttl = tmp_path / "Foo.ttl"
        ttl.write_text("", encoding="utf-8")
        (tmp_path / "metadata.json").write_text("{not json", encoding="utf-8")
        assert _read_status(ttl, "idta") == "release"
        assert _read_status(ttl, "catenax") == "undefined"
