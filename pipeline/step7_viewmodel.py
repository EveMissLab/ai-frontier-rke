"""Step 7 — repository page view model: validated canonical Markdown → one JSON the portal renders.

The rendered page is a view of the ledger, never the source of truth (Paper 08 §2). This step reads
only validated artifacts (canonical Markdown with its front matter, the receipts, taxonomy names from
the catalog) and writes `<slice>/canonical/repository-view.json`. It publishes nothing: the view model
carries `publication.status = "unpublished"` until step 8 records a human-approved publication event.

Usage: python step7_viewmodel.py <slice_slug>
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

import yaml

from af_common import load_json, open_store, rel_ref, slice_dir, utc_now, write_json
from step5_validate import VALIDATOR_VERSION

VIEW_SCHEMA = "ai-frontier-repository-view/v0.1"
NOTICES_VERSION = "attribution-license-templates/v0.1"
# ATTRIBUTION_LICENSE_TEMPLATES.md v0.1, verbatim.
NOTICES = {
    "rights": "Original repository hosted on GitHub. Repository source code, documentation, names, media, and related project materials remain subject to the rights of their respective authors, contributors, and other rights holders and to applicable repository license terms.",
    "platform": "GitHub is the source hosting platform for the linked repository. GitHub and related marks are trademarks of GitHub, Inc. EVEMISS Technology is not affiliated with or endorsed by GitHub unless explicitly stated otherwise.",
    "ai_process": "This page was produced using revision-aware repository analysis and AI-assisted editorial tooling. Technical claims are tied to the analyzed repository revision and may be revalidated when the source repository changes.",
    "no_license": "No license detected. This page provides analysis and links to the original repository. Source reuse is handled conservatively.",
    "unresolved_license": "License status is not yet reliably resolved. Source-code reproduction is restricted until the license state is clarified.",
}
# Canonical sections that the page lays out itself (hero, aside, details) rather than as article body.
CHROME_SECTIONS = {"Repository", "Uncertainties reported by the writer", "Claims and evidence", "Notices"}


def split_canonical(md_text: str) -> tuple[dict, str, str, list[dict]]:
    """front matter, H1 title, summary blockquote, [{id, heading, markdown}] in document order."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", md_text, flags=re.S)
    assert m, "canonical Markdown has no front matter"
    fm, body = yaml.safe_load(m.group(1)), m.group(2)
    title_m = re.search(r"^# (.+)$", body, flags=re.M)
    title = title_m.group(1).strip() if title_m else fm.get("title")
    quote = re.search(r"^> (.+?)(?:\n\n|\n(?!>))", body, flags=re.S | re.M)
    summary = " ".join(line.lstrip("> ").strip() for line in quote.group(1).splitlines()) if quote else ""
    parts = re.split(r"^## (.+)$", body, flags=re.M)  # [preamble, h2, text, h2, text, ...]
    sections = []
    for i in range(1, len(parts), 2):
        heading, text = parts[i].strip(), parts[i + 1].strip("\n")
        sid = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
        sections.append({"id": sid, "heading": heading, "markdown": text.strip() + "\n"})
    return fm, title, summary, sections


def main(slug: str) -> int:
    sdir = slice_dir(slug)
    reg = load_json(sdir / "registration-receipt.json")
    gh = load_json(sdir / "artifacts" / "github" / "repo.json")
    val = load_json(sdir / "validation-receipt.json")
    assert val["hard_gate_pass"], "view model only from a validated asset revision (validation-receipt hard_gate_pass is false)"
    final = load_json(sdir / "worker_runs" / "final-draft-bundle.json")
    canonical_path = sdir / "canonical" / "overview.md"
    fm, title, summary, sections = split_canonical(canonical_path.read_text(encoding="utf-8"))
    assert fm["publication_status"] == "unpublished" and fm["verifier_status"] == "pass"

    store = open_store()
    cat_names = {r["values"]["af_category_slug"]: r["values"]["af_display_name"] for r in store.find("af_category")}
    tx = load_json(sdir / "workers-receipt.json").get("taxonomy") or {}
    claims = final["draft"]["claims"]
    counts = Counter(c["epistemic_status"] for c in claims)
    owner, name = gh["owner"]["login"], gh["name"]
    sha = reg["commit_sha"]
    github_url = gh["html_url"]
    lic_state = reg["license"]["status"]
    article = [s for s in sections if s["heading"] not in CHROME_SECTIONS]
    by_heading = {s["heading"]: s for s in sections}

    view = {
        "schema": VIEW_SCHEMA,
        "generated_at": utc_now(),
        "repository_id": reg["repository_id"],
        "repository": {
            "owner": owner, "name": name, "full_name": f"{owner}/{name}", "platform": "github", "github_url": github_url,
            "description_author_claimed": gh.get("description"), "homepage_author_claimed": gh.get("homepage"),
            "primary_language": gh.get("language"), "topics_platform": gh.get("topics", []),
            "stars_at_observation": gh.get("stargazers_count"), "observed_at": reg["observed_at"],
        },
        "license": {"state": lic_state, "spdx": reg["license"]["spdx"], "license_url": f"{github_url}/blob/{sha}/LICENSE",
                    "notice": NOTICES["no_license"] if lic_state == "none" else NOTICES["unresolved_license"] if lic_state in ("unknown", "unresolved") else None},
        "analysis": {"analyzed_revision": sha, "revision_url": f"{github_url}/tree/{sha}", "analyzer": fm["analyzer"], "analysis_run_id": fm["analysis_run_id"],
                     "manifest_sha256": fm["manifest_sha256"], "grounding_bundle_sha256": fm["grounding_bundle_sha256"], "last_verified": fm["last_verified"],
                     "static_only": True},
        "taxonomy": {"version": fm["taxonomy_version"], "primary": {"slug": tx.get("primary", fm["primary_category"]), "name": cat_names.get(tx.get("primary", fm["primary_category"]), fm["primary_category"])},
                     "secondary": [{"slug": s, "name": cat_names.get(s, s)} for s in tx.get("secondary", [])],
                     "assignment": fm["category_assignment"], "confidence": tx.get("confidence")},
        "asset": {"type": "overview", "content_version": fm["content_version"], "asset_revision_id": val["asset_revision_id"], "canonical_source_ref": rel_ref(canonical_path),
                  "canonical_source_sha256": val["canonical_source_sha256"], "validator_version": VALIDATOR_VERSION, "title": title, "summary": summary,
                  "meta_description": fm["meta_description"], "sections": article,
                  "uncertainties_markdown": by_heading.get("Uncertainties reported by the writer", {}).get("markdown"),
                  "claims_markdown": by_heading.get("Claims and evidence", {}).get("markdown"),
                  "claim_counts": {"total": len(claims), **{k: counts.get(k, 0) for k in ("observed", "inferred", "author_claimed", "unresolved")}},
                  "supported_claims": val["supported_claims"], "worker_runs": fm["worker_runs"], "language": "en"},
        "guides": [{"type": "overview", "available": True}, {"type": "getting_started", "available": False}, {"type": "architecture", "available": False}, {"type": "source_walkthrough", "available": False}],
        "related": [],
        "notices": {"version": NOTICES_VERSION, "rights": NOTICES["rights"], "platform": NOTICES["platform"], "ai_process": NOTICES["ai_process"]},
        "publication": {"status": "unpublished", "approved_by": None, "via": None, "published_at": None, "event_id": None, "url": None},
    }
    out = sdir / "canonical" / "repository-view.json"
    digest = write_json(out, view)
    print(json.dumps({"view_ref": rel_ref(out), "sha256": digest, "sections": [s["heading"] for s in article], "claims": view["asset"]["claim_counts"],
                      "taxonomy": view["taxonomy"]["primary"], "publication": "unpublished"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
