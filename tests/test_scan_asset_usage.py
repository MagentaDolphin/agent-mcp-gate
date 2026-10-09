import json

import pytest

from agent_mcp_gate.tools.scan_asset_usage import scan_asset_usage


def _asset(root, asset_id, references=(), hard=True, filename=None):
    (root / (filename or f"{asset_id}.json")).write_text(
        json.dumps(
            {
                "asset": asset_id,
                "package": f"/Game/{asset_id}",
                "references": list(references),
                "hard": hard,
            }
        ),
        encoding="utf-8",
    )


def _level(root, *ids):
    (root / "level.json").write_text(json.dumps([{"asset": item} for item in ids]))


def test_cycle_missing_and_unused_with_shortest_paths(tmp_path):
    _asset(tmp_path, "a", ["b", "missing"])
    _asset(tmp_path, "b", ["a", "c"])
    _asset(tmp_path, "c")
    _asset(tmp_path, "unused")
    _level(tmp_path, "a")
    output = scan_asset_usage(str(tmp_path), "level.json")
    rows = {row["id"]: row for row in output["assets"]}
    assert output["counts"] == {"used": 3, "unused": 1, "missing": 1, "ambiguous": 0}
    assert rows["a"]["path"] == ["a"]
    assert rows["c"]["path"] == ["a", "b", "c"]
    assert rows["c"]["depth"] == 2
    assert rows["missing"] == {
        "id": "missing",
        "package": None,
        "verdict": "missing",
        "depth": 1,
        "path": ["a", "missing"],
    }
    assert rows["unused"]["path"] == [] and rows["unused"]["depth"] is None
    assert output == scan_asset_usage(str(tmp_path), str(tmp_path / "level.json"))


@pytest.mark.parametrize(
    "include_soft, used, unused, missing",
    [
        (False, 1, 2, 0),
        (True, 3, 0, 1),
    ],
)
def test_soft_dependencies_are_an_optional_transitive_branch(
    tmp_path, include_soft, used, unused, missing
):
    _asset(tmp_path, "a", ["soft"])
    _asset(tmp_path, "soft", ["child", "missing"], hard=False)
    _asset(tmp_path, "child")
    _level(tmp_path, "a")
    output = scan_asset_usage(str(tmp_path), "level.json", include_soft)
    assert output["counts"] == {
        "used": used,
        "unused": unused,
        "missing": missing,
        "ambiguous": 0,
    }


def test_ambiguous_definitions_contribute_all_references(tmp_path):
    _asset(tmp_path, "a", ["missing1"], filename="a1.json")
    _asset(tmp_path, "a", ["missing2"], filename="a2.json")
    _asset(tmp_path, "duplicate", filename="d1.json")
    _asset(tmp_path, "duplicate", filename="d2.json")
    _level(tmp_path, "a")
    output = scan_asset_usage(str(tmp_path), "level.json")
    assert output["counts"] == {"used": 0, "unused": 0, "missing": 2, "ambiguous": 2}
    assert output["assets"][0]["package"] is None
    assert output["assets"][0]["path"] == ["a"]


def test_multiple_level_roots_and_ties_use_stable_shortest_path(tmp_path):
    _asset(tmp_path, "a", ["b", "c"])
    _asset(tmp_path, "b", ["c"])
    _asset(tmp_path, "c")
    _level(tmp_path, "b", "a", "b")
    output = scan_asset_usage(str(tmp_path), "level.json")
    assert [row["id"] for row in output["assets"]] == ["a", "b", "c"]
    assert output["assets"][1]["depth"] == 0
    assert output["assets"][2]["path"] == ["a", "c"]


def test_nested_fixture_and_empty_level(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    _asset(nested, "asset")
    _level(tmp_path)
    output = scan_asset_usage(str(tmp_path), "level.json")
    assert output["counts"] == {"used": 0, "unused": 1, "missing": 0, "ambiguous": 0}


def test_malformed_fixture_fails_instead_of_silently_skipping(tmp_path):
    _level(tmp_path, "a")
    (tmp_path / "a.json").write_text('{"asset":"a"}')
    with pytest.raises(ValueError, match="Invalid asset fixture"):
        scan_asset_usage(str(tmp_path), "level.json")
