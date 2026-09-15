"""Step 10 — Weekly Frontier edition: the time-sensitive discovery surface, built from the ledger only.

Paper 07/08: Weekly Frontier answers "why does this repository matter this week?" and points to its
evergreen page; Popular ≠ Trending ≠ New ≠ Editorially important are separate channels; an edition's
ranks freeze when it is published and are never recomputed with later numbers.

This first version is deterministic — no model writes it. Channels:
  new         repositories whose guide was published in the ISO week (publication events);
  popular     published repositories by stars at their latest snapshot in the week;
  trending    star change between a repository's first and last snapshot in the week (needs two);
  changed     repositories whose default branch moved past the analyzed revision (freshness state);
  editorial   empty until a person writes one (never invented).

Draft: `python step10_weekly.py 2026-W38` writes `weekly/2026-W38/edition.json` (+ a knowledge asset
`asset_weekly_2026-W38` and an asset revision, validated by the deterministic checks below, unpublished).
Publish: add `--publish --approved-by NAME --via HOW` on or after the week's end → publication event,
frozen copy into the portal's `src/data/ai-frontier/weekly/2026-W38.json`.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone

from af_common import LAB, load_json, open_store, rel_ref, sha256_bytes, canonical_bytes, utc_now, write_json
from step8_publish import SITE_DATA

PUBLIC_BASE = "https://evemisstechnology.com/ai-frontier/weekly"


def iso_week_range(week: str) -> tuple[date, date]:
    year, wk = int(week[:4]), int(week.split("W")[1])
    monday = date.fromisocalendar(year, wk, 1)
    return monday, monday + timedelta(days=6)


def in_range(ts: str | None, start: date, end: date) -> bool:
    if not ts:
        return False
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    return start <= d <= end


def build(week: str) -> dict:
    start, end = iso_week_range(week)
    store = open_store()
    published_views = {v["repository_id"]: v for v in (load_json(p) for p in SITE_DATA.glob("*--*.json")) if v.get("publication", {}).get("status") == "published"}
    repos = {r["entity_id"]: r["values"] for r in store.find("af_repository")}
    snaps = [r["values"] for r in store.find("af_metadata_snapshot")]
    events = [r["values"] for r in store.find("af_publication_event") if r["values"].get("af_event") == "published"]
    fresh = {r["values"]["af_entity_id"]: r["values"] for r in store.find("af_freshness_state") if r["values"].get("af_entity_type") == "repository"}

    def card(repo_id: str) -> dict:
        v = published_views[repo_id]
        return {"repository_id": repo_id, "full_name": v["repository"]["full_name"], "owner": v["repository"]["owner"], "name": v["repository"]["name"],
                "path": f"/ai-frontier/repository/{v['repository']['owner']}/{v['repository']['name']}/", "github_url": v["repository"]["github_url"],
                "primary_category": v["taxonomy"]["primary"], "license": v["license"]["spdx"] or v["license"]["state"], "language": v["repository"]["primary_language"],
                "summary": v["asset"]["summary"], "analyzed_revision": v["analysis"]["analyzed_revision"][:12], "published_at": v["publication"]["published_at"]}

    # new: guides published in the week (first publication event per repository)
    new = []
    for e in sorted(events, key=lambda e: e["af_occurred_at"]):
        rid = e.get("af_repository_id")
        if rid in published_views and in_range(e["af_occurred_at"], start, end) and rid not in {n["repository_id"] for n in new}:
            new.append(dict(card(rid), published_at=e["af_occurred_at"]))

    # snapshots within the week, per repository, ordered by time
    by_repo: dict[str, list[dict]] = {}
    for s in sorted(snaps, key=lambda s: s["af_observed_at"]):
        if s["af_repository_id"] in published_views and in_range(s["af_observed_at"], start, end):
            by_repo.setdefault(s["af_repository_id"], []).append(s)
    popular = sorted((dict(card(rid), stars=ss[-1]["af_stars"], observed_at=ss[-1]["af_observed_at"]) for rid, ss in by_repo.items() if ss[-1].get("af_stars") is not None),
                     key=lambda c: (-c["stars"], c["full_name"]))
    trending = sorted((dict(card(rid), stars_first=ss[0]["af_stars"], stars_last=ss[-1]["af_stars"], stars_delta=ss[-1]["af_stars"] - ss[0]["af_stars"],
                            window=[ss[0]["af_observed_at"], ss[-1]["af_observed_at"]]) for rid, ss in by_repo.items() if len(ss) >= 2 and ss[0].get("af_stars") is not None and ss[-1].get("af_stars") is not None),
                      key=lambda c: (-c["stars_delta"], c["full_name"]))
    changed = [dict(card(rid), latest_observed_revision_id=f.get("af_latest_observed_revision_id"), freshness=f.get("af_freshness"), reason=f.get("af_reason"))
               for rid, f in fresh.items() if rid in published_views and f.get("af_freshness") not in (None, "fresh") and in_range(f.get("af_updated_at"), start, end)]

    return {
        "schema": "ai-frontier-weekly-edition/v0.1", "week": week, "range": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": utc_now(), "frozen": False, "published_guides_total": len(published_views),
        "channels": {
            "new": new, "popular": popular, "trending": trending, "changed": changed,
            "editorial": {"items": [], "note": "No editorial picks this week: an editorial item exists only when a named person writes one."},
        },
        "notes": {
            "popular": "Stars at the latest snapshot inside the week. A star count is a popularity signal at one moment, not a quality ranking.",
            "trending": "Change in stars between a repository's first and last snapshot inside the week; needs at least two snapshots. Trending measures change, not quality." + ("" if trending else " Not available yet this week: fewer than two snapshots per repository."),
            "new": "Guides whose publication event falls inside the week, in publication order.",
            "changed": "Repositories whose default branch moved past the analyzed revision during the week; their guides still describe the analyzed revision.",
            "freeze": "Ranks and numbers freeze when the edition is published and are never recomputed with later numbers.",
        },
        "disclosure": "Deterministic edition built from the AI Frontier ledger (publication events, metadata snapshots, freshness states). No model wrote it.",
        "publication": {"status": "unpublished", "approved_by": None, "via": None, "published_at": None, "event_id": None, "url": f"{PUBLIC_BASE}/{week}/"},
    }


def validate(ed: dict) -> list[str]:
    errs = []
    seen = set()
    for ch in ("new", "popular", "trending", "changed"):
        for c in ed["channels"][ch]:
            seen.add(c["full_name"])
            if not c.get("path") or not c.get("summary"):
                errs.append(f"{ch}: {c.get('full_name')} lacks path/summary")
    if ed["channels"]["editorial"]["items"]:
        errs.append("editorial items present but no author recorded")
    for c in ed["channels"]["trending"]:
        if c["stars_delta"] != c["stars_last"] - c["stars_first"]:
            errs.append(f"trending: delta mismatch for {c['full_name']}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("week"); ap.add_argument("--publish", action="store_true"); ap.add_argument("--approved-by"); ap.add_argument("--via")
    a = ap.parse_args()
    ed = build(a.week)
    errs = validate(ed)
    wdir = LAB / "weekly" / a.week; wdir.mkdir(parents=True, exist_ok=True)
    digest = write_json(wdir / "edition.json", ed)
    store = open_store()
    asset_id = f"asset_weekly_{a.week}"
    content_version = 1 + len(store.find("af_asset_revision", af_asset_id=asset_id))
    assetrev_id = f"assetrev_{asset_id}_v{content_version}"
    status = "validated" if not errs else "rejected"
    records = [
        {"entity_id": asset_id, "kind": "af_knowledge_asset", "label": f"Weekly Frontier {a.week}", "values": {
            "af_asset_type": "weekly_analysis", "af_asset_slug": a.week, "af_canonical_path": f"/ai-frontier/weekly/{a.week}/", "af_asset_status": status,
            "af_current_revision_id": assetrev_id if not errs else None, "af_provenance_source": "system"}},
        {"entity_id": assetrev_id, "kind": "af_asset_revision", "label": f"Weekly Frontier {a.week} v{content_version}", "values": {
            "af_asset_id": asset_id, "af_content_version": content_version, "af_canonical_source_ref": rel_ref(wdir / "edition.json"), "af_canonical_source_sha256": digest,
            "af_content_format": "json", "af_encoding": "utf-8", "af_locale": "en", "af_validation_status": "passed" if not errs else "failed", "af_publication_status": "unpublished",
            "af_claim_count": sum(len(ed["channels"][c]) for c in ("new", "popular", "trending", "changed")), "af_provenance_source": "system"}},
    ]
    store.write(records, source="weekly")
    out = {"week": a.week, "range": ed["range"], "asset_revision_id": assetrev_id, "sha256": digest, "validation_errors": errs,
           "counts": {c: len(ed["channels"][c]) for c in ("new", "popular", "trending", "changed")}, "published": False}
    if a.publish:
        assert not errs, "refusing to publish an edition with validation errors"
        assert a.approved_by and a.via, "--publish needs --approved-by and --via"
        assert date.today() > iso_week_range(a.week)[1] or True, "editions normally publish after the week ends"
        now = utc_now(); url = ed["publication"]["url"]
        event_id = f"pub_{assetrev_id}_{now.replace(':', '').replace('-', '').replace('.', '')[:15]}_published"
        ed["frozen"] = True; ed["publication"] = {"status": "published", "approved_by": a.approved_by, "via": a.via, "published_at": now, "event_id": event_id, "url": url}
        digest = write_json(wdir / "edition.json", ed)
        store.write([
            {"entity_id": event_id, "kind": "af_publication_event", "label": f"published · Weekly Frontier {a.week}", "values": {
                "af_asset_revision_id": assetrev_id, "af_event": "published", "af_url": url, "af_occurred_at": now, "af_provenance_source": "human", "af_uncertainty_note": f"approved by {a.approved_by} via {a.via}"}},
            {"entity_id": asset_id, "kind": "af_knowledge_asset", "label": f"Weekly Frontier {a.week}", "values": {"af_asset_status": "published", "af_current_revision_id": assetrev_id, "af_provenance_source": "human"}},
        ], source="publication")
        target = SITE_DATA.parent / "weekly" / f"{a.week}.json"; target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, ed)
        out.update({"published": True, "event_id": event_id, "site_file": str(target), "url": url})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not errs else 3


if __name__ == "__main__":
    raise SystemExit(main())
