"""Tests des checks de dépendances inter-modèles (issues.py).

On vérifie les cas critiques de `_dep_issues` : cycle (Tarjan), deprecated
transitif (BFS + plus court chemin), unresolved, outdated_dependency,
dep_on_draft. Les entrées sont des `deps_out`/`status_by_key` synthétiques —
pas d'accès au FS ni à `index.json`.
"""

from __future__ import annotations

from sldt_analyzer.issues import _dep_issues


def _deps(**kwargs):
    """Sucre : construit un `deps_out` à la `name@version` -> {deps: [...]}.
    Les champs name/version/family/source sont stub car _dep_issues ne lit
    que la clé et `.deps`."""
    return {
        k: {"name": k.split("@")[0], "version": k.split("@")[1],
            "family": "SAMM", "source": "catenax", "deps": v}
        for k, v in kwargs.items()
    }


class TestUnresolvedDep:
    def test_target_not_in_catalog(self):
        deps = _deps(**{"a@1.0.0": ["missing@9.9.9"]})
        status = {"a@1.0.0": "release"}
        out = _dep_issues(deps, status)
        assert "unresolved_dep" in out["a@1.0.0"]
        assert out["a@1.0.0"]["unresolved_dep"]["detail"] == ["missing@9.9.9"]


class TestCircularDep:
    def test_two_cycle_flags_both(self):
        deps = _deps(**{
            "a@1.0.0": ["b@1.0.0"],
            "b@1.0.0": ["a@1.0.0"],
        })
        status = {"a@1.0.0": "release", "b@1.0.0": "release"}
        out = _dep_issues(deps, status)
        assert "circular_dep" in out["a@1.0.0"]
        assert "circular_dep" in out["b@1.0.0"]
        assert out["a@1.0.0"]["circular_dep"]["detail"] == ["b@1.0.0"]
        assert out["b@1.0.0"]["circular_dep"]["detail"] == ["a@1.0.0"]

    def test_self_loop(self):
        deps = _deps(**{"a@1.0.0": ["a@1.0.0"]})
        status = {"a@1.0.0": "release"}
        out = _dep_issues(deps, status)
        assert "circular_dep" in out["a@1.0.0"]

    def test_acyclic_chain_not_flagged(self):
        deps = _deps(**{
            "a@1.0.0": ["b@1.0.0"],
            "b@1.0.0": ["c@1.0.0"],
            "c@1.0.0": [],
        })
        status = {k: "release" for k in deps}
        out = _dep_issues(deps, status)
        for k in deps:
            assert "circular_dep" not in out.get(k, {})


class TestDeprecatedDep:
    def test_direct_dependency_flagged(self):
        deps = _deps(**{
            "a@1.0.0": ["b@1.0.0"],
            "b@1.0.0": [],
        })
        status = {"a@1.0.0": "release", "b@1.0.0": "deprecated"}
        out = _dep_issues(deps, status)
        assert "deprecated_dep" in out["a@1.0.0"]
        det = out["a@1.0.0"]["deprecated_dep"]["detail"]
        assert det == [{"target": "b@1.0.0", "path": ["a@1.0.0", "b@1.0.0"]}]

    def test_transitive_dependency_flagged_with_path(self):
        # A uses B uses C, C deprecated -> A AND B both flagged.
        # Shortest path provided.
        deps = _deps(**{
            "a@1.0.0": ["b@1.0.0"],
            "b@1.0.0": ["c@1.0.0"],
            "c@1.0.0": [],
        })
        status = {"a@1.0.0": "release", "b@1.0.0": "release",
                  "c@1.0.0": "deprecated"}
        out = _dep_issues(deps, status)
        # A flagged transitively, with the chain A -> B -> C in the path
        assert out["a@1.0.0"]["deprecated_dep"]["detail"] == [
            {"target": "c@1.0.0", "path": ["a@1.0.0", "b@1.0.0", "c@1.0.0"]},
        ]
        # B flagged directly
        assert out["b@1.0.0"]["deprecated_dep"]["detail"] == [
            {"target": "c@1.0.0", "path": ["b@1.0.0", "c@1.0.0"]},
        ]
        # C is itself deprecated, not flagged
        assert "deprecated_dep" not in out.get("c@1.0.0", {})

    def test_deprecated_model_not_flagged_for_itself(self):
        deps = _deps(**{
            "a@1.0.0": ["b@1.0.0"],
            "b@1.0.0": [],
        })
        # A is also deprecated, even if its dep is deprecated, don't flag it.
        status = {"a@1.0.0": "deprecated", "b@1.0.0": "deprecated"}
        out = _dep_issues(deps, status)
        assert "deprecated_dep" not in out.get("a@1.0.0", {})


class TestOutdatedDependency:
    def test_older_version_of_existing_release_flagged(self):
        deps = _deps(**{
            "a@1.0.0": ["dep@1.0.0"],
            "dep@1.0.0": [],
            "dep@2.0.0": [],
        })
        status = {"a@1.0.0": "release",
                  "dep@1.0.0": "deprecated",
                  "dep@2.0.0": "release"}
        out = _dep_issues(deps, status)
        assert out["a@1.0.0"]["outdated_dependency"]["detail"] == [
            {"target": "dep@1.0.0", "latest_release": "dep@2.0.0"},
        ]

    def test_latest_release_not_flagged(self):
        deps = _deps(**{
            "a@1.0.0": ["dep@2.0.0"],
            "dep@1.0.0": [],
            "dep@2.0.0": [],
        })
        status = {"a@1.0.0": "release",
                  "dep@1.0.0": "deprecated",
                  "dep@2.0.0": "release"}
        out = _dep_issues(deps, status)
        assert "outdated_dependency" not in out.get("a@1.0.0", {})


class TestDepOnDraft:
    def test_dep_on_draft_flagged(self):
        deps = _deps(**{
            "a@1.0.0": ["draftmodel@1.0.0"],
            "draftmodel@1.0.0": [],
        })
        status = {"a@1.0.0": "release", "draftmodel@1.0.0": "draft"}
        out = _dep_issues(deps, status)
        assert out["a@1.0.0"]["dep_on_draft"]["detail"] == ["draftmodel@1.0.0"]
