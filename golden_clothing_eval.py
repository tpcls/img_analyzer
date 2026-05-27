import argparse
import json
from pathlib import Path

from youtube_frame_fetcher import YouTubeFrameFetcher


USABLE_PERSON_THRESHOLD = 0.36
USABLE_COLOR_THRESHOLD = 0.40


# Labels were assigned by visual inspection of the cached frame contact sheets in
# eval_outputs/prediction_sheet_*.jpg. Low-quality wide/group frames are expected
# to be rejected instead of scored as reliable clothing predictions.
GOLDEN_SAMPLES = [
    {
        "file": "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-5.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "file": "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-5.jpg",
        "expected": {
            "upper_color": "white",
            "lower_color": "white",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "high",
        },
    },
    {"file": "youtube-only-8b48fea6f43bef22ba900f211ef12ce4cc8d98cb-5.jpg", "expected_usable": False},
    {"file": "youtube-only-a94d3c442af11ebeb100fcfa2cf877143298f111-5.jpg", "expected_usable": False},
    {
        "file": "youtube-only-d6e35cca84882bd260a1c16d1412d9edc9b9290d-5.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "file": "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-10.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "file": "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-10.jpg",
        "expected": {
            "upper_color": "white",
            "lower_color": "white",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "high",
        },
    },
    {
        "file": "youtube-only-cb661687647ad85aeb454de945079c5f2bbb77a9-10.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "file": "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-15.jpg",
        "expected": {
            "upper_color": "white",
            "lower_color": "white",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "low",
        },
    },
    {"file": "youtube-only-a94d3c442af11ebeb100fcfa2cf877143298f111-15.jpg", "expected_usable": False},
    {
        "file": "youtube-only-d6e35cca84882bd260a1c16d1412d9edc9b9290d-15.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "low",
        },
    },
    {
        "file": "youtube-only-cb661687647ad85aeb454de945079c5f2bbb77a9-20.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "file": "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-30.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "file": "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-30.jpg",
        "expected": {
            "upper_color": "white",
            "lower_color": "white",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "low",
        },
    },
    {"file": "youtube-only-a94d3c442af11ebeb100fcfa2cf877143298f111-30.jpg", "expected_usable": False},
    {
        "file": "youtube-only-8b48fea6f43bef22ba900f211ef12ce4cc8d98cb-75.jpg",
        "expected": {
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
        },
    },
    {
        "file": "youtube-only-8b48fea6f43bef22ba900f211ef12ce4cc8d98cb-90.jpg",
        "expected": {
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
        },
    },
    {
        "file": "youtube-only-8b48fea6f43bef22ba900f211ef12ce4cc8d98cb-120.jpg",
        "expected": {
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
        },
    },
    {
        "file": "youtube-only-d6e35cca84882bd260a1c16d1412d9edc9b9290d-30.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "low",
        },
    },
    {
        "file": "youtube-only-cb661687647ad85aeb454de945079c5f2bbb77a9-45.jpg",
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
]

GOLDEN_SEQUENCES = [
    {
        "name": "Karina seven-frame vote",
        "files": [
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-5.jpg",
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-10.jpg",
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-15.jpg",
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-20.jpg",
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-30.jpg",
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-45.jpg",
            "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20-60.jpg",
        ],
        "expected": {
            "upper_color": "black",
            "lower_color": "black",
            "lower_garment": "shorts",
            "pants_length": "shorts",
            "exposure": "medium",
        },
    },
    {
        "name": "Wonyoung seven-frame vote",
        "files": [
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-5.jpg",
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-10.jpg",
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-15.jpg",
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-20.jpg",
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-30.jpg",
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-45.jpg",
            "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473-60.jpg",
        ],
        "expected": {
            "upper_color": "white",
            "lower_color": "white",
            "lower_garment": "mini_skirt",
            "pants_length": "shorts",
            "exposure": "low",
        },
    },
]

GOLDEN_VIDEO_GROUPS = [
    {
        "name": "Karina full cached vote",
        "prefix": "youtube-only-2f768026e3f1a749b5a65e02e121f37a66319b20",
        "expected": {"lower_garment": "shorts", "pants_length": "shorts"},
        "min_lower_garment_vote_confidence": 0.90,
    },
    {
        "name": "Wonyoung full cached vote",
        "prefix": "youtube-only-7b61c781f17c2095badee0dbe0a437fec4ca7473",
        "expected": {"lower_garment": "mini_skirt", "pants_length": "shorts"},
        "min_lower_garment_vote_confidence": 0.90,
    },
    {
        "name": "Wide cage full cached vote",
        "prefix": "youtube-only-8b48fea6f43bef22ba900f211ef12ce4cc8d98cb",
        "expected_usable": False,
    },
    {
        "name": "Laser wide full cached vote",
        "prefix": "youtube-only-a94d3c442af11ebeb100fcfa2cf877143298f111",
        "expected_usable": False,
    },
    {
        "name": "Stage black skirt full cached vote",
        "prefix": "youtube-only-cb661687647ad85aeb454de945079c5f2bbb77a9",
        "expected": {"lower_garment": "mini_skirt", "pants_length": "shorts"},
        "min_lower_garment_vote_confidence": 0.60,
    },
    {
        "name": "Stage white shorts full cached vote",
        "prefix": "youtube-only-d6e35cca84882bd260a1c16d1412d9edc9b9290d",
        "expected": {"lower_garment": "shorts", "pants_length": "shorts"},
        "min_lower_garment_vote_confidence": 0.90,
    },
]


def is_usable(analysis):
    return (
        float(analysis.get("person_confidence", 0.0)) >= USABLE_PERSON_THRESHOLD
        and float(analysis.get("color_confidence", 0.0)) >= USABLE_COLOR_THRESHOLD
    )


def frames_for_prefix(frame_dir, prefix):
    paths = sorted(
        frame_dir.glob(f"{prefix}-*.jpg"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    if not paths:
        raise FileNotFoundError(f"No frames found for {prefix}")
    return [
        {"file_path": str(path), "second": int(path.stem.rsplit("-", 1)[-1])}
        for path in paths
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-width", type=int, default=384)
    parser.add_argument("--target", type=float, default=90.0)
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    frame_dir = base_dir / "cache" / "youtube_only" / "frames"
    fetcher = YouTubeFrameFetcher(analysis_width=args.analysis_width)

    frames = []
    for sample in GOLDEN_SAMPLES:
        path = frame_dir / sample["file"]
        if not path.exists():
            raise FileNotFoundError(path)
        frames.append({"file_path": str(path), "second": 0})

    analyzed = fetcher.analyze_frames_with_c_model(frames)
    total = 0
    correct = 0
    rows = []

    for sample, item in zip(GOLDEN_SAMPLES, analyzed):
        analysis = (item.get("result") or {}).get("analysis") or {}
        usable = is_usable(analysis)
        expected_usable = sample.get("expected_usable", True)

        if not expected_usable:
            total += 1
            ok = usable is False
            correct += int(ok)
            rows.append({"file": sample["file"], "check": "usable", "expected": False, "actual": usable, "ok": ok})
            continue

        expected = sample["expected"]
        actual = {key: analysis.get(key) for key in expected}
        for key, expected_value in expected.items():
            total += 1
            ok = analysis.get(key) == expected_value and usable
            correct += int(ok)
            rows.append(
                {
                    "file": sample["file"],
                    "check": key,
                    "expected": expected_value,
                    "actual": analysis.get(key),
                    "usable": usable,
                    "ok": ok,
                }
            )

    for sequence in GOLDEN_SEQUENCES:
        sequence_frames = []
        for file_name in sequence["files"]:
            path = frame_dir / file_name
            if not path.exists():
                raise FileNotFoundError(path)
            second = int(path.stem.rsplit("-", 1)[-1])
            sequence_frames.append({"file_path": str(path), "second": second})

        sequence_result = fetcher.aggregate_clothing_results(
            fetcher.analyze_frames_with_c_model(sequence_frames),
            min_frames=7,
        )
        analysis = (sequence_result.get("result") or {}).get("analysis") or {}
        aggregation = sequence_result.get("aggregation") or {}

        total += 1
        used_ok = aggregation.get("used_frames", 0) >= 7
        correct += int(used_ok)
        rows.append(
            {
                "file": sequence["name"],
                "check": "used_frames",
                "expected": ">=7",
                "actual": aggregation.get("used_frames", 0),
                "ok": used_ok,
            }
        )

        for key, expected_value in sequence["expected"].items():
            total += 1
            ok = analysis.get(key) == expected_value
            correct += int(ok)
            rows.append(
                {
                    "file": sequence["name"],
                    "check": f"aggregate_{key}",
                    "expected": expected_value,
                    "actual": analysis.get(key),
                    "votes": (aggregation.get("votes") or {}).get(key, {}),
                    "ok": ok,
                }
            )

    for group in GOLDEN_VIDEO_GROUPS:
        group_result = fetcher.aggregate_clothing_results(
            fetcher.analyze_frames_with_c_model(frames_for_prefix(frame_dir, group["prefix"])),
            min_frames=7,
        )
        analysis = (group_result.get("result") or {}).get("analysis") or {}
        aggregation = group_result.get("aggregation") or {}
        expected_usable = group.get("expected_usable", True)

        total += 1
        usable_ok = bool(group_result.get("usable")) is expected_usable
        correct += int(usable_ok)
        rows.append(
            {
                "file": group["name"],
                "check": "aggregate_usable",
                "expected": expected_usable,
                "actual": bool(group_result.get("usable")),
                "used_frames": aggregation.get("used_frames", 0),
                "ok": usable_ok,
            }
        )
        if not expected_usable:
            continue

        total += 1
        used_ok = aggregation.get("used_frames", 0) >= 7
        correct += int(used_ok)
        rows.append(
            {
                "file": group["name"],
                "check": "aggregate_used_frames",
                "expected": ">=7",
                "actual": aggregation.get("used_frames", 0),
                "ok": used_ok,
            }
        )

        for key, expected_value in group["expected"].items():
            total += 1
            ok = analysis.get(key) == expected_value
            correct += int(ok)
            rows.append(
                {
                    "file": group["name"],
                    "check": f"group_{key}",
                    "expected": expected_value,
                    "actual": analysis.get(key),
                    "votes": (aggregation.get("votes") or {}).get(key, {}),
                    "ok": ok,
                }
            )

        if "min_lower_garment_vote_confidence" in group:
            total += 1
            actual_confidence = float(analysis.get("lower_garment_vote_confidence", 0.0))
            expected_confidence = group["min_lower_garment_vote_confidence"]
            ok = actual_confidence >= expected_confidence
            correct += int(ok)
            rows.append(
                {
                    "file": group["name"],
                    "check": "lower_garment_vote_confidence",
                    "expected": f">={expected_confidence}",
                    "actual": actual_confidence,
                    "votes": (aggregation.get("votes") or {}).get("lower_garment", {}),
                    "ok": ok,
                }
            )

    accuracy = correct / total * 100.0 if total else 0.0
    output = {
        "summary": {
            "samples": len(GOLDEN_SAMPLES),
            "sequences": len(GOLDEN_SEQUENCES),
            "video_groups": len(GOLDEN_VIDEO_GROUPS),
            "checks": total,
            "correct": correct,
            "accuracy_percent": round(accuracy, 2),
            "target_percent": args.target,
            "target_met": accuracy >= args.target,
            "analysis_width": args.analysis_width,
        },
        "failures": [row for row in rows if not row["ok"]],
        "rows": rows,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if output["summary"]["target_met"] else 1)


if __name__ == "__main__":
    main()
