"""Score old.jsonl against new.jsonl: how well captions and tags match each image, failures, time.

usage: python score.py <dir with old.jsonl and new.jsonl> [--user u_owner]
Runs inside the MCP container (it needs SigLIP 2 on the GPU and the stored image
embeddings). Writes scores.json and report.md to the same directory.

The main measure is SigLIP 2's cosine similarity between a caption (or the tags, joined)
and the image's stored embedding: the same model meaning search uses, so a caption that
scores higher is one search will find more easily. It cannot judge style or catch every
wrong detail, so read the examples report.md lists.
"""

import argparse
import json
import re
from pathlib import Path
from statistics import mean, median

import numpy as np
import psycopg

from app.core.config import AppConfig
from app.integrations.embeddings import SiglipEmbedder

parser = argparse.ArgumentParser()
parser.add_argument("dir", type=Path)
parser.add_argument("--user", default="u_owner")
args = parser.parse_args()

# Words that are not English (another script) in a caption the prompt asks for in English.
NON_LATIN = re.compile(r"[^\x00-\x7F‘’“”–—°é]")


def load(path: Path) -> dict:
    return {r["name"]: r for r in map(json.loads, path.read_text().splitlines()) if r}


runs = {label: load(args.dir / f"{label}.jsonl") for label in ("old", "new")}
names = sorted(set(runs["old"]) & set(runs["new"]))
with psycopg.connect(AppConfig.from_env().database_url) as conn:
    stored = {
        name: np.asarray(json.loads(vector), dtype=np.float32)
        for name, vector in conn.execute(
            f"SELECT name, embedding::text FROM {args.user}.image_metadata WHERE name = ANY(%s)", (names,)
        ).fetchall()
    }

siglip = SiglipEmbedder()


def similarity(text: str, name: str) -> float:
    return round(float(np.dot(siglip.embed_text(text), stored[name])), 4)


per_image = []
for name in names:
    entry = {"name": name}
    for label in ("old", "new"):
        row = runs[label][name]
        if "crash" in row:
            entry[label] = {"crash": row["crash"], "seconds": row["seconds"]}
            continue
        entry[label] = {
            "caption": row["caption"],
            "tags": row["tags"],
            "caption_match": similarity(row["caption"], name),
            "tags_match": similarity(", ".join(row["tags"]), name),
            "tag_count": len(row["tags"]),
            "caption_chars": len(row["caption"]),
            "not_english": bool(NON_LATIN.search(row["caption"] + " ".join(row["tags"]))),
            "seconds": row["seconds"],
        }
    per_image.append(entry)


def summarize(label: str) -> dict:
    scores = [e[label] for e in per_image]
    ok = [s for s in scores if "crash" not in s]
    return {
        "images": len(scores),
        "failures": len(scores) - len(ok),
        "caption_match_mean": round(mean(s["caption_match"] for s in ok), 4) if ok else None,
        "tags_match_mean": round(mean(s["tags_match"] for s in ok), 4) if ok else None,
        "tag_count_mean": round(mean(s["tag_count"] for s in ok), 1) if ok else None,
        "caption_chars_median": median(s["caption_chars"] for s in ok) if ok else None,
        "not_english": sum(s["not_english"] for s in ok),
        "mean_seconds": round(mean(s["seconds"] for s in scores), 1),
        "total_minutes": round(sum(s["seconds"] for s in scores) / 60, 1),
    }


both = [e for e in per_image if "crash" not in e["old"] and "crash" not in e["new"]]
for e in both:
    e["caption_gain"] = round(e["new"]["caption_match"] - e["old"]["caption_match"], 4)
summary = {
    "old": {"model": next(iter(runs["old"].values()))["model"], **summarize("old")},
    "new": {"model": next(iter(runs["new"].values()))["model"], **summarize("new")},
    "new_caption_better": sum(e["caption_gain"] > 0.01 for e in both),
    "new_caption_worse": sum(e["caption_gain"] < -0.01 for e in both),
}
(args.dir / "scores.json").write_text(json.dumps({"summary": summary, "per_image": per_image}, indent=1))

lines = ["# Visia caption evaluation", "", "```json", json.dumps(summary, indent=1), "```", ""]
for title, chosen in (
    ("Biggest drops (check these by hand)", sorted(both, key=lambda e: e["caption_gain"])[:10]),
    ("Biggest gains", sorted(both, key=lambda e: -e["caption_gain"])[:5]),
    ("Failures", [e for e in per_image if "crash" in e["old"] or "crash" in e["new"]][:10]),
):
    lines += [f"## {title}", ""]
    for e in chosen:
        lines.append(f"### {e['name']} ({e.get('caption_gain', 'failed')})")
        for label in ("old", "new"):
            s = e[label]
            text = s.get("crash") or f"{s['caption']} [{', '.join(s['tags'])}] ({s['caption_match']})"
            lines.append(f"- {label}: {text}")
        lines.append("")
(args.dir / "report.md").write_text("\n".join(lines))
print(json.dumps(summary, indent=1))
