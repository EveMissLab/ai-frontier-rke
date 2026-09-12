"""Step 6 — local HTML preview of the repository page (Repository Page Minimum Contract).

Rendering is derived from the canonical Markdown (Paper 05 §94), never the
other way round. This is a local preview file, not a deployment.

Usage: python step6_render.py <slice_slug>
"""
from __future__ import annotations

import html
import re
import sys

import yaml

from af_common import load_json, slice_dir


def inline(md: str) -> str:
    s = html.escape(md, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', s)
    return s


def md_to_html(body: str) -> str:
    out, para, in_list, in_code, table = [], [], False, False, []
    def flush_para():
        nonlocal para
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>"); para = []
    def flush_table():
        nonlocal table
        if table:
            head, rows = table[0], [r for r in table[1:] if not re.match(r"^\s*\|?\s*-", r)]
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells(head)) + "</tr></thead><tbody>" +
                       "".join("<tr>" + "".join(f"<td>{inline(c.replace(chr(92) + '|', '|'))}</td>" for c in re.split(r"(?<!\\)\|", r.strip().strip('|'))) + "</tr>" for r in rows) + "</tbody></table>")
            table = []
    for line in body.split("\n"):
        if line.startswith("```"):
            flush_para(); flush_table()
            if in_code:
                out.append("</code></pre>"); in_code = False
            else:
                out.append("<pre><code>"); in_code = True
            continue
        if in_code:
            out.append(html.escape(line)); continue
        if line.startswith("|"):
            flush_para(); table.append(line); continue
        else:
            flush_table()
        m = re.match(r"^(#{1,3}) (.+)$", line)
        if m:
            flush_para()
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>"); continue
        if line.startswith("> "):
            flush_para(); out.append(f"<blockquote>{inline(line[2:])}</blockquote>"); continue
        if re.match(r"^\s*[-*] ", line):
            flush_para()
            if not in_list: out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(re.sub(r'^\s*[-*] ', '', line))}</li>"); continue
        if in_list and not line.strip():
            out.append("</ul>"); in_list = False; continue
        if not line.strip():
            flush_para(); continue
        para.append(line)
    flush_para(); flush_table()
    if in_list: out.append("</ul>")
    return "\n".join(out)


def main(slug: str) -> int:
    sdir = slice_dir(slug)
    val = load_json(sdir / "validation-receipt.json")
    md = (sdir / val["canonical_source_ref"].split("/", 1)[1]).read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    body = md.split("---\n", 2)[2]
    wr = load_json(sdir / "workers-receipt.json")
    tax = wr.get("taxonomy") or {}
    status = "VALIDATED · unpublished (awaiting human review and release gates)" if val["hard_gate_pass"] else "NOT VALIDATED · hard gate failed"
    css = """body{font:16px/1.6 system-ui,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#111}
    .contract{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1rem;border:1px solid #ddd;border-radius:12px;padding:1rem;margin:1rem 0;background:#fafafa}
    .contract dt{font:600 12px/1.4 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.06em;color:#555}.contract dd{margin:0}
    .status{display:inline-block;padding:.2rem .6rem;border-radius:999px;font:600 12px/1.4 ui-monospace,monospace;background:#eef;color:#224}
    table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ddd;padding:.35rem .5rem;vertical-align:top;text-align:left}
    code{font-size:.9em;background:#f3f3f3;padding:.05rem .3rem;border-radius:4px}blockquote{border-left:3px solid #ccc;margin:0;padding:.2rem 1rem;color:#333}
    .notice{font-size:14px;color:#444}"""
    contract = [
        ("Owner / Repository", f'<a href="{html.escape(fm["canonical_source_url"])}">{html.escape(fm["repository"])}</a>'),
        ("Summary", html.escape(fm.get("meta_description") or "")),
        ("Primary Category", html.escape(str(fm.get("primary_category") or "unassigned")) + (f" (model-proposed, confidence {tax.get('confidence')}, not validated)" if tax else "")),
        ("Language", "Python"),
        ("License Status", f"{html.escape(str(fm.get('license_spdx')))} · {html.escape(str(fm.get('license_state')))}"),
        ("Analyzed Revision", f"<code>{html.escape(fm['analyzed_revision'])}</code>"),
        ("Last Verified", html.escape(str(fm.get("last_verified")))),
        ("Original GitHub Repository", f'<a href="{html.escape(fm["canonical_source_url"])}" rel="noopener">{html.escape(fm["canonical_source_url"])} ↗</a>'),
        ("Available Guides", "Overview (this page). Architecture and source walkthrough: not yet produced."),
        ("Related Repositories", "None recorded."),
        ("Provenance", f"analysis run <code>{html.escape(fm['analysis_run_id'])}</code> · grounding bundle <code>{html.escape(fm['grounding_bundle_sha256'][:16])}…</code> · canonical source <code>{val['canonical_source_sha256'][:16]}…</code>"),
    ]
    page = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(fm['title'])} — AI Frontier preview</title><style>{css}</style>",
            f"<p><span class='status'>{status}</span> <span class='status'>LOCAL PREVIEW — not deployed</span></p>",
            f"<h1>{html.escape(fm['title'])}</h1>", "<dl class='contract'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in contract) + "</dl>",
            md_to_html(body), "<hr><p class='notice'>Rendered from the canonical UTF-8 Markdown; the Markdown, not this HTML, is the source of truth.</p>"]
    out = sdir / "canonical" / "preview.html"
    out.write_text("\n".join(page), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
