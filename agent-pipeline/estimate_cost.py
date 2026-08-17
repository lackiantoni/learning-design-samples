#!/usr/bin/env python3
"""
Reconstruct the token and dollar cost of the multi-agent review pipeline.

This is NOT a metered invoice. The pipeline ran inside Claude Code on a
subscription, so no per-token bill exists for these runs. What this script does
is measure the real artifacts on disk, model the input and output of each agent
call, and price the result at published API rates.

Two things it deliberately does NOT do:

  1. Guess a single number. Polish markdown does not tokenize at a fixed rate,
     so every figure is reported as a band across a stated chars-per-token range.
  2. Claim completeness. The harness system prompt and tool-call overhead are not
     on disk and are not counted, so input tokens here are a FLOOR, not a total.

Usage:
    python3 estimate_cost.py [REVIEWS_DIR] [-a ARTICLES_DIR] [-o OUTPUT.json]
"""

import argparse
import json
import os
import statistics
import sys

REVIEWERS = [
    "devils-advocate",
    "client-avatar",
    "copy-coach",
    "seo-reviewer",
    "fact-checker",
    "brand-voice",
    "hook-coach",
]

DEFAULT_REVIEWS_DIR = (
    "/Users/antonilacki/Desktop/Claude/outputs/personal-brand/"
    "antonilackicom-blog/reviews"
)
DEFAULT_ARTICLES_DIR = (
    "/Users/antonilacki/Desktop/Claude/outputs/personal-brand/antonilackicom-blog"
)
DEFAULT_AGENTS_DIR = "/Users/antonilacki/Desktop/Claude/.claude/agents"

# Polish markdown tokenizes worse than English prose. Report a band rather than
# a point estimate; the low end of the band is the expensive end.
CHARS_PER_TOKEN = (2.8, 3.6)

# Claude Opus 5, USD per million tokens (cached rate, 2026-06-24).
PRICE_IN_PER_MTOK = 5.00
PRICE_OUT_PER_MTOK = 25.00


def size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def band(chars):
    """Return (high_tokens, low_tokens) for a character count."""
    return round(chars / CHARS_PER_TOKEN[0]), round(chars / CHARS_PER_TOKEN[1])


def agent_prompt_sizes(agents_dir, lang):
    out = {}
    for role in REVIEWERS + ["synthesizer"]:
        out[role] = size(os.path.join(agents_dir, "blog-%s-%s.md" % (lang, role)))
    return out


def find_article(articles_dir, slug):
    """The source article and the rewritten v2, if present.

    Published articles are moved to 1-online/, so look there too before
    giving up on a slug.
    """
    subdirs = ["", "1-online"]
    src = v2 = 0
    for sub in subdirs:
        src = src or size(os.path.join(articles_dir, sub, slug + ".md"))
        v2 = v2 or size(os.path.join(articles_dir, sub, slug + "-v2.md"))
    return src, v2


def analyse_run(run_dir, articles_dir, prompts):
    slug = os.path.basename(run_dir)
    article_chars, v2_chars = find_article(articles_dir, slug)

    review_chars = {}
    for role in REVIEWERS:
        review_chars[role] = size(os.path.join(run_dir, role + ".md"))
    synthesis_chars = size(os.path.join(run_dir, "_synthesis.md"))

    # Seven reviewers: each reads its own brief plus the whole article.
    reviewer_in = sum(prompts[r] + article_chars for r in REVIEWERS)
    reviewer_out = sum(review_chars.values())

    # Synthesiser: reads its brief, the article, and all seven reviews.
    # Writes the synthesis plus a rewritten article.
    synth_in = prompts["synthesizer"] + article_chars + reviewer_out
    synth_out = synthesis_chars + v2_chars

    in_chars = reviewer_in + synth_in
    out_chars = reviewer_out + synth_out

    in_hi, in_lo = band(in_chars)
    out_hi, out_lo = band(out_chars)

    def usd(tin, tout):
        return round(
            tin / 1e6 * PRICE_IN_PER_MTOK + tout / 1e6 * PRICE_OUT_PER_MTOK, 4
        )

    return {
        "slug": slug,
        "article_chars": article_chars,
        "v2_chars": v2_chars,
        "input_chars_floor": in_chars,
        "output_chars": out_chars,
        "input_tokens_band": [in_lo, in_hi],
        "output_tokens_band": [out_lo, out_hi],
        "usd_band": [usd(in_lo, out_lo), usd(in_hi, out_hi)],
        "agent_calls": len(REVIEWERS) + 1,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reviews_dir", nargs="?", default=DEFAULT_REVIEWS_DIR)
    ap.add_argument("-a", "--articles-dir", default=DEFAULT_ARTICLES_DIR)
    ap.add_argument("-g", "--agents-dir", default=DEFAULT_AGENTS_DIR)
    ap.add_argument("-l", "--lang", default="pl", choices=["pl", "en"])
    ap.add_argument("-o", "--output", default="data/cost.json")
    args = ap.parse_args()

    if not os.path.isdir(args.reviews_dir):
        sys.exit("reviews dir not found: %s" % args.reviews_dir)

    prompts = agent_prompt_sizes(args.agents_dir, args.lang)
    missing = [r for r, n in prompts.items() if not n]
    if missing:
        print("warning: no agent brief found for %s (counted as 0)" % ", ".join(missing))

    runs = []
    for name in sorted(os.listdir(args.reviews_dir)):
        path = os.path.join(args.reviews_dir, name)
        if os.path.isdir(path):
            runs.append(analyse_run(path, args.articles_dir, prompts))

    priced = [r for r in runs if r["article_chars"]]
    no_source = [r["slug"] for r in runs if not r["article_chars"]]

    def stat(key, idx):
        vals = [r[key][idx] for r in priced]
        return {
            "min": min(vals),
            "median": round(statistics.median(vals), 4),
            "max": max(vals),
        }

    summary = {
        "method": (
            "Reconstruction from measured artifact sizes, priced at published "
            "Claude Opus 5 API rates ($%.2f in / $%.2f out per million tokens). "
            "Not a metered invoice: these runs executed inside Claude Code on a "
            "subscription." % (PRICE_IN_PER_MTOK, PRICE_OUT_PER_MTOK)
        ),
        "chars_per_token_band": list(CHARS_PER_TOKEN),
        "input_is_a_floor": (
            "Harness system prompt and tool-call overhead are not on disk and "
            "are not counted. Real input tokens are higher than reported here."
        ),
        "runs_total": len(runs),
        "runs_priced": len(priced),
        "runs_without_source_article": no_source,
        "agent_calls_per_run": 8,
        "usd_per_run": {"low": stat("usd_band", 0), "high": stat("usd_band", 1)},
        "input_tokens_per_run": {
            "low": stat("input_tokens_band", 0),
            "high": stat("input_tokens_band", 1),
        },
        "output_tokens_per_run": {
            "low": stat("output_tokens_band", 0),
            "high": stat("output_tokens_band", 1),
        },
    }

    payload = {"summary": summary, "runs": runs}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nwrote %s" % args.output)


if __name__ == "__main__":
    main()
