"""RepoLumen deterministic analysis runner (AI Frontier RKE vertical slice).

Runs RepoLumen v0.10 `analyze_source` in deterministic mode (provider=None) on a
public GitHub repository, validates the AI Semantic Manifest against the shipped
JSON schema, and writes the canonical manifest artifact plus a run receipt.

Usage (from the RepoLumen venv):
    python rl_analyze.py <source_url> <out_dir> <expected_revision_or_->
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
# RepoLumen checkout: AF_REPOLUMEN_ROOT, else a sibling directory of the lab (this script runs in RepoLumen's own venv, no lab imports).
REPOLUMEN = Path(os.environ.get("AF_REPOLUMEN_ROOT", str(Path(__file__).resolve().parents[2] / "RepoLumen")))
SCHEMA = REPOLUMEN / "repo-semantic-manifest-v0.10.schema.json"

sys.path.insert(0, str(REPOLUMEN))
os.environ["REPO_SEMANTIC_CACHE_DIR"] = str(LAB / "repolumen-cache")

from repo_semantic.knowledge_cache import KnowledgeCache  # noqa: E402
from repo_semantic.service import CACHE_ANALYZER_VERSION, analyze_source  # noqa: E402


def canonical_bytes(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    source, out_dir, expected = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    manifest = analyze_source(source, audience="developer", provider=None,
                              cache=KnowledgeCache(LAB / "repolumen-cache"))
    elapsed = time.perf_counter() - t0

    # Local temp clone paths are transient and must never become identity or leak
    # into artifacts (Paper 04 §12, §170).
    src = manifest.get("source", {})
    local_path = src.pop("local_path", None)

    import jsonschema  # from the RepoLumen venv
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(manifest), key=lambda e: list(e.path))
    revision = src.get("revision")
    data = canonical_bytes(manifest)
    digest = sha256(data)
    (out_dir / "semantic-manifest.json").write_bytes(data)
    receipt = {
        "engine": "RepoLumen",
        "engine_version": CACHE_ANALYZER_VERSION,
        "manifest_schema_version": manifest.get("schema_version"),
        "mode": "deterministic",
        "external_provider": "disabled",
        "audience": "developer",
        "source": source,
        "revision": revision,
        "expected_revision": None if expected == "-" else expected,
        "revision_matches_expected": (expected == "-") or (revision == expected),
        "started_at": started,
        "elapsed_seconds": round(elapsed, 3),
        "manifest_bytes": len(data),
        "manifest_sha256": digest,
        "schema_valid": not errors,
        "schema_error_count": len(errors),
        "schema_errors": [f"{'/'.join(str(p) for p in e.path)}: {e.message[:200]}" for e in errors[:20]],
        "knowledge_cache": manifest.get("knowledge_cache"),
        "temp_clone_path_removed_from_artifact": local_path is not None,
        "counts": {
            "evidence": len(manifest.get("evidence", [])),
            "annotations": len(manifest.get("annotations", [])),
            "symbols": len(manifest.get("code_map", {}).get("symbols", [])),
            "important_files": len(manifest.get("code_map", {}).get("important_files", [])),
            "semantic_blocks": len(manifest.get("code_map", {}).get("semantic_blocks", [])),
            "dependencies": len(manifest.get("code_map", {}).get("dependencies", [])),
            "tests": len(manifest.get("code_map", {}).get("tests", [])),
            "entrypoints": len(manifest.get("architecture", {}).get("entrypoints", [])),
            "relations": len(manifest.get("architecture", {}).get("relations", [])),
            "execution_paths": len(manifest.get("architecture", {}).get("execution_paths", [])),
        },
    }
    (out_dir / "analysis-receipt.json").write_bytes(canonical_bytes(receipt))
    print(json.dumps(receipt, ensure_ascii=False, indent=1))
    return 0 if receipt["schema_valid"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
