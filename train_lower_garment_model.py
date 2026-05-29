import argparse
import json
from pathlib import Path

from golden_clothing_eval import GOLDEN_SAMPLES, GOLDEN_SEQUENCES, GOLDEN_VIDEO_GROUPS
from lower_garment_model import DEFAULT_MODEL_PATH, save_model, train_softmax, vectorize_analysis
from youtube_frame_fetcher import YouTubeFrameFetcher


EXTRA_FRAME_LABELS = {
    "HmStZ8eQD98 gray wide skirt": {
        "prefix": "youtube-only-7e9c442392595cc79cf27b511c4f7b58c61bbe08",
        "label": "mini_skirt",
    },
    "jXcU22lLPlc black mini skirt": {
        "prefix": "youtube-only-d75ee9462fb527b9c2233a3a82fc6f2e61b21b2f",
        "label": "mini_skirt",
    },
    "l_lRA0DytNE black mini skirt": {
        "prefix": "youtube-only-2acc4c26f1a24498e3a4ad7466297804ad898519",
        "label": "mini_skirt",
    },
    "cSXqwcLsVPc dark shorts": {
        "prefix": "youtube-only-c9c31ce31de698480adbe62cbeb2ddfc731aae56",
        "label": "shorts",
    },
    "erNe2L0beR0 black mini skirt": {
        "prefix": "youtube-only-457426eca9078ad568a3be23df4814bd33d01f4e",
        "label": "mini_skirt",
    },
}


def collect_labels(frame_dir):
    labels = {}
    for sample in GOLDEN_SAMPLES:
        expected = sample.get("expected") or {}
        label = expected.get("lower_garment")
        if label:
            labels[sample["file"]] = label
    for sequence in GOLDEN_SEQUENCES:
        label = (sequence.get("expected") or {}).get("lower_garment")
        if not label:
            continue
        for filename in sequence.get("files") or []:
            labels.setdefault(filename, label)
    for group in GOLDEN_VIDEO_GROUPS:
        if group.get("expected_usable") is False:
            continue
        label = (group.get("expected") or {}).get("lower_garment")
        prefix = group.get("prefix")
        if not label or not prefix:
            continue
        for path in frame_dir.glob(f"{prefix}-*.jpg"):
            labels.setdefault(path.name, label)
        for path in frame_dir.glob(f"{prefix}-*.ppm"):
            labels.setdefault(path.name, label)
    for extra in EXTRA_FRAME_LABELS.values():
        prefix = extra["prefix"]
        label = extra["label"]
        for path in frame_dir.glob(f"{prefix}-*.jpg"):
            labels[path.name] = label
        for path in frame_dir.glob(f"{prefix}-*.ppm"):
            labels[path.name] = label
    return labels


def analyze_labeled_frames(fetcher, labels):
    items = []
    for filename, label in sorted(labels.items()):
        path = fetcher.frame_cache_dir / filename
        if not path.exists():
            continue
        ppm = fetcher.ensure_ppm(str(path))
        if not ppm.get("ok"):
            continue
        items.append((filename, label, ppm["ppm_path"]))
    analyses = []
    for index in range(0, len(items), 32):
        chunk = items[index : index + 32]
        result = fetcher.run_c_model_batch([ppm_path for _filename, _label, ppm_path in chunk])
        if not result.get("ok"):
            continue
        for (filename, label, _ppm_path), analysis in zip(chunk, result["analyses"]):
            analyses.append({"file": filename, "label": label, "analysis": analysis})
    return analyses


def split_train_eval(rows):
    holdout = {}
    train = []
    eval_rows = []
    for row in rows:
        label = row["label"]
        holdout.setdefault(label, row)
    holdout_ids = {id(row) for row in holdout.values()}
    for row in rows:
        if id(row) in holdout_ids:
            eval_rows.append(row)
        else:
            train.append(row)
    return train or rows, eval_rows


def evaluate(model, rows):
    if not rows:
        return {"total": 0, "correct": 0, "accuracy_percent": None, "rows": []}
    from lower_garment_model import predict

    output_rows = []
    correct = 0
    for row in rows:
        pred = predict(model, row["analysis"])
        ok = pred["label"] == row["label"]
        correct += int(ok)
        output_rows.append(
            {
                "file": row["file"],
                "expected": row["label"],
                "actual": pred["label"],
                "confidence": pred["confidence"],
                "ok": ok,
            }
        )
    return {
        "total": len(rows),
        "correct": correct,
        "accuracy_percent": round(correct * 100.0 / len(rows), 2),
        "rows": output_rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--analysis-width", type=int, default=384)
    args = parser.parse_args()

    fetcher = YouTubeFrameFetcher(analysis_width=args.analysis_width)
    labels = collect_labels(fetcher.frame_cache_dir)
    rows = analyze_labeled_frames(fetcher, labels)
    if len(rows) < 6:
        raise SystemExit("not enough labeled cached frames to train")
    train_rows, eval_rows = split_train_eval(rows)
    samples = [(vectorize_analysis(row["analysis"]), row["label"]) for row in train_rows]
    model = train_softmax(samples)
    model["source"] = "golden cached frames"
    model["eval"] = evaluate(model, eval_rows)
    model["train_eval"] = evaluate(model, train_rows)
    save_model(model, args.output)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(Path(args.output).resolve()),
                "training_samples": len(train_rows),
                "eval": model["eval"],
                "train_eval_summary": {
                    "total": model["train_eval"]["total"],
                    "correct": model["train_eval"]["correct"],
                    "accuracy_percent": model["train_eval"]["accuracy_percent"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
