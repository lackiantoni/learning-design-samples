#!/usr/bin/env python3
"""
Mine the artifacts of a multi-agent blog review pipeline.

Each production run leaves one directory containing 7 independent reviewer files
plus a synthesis written by an 8th agent. This script turns 30 such directories
into a single runs.json so that every number quoted in the case study can be
traced back to a file on disk.

Usage:
    python3 analyze_runs.py [REVIEWS_DIR] [-o OUTPUT.json]
"""

import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

REVIEWERS = [
    "devils-advocate",
    "client-avatar",
    "copy-coach",
    "seo-reviewer",
    "fact-checker",
    "brand-voice",
    "hook-coach",
]
SYNTHESIS = "_synthesis.md"

DEFAULT_REVIEWS_DIR = (
    "/Users/antonilacki/Desktop/Claude/outputs/personal-brand/"
    "antonilackicom-blog/reviews"
)

# "**7/7 agentów: ..." or "- **5/7: ..." - the synthesizer records how many of the
# seven independent reviewers raised the same finding. This is the agreement signal.
CONSENSUS_RE = re.compile(r"\*\*\s*(\d)\s*/\s*7\b")

# Rows of the priority-1 change table: "| 12 | 65 vs 281 | ... |"
TABLE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")

# Numbered findings inside a reviewer file: "1. **Niepoparte twierdzenie**"
NUMBERED_FINDING_RE = re.compile(r"^\s*(\d+)\.\s+\*\*")

MUSTFIX_HEADING_RE = re.compile(r"^#{1,4}\s.*(must-fix|priorytet 1|priority 1)", re.I)
DIVERGENCE_HEADING_RE = re.compile(r"^#{1,4}\s.*(rozbie|divergen|disagree)", re.I)
HEADING_RE = re.compile(r"^#{1,4}\s")


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def section(lines, start_re):
    """Return the lines of the first section whose heading matches start_re."""
    out, capturing = [], False
    for line in lines:
        if HEADING_RE.match(line):
            if capturing:
                break
            capturing = bool(start_re.search(line))
            continue
        if capturing:
            out.append(line)
    return out


def analyse_synthesis(path):
    text = read(path)
    lines = text.splitlines()

    consensus = [int(m) for m in CONSENSUS_RE.findall(text)]
    mustfix = [l for l in section(lines, MUSTFIX_HEADING_RE) if TABLE_ROW_RE.match(l)]
    divergences = [
        l for l in section(lines, DIVERGENCE_HEADING_RE)
        if l.strip().startswith(("-", "*"))
    ]

    return {
        "words": len(text.split()),
        "consensus_counts": consensus,
        "findings_with_multi_reviewer_agreement": sum(1 for c in consensus if c >= 2),
        "findings_with_majority_agreement": sum(1 for c in consensus if c >= 4),
        "must_fix_rows": len(mustfix),
        "divergences_resolved": len(divergences),
    }


def analyse_run(run_dir):
    name = os.path.basename(run_dir)
    present = {r: os.path.join(run_dir, r + ".md") for r in REVIEWERS}
    present = {r: p for r, p in present.items() if os.path.exists(p)}
    syn_path = os.path.join(run_dir, SYNTHESIS)

    reviewers = {}
    for role, path in present.items():
        text = read(path)
        reviewers[role] = {
            "words": len(text.split()),
            "numbered_findings": sum(
                1 for l in text.splitlines() if NUMBERED_FINDING_RE.match(l)
            ),
            "mtime": os.path.getmtime(path),
        }

    run = {
        "slug": name,
        "reviewers_present": sorted(present),
        "reviewer_count": len(present),
        "complete": len(present) == len(REVIEWERS) and os.path.exists(syn_path),
        "reviewers": reviewers,
    }

    if reviewers:
        mtimes = [r["mtime"] for r in reviewers.values()]
        run["fan_out_span_seconds"] = round(max(mtimes) - min(mtimes), 1)
        run["reviewer_findings_total"] = sum(
            r["numbered_findings"] for r in reviewers.values()
        )
        run["reviewer_words_total"] = sum(r["words"] for r in reviewers.values())
        if os.path.exists(syn_path):
            run["synthesis_lag_seconds"] = round(
                os.path.getmtime(syn_path) - max(mtimes), 1
            )
            run["run_date"] = datetime.fromtimestamp(
                max(mtimes), tz=timezone.utc
            ).strftime("%Y-%m-%d")

    if os.path.exists(syn_path):
        run["synthesis"] = analyse_synthesis(syn_path)

    return run


def summarise(runs):
    complete = [r for r in runs if r.get("complete")]
    with_syn = [r for r in runs if "synthesis" in r]

    all_consensus = [
        c for r in with_syn for c in r["synthesis"]["consensus_counts"]
    ]
    mustfix = [r["synthesis"]["must_fix_rows"] for r in with_syn]
    mustfix = [m for m in mustfix if m]
    findings = [r.get("reviewer_findings_total", 0) for r in runs]
    findings = [f for f in findings if f]
    spans = [r["fan_out_span_seconds"] for r in runs if "fan_out_span_seconds" in r]

    def stats(values):
        if not values:
            return None
        return {
            "n": len(values),
            "min": min(values),
            "median": round(statistics.median(values), 1),
            "mean": round(statistics.mean(values), 1),
            "max": max(values),
            "total": sum(values),
        }

    agreement_hist = {str(n): all_consensus.count(n) for n in range(1, 8)}

    return {
        "runs_total": len(runs),
        "runs_complete": len(complete),
        "runs_with_synthesis": len(with_syn),
        "coverage_note": (
            "Metrics are reported only over the runs where the underlying file "
            "actually exists. Counts differ per metric on purpose."
        ),
        "reviewer_findings_per_run": stats(findings),
        "must_fix_items_per_run": stats(mustfix),
        "consensus_findings_per_run": stats(
            [len(r["synthesis"]["consensus_counts"]) for r in with_syn]
        ),
        "agreement_histogram_reviewers_per_finding": agreement_hist,
        "findings_confirmed_by_2_or_more": sum(1 for c in all_consensus if c >= 2),
        "findings_confirmed_by_4_or_more": sum(1 for c in all_consensus if c >= 4),
        "fan_out_span_seconds": stats(spans),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reviews_dir", nargs="?", default=DEFAULT_REVIEWS_DIR)
    ap.add_argument("-o", "--output", default="data/runs.json")
    args = ap.parse_args()

    if not os.path.isdir(args.reviews_dir):
        sys.exit("reviews dir not found: %s" % args.reviews_dir)

    dirs = sorted(
        os.path.join(args.reviews_dir, d)
        for d in os.listdir(args.reviews_dir)
        if os.path.isdir(os.path.join(args.reviews_dir, d))
    )
    runs = [analyse_run(d) for d in dirs]
    payload = {"summary": summarise(runs), "runs": runs}

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    print("\nwrote %s (%d runs)" % (args.output, len(runs)))


if __name__ == "__main__":
    main()
