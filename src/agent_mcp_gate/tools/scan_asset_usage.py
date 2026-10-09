"""Deterministic reachability analysis over local JSON asset fixtures."""

import json
from collections import defaultdict, deque
from pathlib import Path


def _load(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def scan_asset_usage(root: str, level: str, include_soft: bool = False) -> dict:
    """Return one row per asset ID, including unresolved reachable references.

    `hard` belongs to the referenced asset: soft assets are not traversed by
    default. Duplicate IDs are ambiguous; eligible definitions contribute the
    union of their references so unresolved dependencies remain visible.
    `path` is the shortest root-to-ID chain; `depth` counts edges (root = 0).
    """
    root_path = Path(root).resolve(strict=True)
    if not root_path.is_dir():
        raise ValueError("root must be a directory")
    level_path = Path(level)
    if not level_path.is_absolute():
        level_path = root_path / level_path
    level_path = level_path.resolve(strict=True)
    level_data = _load(level_path)
    if not isinstance(level_data, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("asset"), str) for item in level_data
    ):
        raise ValueError("level must be an array of objects with string asset IDs")

    records = defaultdict(list)
    for asset_file in sorted(root_path.rglob("*.json")):
        if asset_file.resolve() == level_path:
            continue
        record = _load(asset_file)
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("asset"), str)
            or not isinstance(record.get("package"), str)
            or not isinstance(record.get("references"), list)
            or any(not isinstance(ref, str) for ref in record["references"])
            or type(record.get("hard")) is not bool
        ):
            raise ValueError(f"Invalid asset fixture: {asset_file.relative_to(root_path)}")
        records[record["asset"]].append(record)

    def eligible(asset_id: str) -> list[dict]:
        return [record for record in records[asset_id] if include_soft or record["hard"]]

    # Queue each ID once, preserving shortest paths even with cycles or many roots.
    paths = {}
    queue = deque()

    def enqueue(asset_id: str, chain: list[str]) -> None:
        if asset_id in paths:
            return
        if asset_id in records and not eligible(asset_id):
            return
        paths[asset_id] = chain
        queue.append(asset_id)

    for asset_id in sorted({item["asset"] for item in level_data}):
        enqueue(asset_id, [asset_id])
    while queue:
        asset_id = queue.popleft()
        references = {ref for record in eligible(asset_id) for ref in record["references"]}
        for ref in sorted(references):
            enqueue(ref, [*paths[asset_id], ref])

    assets = []
    counts = dict.fromkeys(("used", "unused", "missing", "ambiguous"), 0)
    for asset_id in sorted(set(records) | set(paths)):
        definitions = records.get(asset_id, [])
        if not definitions:
            verdict = "missing"
        elif len(definitions) > 1:
            verdict = "ambiguous"
        else:
            verdict = "used" if asset_id in paths else "unused"
        chain = paths.get(asset_id, [])
        assets.append(
            {
                "id": asset_id,
                "package": definitions[0]["package"] if len(definitions) == 1 else None,
                "verdict": verdict,
                "depth": len(chain) - 1 if chain else None,
                "path": chain,
            }
        )
        counts[verdict] += 1
    return {"assets": assets, "counts": counts}
