"""Step 8 — publication event: a human-approved, recorded act; never something the pipeline decides.

Preconditions: the slice's validation receipt says hard_gate_pass, step 7 produced the view model, and a
named human approves this exact asset revision (name + how the approval was given). The step then:

1. writes `<slice>/canonical/PUBLICATION_APPROVAL.json` (who, how, when, which revision, which page-level
   gates were checked — RELEASE_GATES v0.1 G5 rights & provenance, G6 publishing quality, G7 SEO/crawl,
   G9 performance/a11y — plus the human content review);
2. records `af_publication_event` (immutable) and moves the knowledge asset to `published` in the catalog;
3. drops the view model, with its publication block filled in, into the portal's data directory
   (`<site>/src/data/ai-frontier/repositories/<owner>--<repo>.json`). Building and pushing the site is a
   separate, visible act.

`--unpublish` records the reverse event and removes the file (the page disappears on the next build); the
canonical Markdown and every ledger row stay.

Usage: python step8_publish.py <slice_slug> --approved-by "Name" --via "how" [--notes "..."] [--unpublish]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from af_common import LAB, load_json, open_store, rel_ref, slice_dir, utc_now, write_json

SITE_ROOT = Path(os.environ.get("AF_SITE_ROOT") or (json.loads((LAB / "pipeline" / "local_paths.json").read_text(encoding="utf-8")).get("AF_SITE_ROOT")
                                                   if (LAB / "pipeline" / "local_paths.json").exists() else "") or (LAB.parent / "evemiss-technology"))
SITE_DATA = SITE_ROOT / "src" / "data" / "ai-frontier" / "repositories"
PUBLIC_BASE = "https://evemisstechnology.com/ai-frontier/repository"
PAGE_GATES = {
    "G5_rights_provenance": "GitHub source, owner, license state, analyzed revision and disclosure shown on the page",
    "G6_publishing_quality": "human read the overview: intent, original value, grounding, canonical source",
    "G7_seo_crawl": "canonical URL, hreflang, breadcrumbs, sitemap entry, title/description within limits",
    "G9_performance_a11y": "semantic HTML, keyboard reachable, no horizontal overflow at 375px",
    "human_content_review": "a named person read the rendered page and approved this exact asset revision",
}


def export_taxonomy(store) -> Path:
    """The versioned taxonomy the portal's category pages read (slug, name, parent). Always in sync with the catalog."""
    rows = store.find("af_category")
    by_id = {r["entity_id"]: r["values"]["af_category_slug"] for r in rows}
    cats = sorted(({"slug": r["values"]["af_category_slug"], "name": r["values"]["af_display_name"], "parent": by_id.get(r["values"].get("af_parent_category_id")),
                    "status": r["values"].get("af_category_status")} for r in rows), key=lambda c: (c["parent"] or "", c["slug"]))
    out = SITE_DATA.parent / "taxonomy-v1.json"
    write_json(out, {"version": "v1", "exported_at": utc_now(), "categories": cats})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug"); ap.add_argument("--approved-by", required=True); ap.add_argument("--via", required=True)
    ap.add_argument("--notes", default=""); ap.add_argument("--unpublish", action="store_true")
    ap.add_argument("--asset", default="overview", help="asset type to publish: overview (default) or an additional guide such as architecture")
    a = ap.parse_args()
    sdir = slice_dir(a.slug)
    from af_common import asset_paths
    apaths = asset_paths(sdir, a.asset)
    val = load_json(apaths["validation_receipt"])
    view_path = sdir / "canonical" / "repository-view.json"
    assert val["hard_gate_pass"], "refusing: the asset revision did not pass the hard gates"
    assert view_path.exists(), "refusing: run step 7 first"
    view = load_json(view_path)
    if a.asset == "overview":
        assert view["asset"]["asset_revision_id"] == val["asset_revision_id"], "view model is not for this validation receipt"
    else:
        assert a.asset in view.get("assets", {}) and view["assets"][a.asset]["asset_revision_id"] == val["asset_revision_id"], "view model has no validated block for this asset; run step 7"
    repo = view["repository"]
    url = f"{PUBLIC_BASE}/{repo['owner']}/{repo['name']}/{apaths['path_suffix']}"
    now = utc_now()
    event = "unpublished" if a.unpublish else "published"
    store = open_store()
    assetrev_id = val["asset_revision_id"]
    asset_id = val["asset_id"]
    event_id = f"pub_{assetrev_id}_{now.replace(':', '').replace('-', '').replace('.', '')[:15]}_{event}"

    approval = {"asset_revision_id": assetrev_id, "canonical_source_sha256": val["canonical_source_sha256"], "event": event, "approved_by": a.approved_by,
                "via": a.via, "at": now, "notes": a.notes, "page_gates_checked": PAGE_GATES, "release_gates_note": "RELEASE_GATES v0.1 G0–G10 are system-level; this record covers the page-level subset for one asset revision.",
                "public_url": url, "event_id": event_id}
    write_json(sdir / "canonical" / "PUBLICATION_APPROVAL.json", approval)

    records = [
        {"entity_id": event_id, "kind": "af_publication_event", "label": f"{event} · {repo['full_name']} {a.asset} v{(view['asset'] if a.asset == 'overview' else view['assets'][a.asset])['content_version']}", "values": {
            "af_asset_revision_id": assetrev_id, "af_repository_id": val.get("repository_id") or view.get("repository_id"), "af_event": event, "af_url": url, "af_occurred_at": now,
            "af_provenance_source": "human", "af_uncertainty_note": f"approved by {a.approved_by} via {a.via}" + (f"; {a.notes}" if a.notes else "")}},
        {"entity_id": asset_id, "kind": "af_knowledge_asset", "label": f"{repo['full_name']} {a.asset}", "values": {
            "af_asset_status": "published" if event == "published" else "validated", "af_canonical_path": f"/ai-frontier/repository/{repo['owner']}/{repo['name']}/{apaths['path_suffix']}",
            "af_current_revision_id": assetrev_id, "af_provenance_source": "human"}},
    ]
    res = store.write(records, source="publication")

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    export_taxonomy(store)
    target = SITE_DATA / f"{repo['owner']}--{repo['name']}.json"
    if a.asset != "overview":
        # an additional guide joins the repository's already-published view: merge the validated block, keep everything else
        assert target.exists(), "refusing: the repository's overview is not published yet"
        published_view = load_json(target)
        block = dict(view["assets"][a.asset])
        block["publication"] = {"status": "published" if event == "published" else "unpublished", "approved_by": a.approved_by, "via": a.via, "published_at": now if event == "published" else None, "event_id": event_id, "url": url}
        published_view.setdefault("assets", {})[a.asset] = block
        published_view["guides"] = [dict(g, available=(g["type"] == "overview") or (g["type"] in published_view.get("assets", {}) and published_view["assets"][g["type"]]["publication"]["status"] == "published")) for g in published_view["guides"]]
        write_json(target, published_view)
    elif event == "published":
        view["publication"] = {"status": "published", "approved_by": a.approved_by, "via": a.via, "published_at": now, "event_id": event_id, "url": url}
        view["disclosure"] = "AI-assisted analysis. Human-edited and reviewed by EVEMISS Technology."
        write_json(target, view)
    elif target.exists():
        target.unlink()
    out = {"event": event, "event_id": event_id, "asset_revision_id": assetrev_id, "url": url, "approved_by": a.approved_by, "via": a.via,
           "site_file": str(target) if event == "published" else None, "sedb_write": res.__dict__, "approval_ref": rel_ref(sdir / "canonical" / "PUBLICATION_APPROVAL.json"),
           "next": "build and push the site; the page is a view of the ledger"}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
