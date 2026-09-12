"""Step 5 — hard publication gates, canonical UTF-8 Markdown commit, asset records.

Paper 05 §92-93: schema ∧ grounding ∧ revision ∧ license ∧ source ∧ security ∧
canonical-source validation, then Draft → Canonical Markdown → SHA-256.
Validation state and publication state stay separate (Paper 03 §42): this
step never publishes.

Usage: python step5_validate.py <slice_slug>
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse

import yaml

from af_common import load_json, open_store, rel_ref, sha256_bytes, slice_dir, utc_now, write_json
from prompts import SCHEMAS, WRITER_V
from step4_workers import deterministic_checks

VALIDATOR_VERSION = "af-validators/v1.2"  # v1.1: URL host check strips trailing sentence punctuation; v1.2: "best" gate matches superlative/marketing use only, not the hedge "at best" (run 3 false positive)
NOTICES_VERSION = "attribution-license-templates/v0.1"
RIGHTS_NOTICE = ("Original repository hosted on GitHub. Repository source code, documentation, names, media, and related project "
                 "materials remain subject to the rights of their respective authors, contributors, and other rights holders and to "
                 "applicable repository license terms.")
PLATFORM_NOTICE = ("GitHub is the source hosting platform for the linked repository. GitHub and related marks are trademarks of "
                   "GitHub, Inc. EVEMISS Technology is not affiliated with or endorsed by GitHub unless explicitly stated otherwise.")
AI_PROCESS_NOTICE = ("This page was produced using revision-aware repository analysis and AI-assisted editorial tooling. Technical "
                     "claims are tied to the analyzed repository revision and may be revalidated when the source repository changes.")
STATIC_NOTICE = ("Architecture reconstruction is based on static repository analysis and may not capture all runtime behavior, "
                 "dynamic dispatch, generated code, or environment-specific execution.")
FORBIDDEN_WORDING = [r"\bwe tested\b", r"\bverified at runtime\b", r"\bguaranteed\b", r"\bblazing\b", r"\bproduction[- ]ready\b",
                     r"\b(?:the|is|are|its|their)\s+best\b", r"\bbest[- ](?:in[- ]class|of[- ]breed|practices?|way|choice|tool|library|option)\b"]


def md_escape_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def build_markdown(draft, reg, gr, ov_packet, verdict, seo, taxonomy, worker_run_ids, catalog, license_rec, content_version=1) -> str:
    run_id = gr["analysis_run_id"]
    sha = reg["commit_sha"]
    repo_url = ov_packet["repository"]["canonical_source_url"]
    full_name = "/".join(repo_url.rstrip("/").split("/")[-2:])
    owner = full_name.split("/")[0]
    now = utc_now()
    title = (seo or {}).get("title_candidates", [draft["title"]])[0] if seo else draft["title"]
    meta_desc = (seo or {}).get("meta_description")
    front = {
        "asset_type": "overview", "asset_slug": "overview", "content_version": content_version, "locale": "en", "content_format": "markdown", "encoding": "utf-8",
        "title": title, "meta_description": meta_desc, "repository": full_name, "platform": "github", "canonical_source_url": repo_url,
        "analyzed_revision": sha, "analysis_run_id": run_id,
        "analyzer": "RepoLumen 0.10, deterministic mode, external provider disabled", "manifest_sha256": gr["manifest_sha256"], "grounding_bundle_sha256": gr["grounding_bundle_sha256"],
        "license_state": license_rec["status"], "license_spdx": license_rec["spdx"], "license_file_sha256": license_rec.get("file_sha256"),
        "taxonomy_version": "v1", "primary_category": (taxonomy or {}).get("primary"), "category_assignment": "model-proposed, not yet validated" if taxonomy else None,
        "worker_runs": worker_run_ids, "verifier_status": (verdict or {}).get("status"),
        "last_verified": now, "review_status": "awaiting_human_review", "publication_status": "unpublished",
        "notices_version": NOTICES_VERSION,
        "disclosure": "AI-assisted analysis produced from a deterministic repository analysis; not yet human-reviewed; not published.",
    }
    lines = ["---", yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip(), "---", "", f"# {title}", "", f"> {draft['summary']}", ""]
    lines += ["## Repository", "", "| | |", "|---|---|",
              f"| Original Repository | [{full_name}]({repo_url}) |", "| Source Platform | GitHub |", f"| Repository Owner / Organization | {owner} |",
              f"| License | {license_rec['spdx'] or 'No license detected'} ({license_rec['status']}) |", f"| Analyzed Revision | `{sha}` |", f"| Last Verified | {now} |", ""]
    for s in draft["sections"]:
        lines += [f"## {s['heading']}", "", s["markdown"].strip(), ""]
    if draft.get("uncertainties"):
        lines += ["## Uncertainties reported by the writer", ""] + [f"- {u}" for u in draft["uncertainties"]] + [""]
    checks = {c["claim_id"]: c for c in (verdict or {}).get("claim_checks", [])}
    lines += ["## Claims and evidence", "", "Every substantive statement above is a claim bound to grounding IDs of the analyzed revision. "
              "Global grounding IDs are namespaced by the analysis run.", "",
              "| Claim | Epistemic status | Verifier | Grounding (global IDs) |", "|---|---|---|---|"]
    for c in draft["claims"]:
        refs = ", ".join(f"`{run_id}:{r}`" for r in c["grounding_refs"])
        lines.append(f"| **{c['claim_id']}** {md_escape_cell(c['text'])} | {c['epistemic_status']} | {checks.get(c['claim_id'], {}).get('status', 'n/a')} | {refs} |")
    lines += ["", "## Notices", "", f"**Rights notice.** {RIGHTS_NOTICE}", "", f"**Platform notice.** {PLATFORM_NOTICE}", "", f"**AI process notice.** {AI_PROCESS_NOTICE}", "",
              f"**Static architecture notice.** {STATIC_NOTICE}", ""]
    return "\n".join(lines).replace("\r\n", "\n") + "\n"


def security_checks(md: str, allowed_hosts: set[str]) -> list[str]:
    errs = []
    if re.search(r"<\s*(script|iframe|object|embed|style|img|svg)\b", md, flags=re.I):
        errs.append("raw HTML element present")
    for url in re.findall(r"\]\((https?://[^)\s]+)\)", md) + re.findall(r"(?<!\()\bhttps?://[^\s)`]+", md):
        host = urlparse(url.rstrip(".,;:!?")).netloc.lower()  # trailing sentence punctuation is not part of the host
        if host and host not in allowed_hosts:
            errs.append(f"link to non-allowlisted host: {host}")
    if re.search(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[A-Za-z0-9]{16,}", md):
        errs.append("credential-like string present")
    return sorted(set(errs))


def markdown_checks(md: str) -> list[str]:
    errs = []
    try:
        md.encode("utf-8")
    except UnicodeEncodeError:
        errs.append("not UTF-8 encodable")
    if not md.startswith("---\n"):
        errs.append("missing frontmatter")
    try:
        fm = yaml.safe_load(md.split("---\n", 2)[1])
        for k in ("analyzed_revision", "canonical_source_url", "license_state", "analysis_run_id", "grounding_bundle_sha256"):
            if not fm.get(k):
                errs.append(f"frontmatter missing {k}")
    except Exception as exc:
        errs.append(f"frontmatter not valid YAML: {exc}")
    heads = re.findall(r"^## (.+)$", md, flags=re.M)
    dup = {h for h in heads if heads.count(h) > 1}
    if dup:
        errs.append(f"duplicate headings: {sorted(dup)}")
    for req in ("Repository", "Claims and evidence", "Notices"):
        if req not in heads:
            errs.append(f"missing required section: {req}")
    if md.count("```") % 2:
        errs.append("unbalanced code fence")
    for pat in FORBIDDEN_WORDING:
        if re.search(pat, md, flags=re.I):
            errs.append(f"forbidden wording: {pat}")
    return errs


def main(slug: str) -> int:
    sdir = slice_dir(slug)
    reg, gr = load_json(sdir / "registration-receipt.json"), load_json(sdir / "grounding-receipt.json")
    wr = load_json(sdir / "workers-receipt.json")
    final = load_json(sdir / "worker_runs" / "final-draft-bundle.json")
    ov_packet = load_json(sdir / "packets" / "overview.json")
    bundle = load_json(sdir / gr["grounding_bundle_ref"].split("/", 1)[1])
    catalog, files = bundle["groundings"], set(bundle["files"])
    draft, verdict, seo, taxonomy = final["draft"], final.get("verifier"), final.get("seo"), wr.get("taxonomy")
    repo_id, rev_id, run_id = reg["repository_id"], reg["revision_id"], gr["analysis_run_id"]
    store = open_store()
    lic_rec = reg["license"]
    lic_state = gr["license_state"]["status"]

    worker_run_ids = [r["worker_run_entity_id"] for r in wr["runs"] if r.get("worker_run_entity_id")]
    asset_id = f"asset_{repo_id}_overview"
    content_version = 1 + len(store.find("af_asset_revision", af_asset_id=asset_id))
    md = build_markdown(draft, reg, gr, ov_packet, verdict, seo, taxonomy, worker_run_ids, catalog, dict(lic_rec, status=lic_state), content_version)
    allowed_hosts = {"github.com", "raw.githubusercontent.com"}
    hp = next((g["text"] for g in bundle["platform_metadata"] if g["id"] == "meta_homepage"), None)
    if hp:
        allowed_hosts.add(urlparse(hp).netloc.lower())

    import jsonschema
    schema_errors = [e.message[:160] for e in jsonschema.Draft202012Validator(SCHEMAS[WRITER_V]).iter_errors(draft)]
    det = deterministic_checks(draft, ov_packet, catalog, files)
    gates = {
        "schema": {"passed": not schema_errors, "details": schema_errors[:10]},
        "grounding": {"passed": det["ok"] and verdict is not None and verdict.get("status") == "pass",
                      "details": {"deterministic_failures": det["failures"][:10], "verifier_status": (verdict or {}).get("status"),
                                  "unsupported": (verdict or {}).get("unsupported_claims"), "leakage": (verdict or {}).get("knowledge_leakage")}},
        "revision": {"passed": ov_packet["repository"]["analyzed_revision"] == reg["commit_sha"] == bundle["source"]["revision"], "details": {"registered": reg["commit_sha"], "packet": ov_packet["repository"]["analyzed_revision"]}},
        "license": {"passed": lic_state in ("open-source", "custom", "multiple", "none"), "details": {"state": lic_state, "policy": "block_source_reproduction" if lic_state in ("unknown", "unresolved") else "allowed"}},
        "source": {"passed": bool(ov_packet["repository"]["canonical_source_url"].startswith("https://github.com/")), "details": ov_packet["repository"]["canonical_source_url"]},
        "security": {"passed": not security_checks(md, allowed_hosts), "details": security_checks(md, allowed_hosts)},
        "markdown": {"passed": not markdown_checks(md), "details": markdown_checks(md)},
        "policy": {"passed": bool(draft.get("claims")) and len(draft["claims"]) <= 45 and all(c["epistemic_status"] != "unresolved" or True for c in draft["claims"]),
                   "details": {"claims": len(draft.get("claims", [])), "sections": len(draft.get("sections", []))}},
    }
    hard_pass = all(g["passed"] for g in gates.values())
    canonical_dir = sdir / "canonical"
    canonical_dir.mkdir(exist_ok=True)
    name = "overview.md" if hard_pass else f"overview.v{1 + len(store.find('af_asset_revision', af_asset_id=f'asset_{repo_id}_overview'))}.DRAFT-NOT-VALIDATED.md"
    path = canonical_dir / name
    data = md.encode("utf-8")
    path.write_bytes(data)
    digest = sha256_bytes(data)

    asset_id = f"asset_{repo_id}_overview"
    # Asset revisions and validation runs are immutable evidence: every validation pass creates the next
    # content version instead of rewriting an earlier one (Paper 03 §39-42).
    content_version = 1 + len(store.find("af_asset_revision", af_asset_id=asset_id))
    assetrev_id = f"assetrev_{asset_id}_v{content_version}"
    now = utc_now()
    supported = sum(1 for c in (verdict or {}).get("claim_checks", []) if c["status"] == "supported")
    records = []
    for vtype, g in gates.items():
        records.append({"entity_id": f"val_{assetrev_id}_{vtype}_{VALIDATOR_VERSION.rsplit('/', 1)[1]}", "kind": "af_validation_run", "label": f"{vtype} · {assetrev_id[-14:]}", "values": {
            "af_target_type": "asset_revision", "af_target_id": assetrev_id, "af_validator_type": vtype, "af_validator_version": VALIDATOR_VERSION,
            "af_validation_status": "passed" if g["passed"] else "failed", "af_details": g["details"], "af_completed_at": now, "af_provenance_source": "system"}})
    records.append({"entity_id": assetrev_id, "kind": "af_asset_revision", "label": f"overview v1 ({'validated' if hard_pass else 'rejected'})", "values": {
        "af_asset_id": asset_id, "af_repository_id": repo_id, "af_repository_revision_id": rev_id, "af_analysis_run_id": run_id,
        "af_grounding_bundle_sha256": gr["grounding_bundle_sha256"], "af_content_version": content_version, "af_canonical_source_ref": rel_ref(path),
        "af_canonical_source_sha256": digest, "af_content_format": "markdown", "af_encoding": "utf-8", "af_locale": "en",
        "af_selected_grounding_ids": sorted({f"{run_id}:{r}" for c in draft["claims"] for r in c["grounding_refs"]}),
        "af_worker_run_ids": worker_run_ids, "af_validation_status": "passed" if hard_pass else "failed", "af_publication_status": "unpublished",
        "af_claim_count": len(draft["claims"]), "af_supported_claim_count": supported,
        "af_quality_scores": {"critic": (final.get("critic") or {}).get("scores"), "grounding_coverage": round(supported / max(1, len(draft["claims"])), 3), "overclaim_flags": len((final.get("critic") or {}).get("overclaim_flags", []))},
        "af_created_at": now, "af_provenance_source": "system"}})
    records.append({"entity_id": asset_id, "kind": "af_knowledge_asset", "label": f"{reg['repository_id']} overview", "values": {
        "af_repository_id": repo_id, "af_asset_type": "overview", "af_asset_slug": "overview",
        "af_canonical_path": f"/ai-frontier/repository/{'/'.join(ov_packet['repository']['canonical_source_url'].rstrip('/').split('/')[-2:])}/",
        "af_asset_status": "validated" if hard_pass else "rejected", "af_current_revision_id": assetrev_id if hard_pass else None, "af_provenance_source": "system"}})
    records.append({"entity_id": f"fresh_{asset_id}", "kind": "af_freshness_state", "label": "overview freshness", "values": {
        "af_entity_type": "asset", "af_entity_id": asset_id, "af_last_verified_revision_id": rev_id, "af_latest_observed_revision_id": rev_id,
        "af_freshness": "fresh" if hard_pass else "unknown", "af_reason": "validated against the analyzed revision" if hard_pass else "hard gate failed", "af_updated_at": now, "af_provenance_source": "system"}})
    records.append({"entity_id": f"search_{repo_id}", "kind": "af_search_document", "label": f"search · {reg['repository_id']}", "values": {
        "af_entity_type": "repository", "af_entity_id": repo_id, "af_title": "/".join(ov_packet["repository"]["canonical_source_url"].rstrip("/").split("/")[-2:]),
        "af_owner": ov_packet["repository"]["canonical_source_url"].rstrip("/").split("/")[-2], "af_summary": draft["summary"],
        "af_topics": [g["text"] for g in bundle["platform_metadata"] if g["id"] == "meta_topics"], "af_categories": [taxonomy["primary"]] + list(taxonomy.get("secondary", [])) if taxonomy else [],
        "af_language": next((g["text"] for g in bundle["platform_metadata"] if g["id"] == "meta_primary_language"), None),
        "af_asset_titles": [draft["title"]] if hard_pass else [], "af_search_visible": False, "af_search_schema_version": "search/v1", "af_indexed_at": now, "af_provenance_source": "system"}})
    res = store.write(records, source="validator")
    out = {"hard_gate_pass": hard_pass, "gates": gates, "canonical_source_ref": rel_ref(path), "canonical_source_sha256": digest, "canonical_bytes": len(data),
           "asset_id": asset_id, "asset_revision_id": assetrev_id, "claims": len(draft["claims"]), "supported_claims": supported, "sedb_write": res.__dict__,
           "publication_status": "unpublished", "note": "validated ≠ published; release gates G0–G10 and the canary policy decide publication"}
    write_json(sdir / "validation-receipt.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if hard_pass else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
