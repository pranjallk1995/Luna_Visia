"""Caption and tag every stored image with one model; write JSONL, never touch the database.

usage: python run.py <label: old|new> <model> <out.jsonl> [--limit N]
Runs inside the MCP container with the app's code on PYTHONPATH (/workspace/mcp). Both
labels use the app's own prompt and settings; only the model differs. A run that stops
resumes where it left off.
"""

import argparse
import json
import os
import time
import traceback
from pathlib import Path

import psycopg

from app.core.config import AppConfig
from app.core.models import MetadataRules
from app.integrations.vision import OllamaVisionAnalyzer

parser = argparse.ArgumentParser()
parser.add_argument("label", choices=["old", "new"])
parser.add_argument("model")
parser.add_argument("out", type=Path)
parser.add_argument("--limit", type=int, default=0, help="only the first N images (by content hash)")
parser.add_argument("--user", default="u_owner", help="whose gallery: the schema and folder name")
args = parser.parse_args()

config = AppConfig.from_env()
analyzer = OllamaVisionAnalyzer(config.ollama_url, args.model, MetadataRules(config.max_tags, config.search_stop_words))

with psycopg.connect(config.database_url) as conn:
    rows = conn.execute(
        f"SELECT name FROM {args.user}.image_metadata WHERE embedding IS NOT NULL ORDER BY content_sha256"
    ).fetchall()
image_dir = Path(os.environ.get("IMAGE_DIR", "/data/images")) / args.user
names = [name for (name,) in rows if (image_dir / name).exists()]
if args.limit:
    names = names[: args.limit]

done = set()
if args.out.exists():
    done = {json.loads(line)["name"] for line in args.out.read_text().splitlines() if line.strip()}

with args.out.open("a") as out:
    for i, name in enumerate(names, 1):
        if name in done:
            continue
        started = time.monotonic()
        row = {"name": name, "label": args.label, "model": args.model}
        try:
            analysis = analyzer.analyze((image_dir / name).read_bytes())
            row.update(caption=analysis.caption, tags=analysis.tags)
        except Exception as error:  # noqa: BLE001 - a failure is a result
            row.update(crash=f"{type(error).__name__}: {error}"[:500], trace=traceback.format_exc()[-1500:])
        row["seconds"] = round(time.monotonic() - started, 1)
        out.write(json.dumps(row) + "\n")
        out.flush()
        print(f"[{args.label}] {i}/{len(names)} {name} {row['seconds']}s", flush=True)
