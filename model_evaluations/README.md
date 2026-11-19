# Model evaluations

Compare two vision models on every stored image before changing `VISION_MODEL`. Both
runs use Visia's own prompt, image preparation, JSON schema and settings; only the model
differs, and nothing is written to the database. Results hold the gallery's captions and
are kept out of git in `results/` (see `.gitignore`).

## Last evaluation

`results/2026-11-19_qwen3-vl-vs-qwen3-vl-abliterated/`: `qwen3-vl:8b-instruct` against
`huihui_ai/qwen3-vl-abliterated:8b-instruct` on 244 images: SigLIP caption match 0.1725 → 0.1711 (42 captions better, 47 worse), no failures, no captions outside English, 1.8 s an image. **Adopted**: about
even here and better on Lexia's documents, so one vision model, the one Luna Socia uses for
photos, now serves every app. `results/2026-11-10_qwen3-vl-vs-qwen3.5-9b/` compared
`qwen3.5:9b`, which was not adopted.

## What is scored

| Measure | What it shows |
| --- | --- |
| `failures` | Replies that broke the schema, had no usable tags, or timed out |
| `caption_match_mean` | SigLIP 2's cosine similarity between each caption and the image's stored embedding: the space meaning search uses, so higher means search finds the image more easily |
| `tags_match_mean` | The same for the tags, joined |
| `new_caption_better` / `new_caption_worse` | Images where the new caption scores more than 0.01 higher or lower |
| `tag_count_mean`, `caption_chars_median` | Whether captions got longer or tags fewer |
| `not_english` | Captions or tags with letters outside English, which the prompt asks for |
| `mean_seconds`, `total_minutes` | Speed, including model loads |

SigLIP cannot judge style or spot every wrong detail, so read the biggest drops that
`report.md` lists before deciding.

## Running one

```sh
C=luna_visia-mcp-1
docker exec $C mkdir -p /tmp/visia_eval
docker cp model_evaluations/run.py $C:/tmp/visia_eval/
docker cp model_evaluations/score.py $C:/tmp/visia_eval/
docker exec -e PYTHONPATH=/workspace/mcp $C python /tmp/visia_eval/run.py old huihui_ai/qwen3-vl-abliterated:8b-instruct /tmp/visia_eval/old.jsonl
docker exec -e PYTHONPATH=/workspace/mcp $C python /tmp/visia_eval/run.py new <model> /tmp/visia_eval/new.jsonl
docker exec -e PYTHONPATH=/workspace/mcp $C python /tmp/visia_eval/score.py /tmp/visia_eval
docker cp $C:/tmp/visia_eval model_evaluations/results/<date>_<old>-vs-<new>
```

`run.py` takes every image that has an embedding, in content-hash order; `--limit N`
keeps a trial run to the first N, and `--user` picks another person's gallery (default
`u_owner`). A run that stops resumes where it left off. `score.py` writes `scores.json`
(per image) and `report.md` (the summary, the biggest drops and gains, and failures).

Run Luna Sentia's `model_checks/check_model.py` on the new model first: it checks
vision, JSON output, `think: false` and the GPU fit in a minute.
