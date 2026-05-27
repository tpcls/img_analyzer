import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from youtube_frame_fetcher import YouTubeFrameFetcher


def frame_second(path):
    return int(path.stem.rsplit("-", 1)[-1])


def video_prefix(path):
    return path.stem.rsplit("-", 1)[0]


def short_name(path):
    prefix = video_prefix(path).replace("youtube-only-", "")
    return f"{prefix[:8]}-{frame_second(path)}s"


def collect_frames(frame_dir):
    groups = defaultdict(list)
    for path in sorted(frame_dir.glob("*.jpg")):
        groups[video_prefix(path)].append(path)
    return {prefix: sorted(paths, key=frame_second) for prefix, paths in sorted(groups.items())}


def analyze_group(fetcher, paths, min_vote_frames):
    frames = [{"file_path": str(path), "second": frame_second(path)} for path in paths]
    frame_results = fetcher.analyze_frames_with_c_model(frames)
    aggregate = fetcher.aggregate_clothing_results(frame_results, min_frames=min_vote_frames)
    return frame_results, aggregate


def analysis_from(item):
    return ((item.get("result") or {}).get("analysis") or {})


def build_sheet(groups, group_results, output_path):
    font = ImageFont.load_default()
    cols = 4
    thumb_w = 280
    thumb_h = 156
    label_h = 86
    total_frames = sum(len(paths) for paths in groups.values())
    rows = max(1, (total_frames + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)

    index = 0
    for prefix, paths in groups.items():
        frame_results = group_results[prefix]["frame_results"]
        for path, item in zip(paths, frame_results):
            analysis = analysis_from(item)
            img = Image.open(path).convert("RGB")
            img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            x = (index % cols) * thumb_w
            y = (index // cols) * (thumb_h + label_h)
            sheet.paste(img, (x + (thumb_w - img.width) // 2, y))
            label = "\n".join(
                [
                    f"#{index + 1} {short_name(path)}",
                    f"{analysis.get('lower_garment')} len:{analysis.get('pants_length')} L:{analysis.get('lower_color')}",
                    f"pc:{analysis.get('person_confidence')} cc:{analysis.get('color_confidence')}",
                    f"skin:{analysis.get('lower_skin_ratio')} cov:{analysis.get('lower_coverage_ratio')}",
                    f"split:{analysis.get('lower_split_ratio')} center:{analysis.get('lower_center_fill_ratio')}",
                ]
            )
            draw.multiline_text((x + 4, y + thumb_h + 3), label, fill=(0, 0, 0), font=font, spacing=2)
            index += 1

    sheet.save(output_path, quality=92)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame-dir", default="cache/youtube_only/frames")
    parser.add_argument("--output-dir", default="eval_outputs")
    parser.add_argument("--analysis-width", type=int, default=384)
    parser.add_argument("--min-vote-frames", type=int, default=7)
    args = parser.parse_args()

    frame_dir = Path(args.frame_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    groups = collect_frames(frame_dir)
    if not groups:
        raise FileNotFoundError(f"No cached frames found in {frame_dir}")

    fetcher = YouTubeFrameFetcher(analysis_width=args.analysis_width)
    group_results = {}
    report_groups = []

    for prefix, paths in groups.items():
        frame_results, aggregate = analyze_group(fetcher, paths, args.min_vote_frames)
        group_results[prefix] = {"frame_results": frame_results, "aggregate": aggregate}
        analysis = analysis_from(aggregate)
        aggregation = aggregate.get("aggregation") or {}
        report_groups.append(
            {
                "prefix": prefix,
                "frames": len(paths),
                "usable": bool(aggregate.get("usable")),
                "warnings": aggregate.get("warnings", []),
                "analysis": {
                    "lower_garment": analysis.get("lower_garment"),
                    "pants_length": analysis.get("pants_length"),
                    "lower_color": analysis.get("lower_color"),
                    "person_confidence": analysis.get("person_confidence"),
                    "color_confidence": analysis.get("color_confidence"),
                    "lower_skin_ratio": analysis.get("lower_skin_ratio"),
                    "lower_coverage_ratio": analysis.get("lower_coverage_ratio"),
                    "lower_split_ratio": analysis.get("lower_split_ratio"),
                    "lower_center_fill_ratio": analysis.get("lower_center_fill_ratio"),
                    "lower_garment_vote_confidence": analysis.get("lower_garment_vote_confidence"),
                    "lower_garment_vote_margin": analysis.get("lower_garment_vote_margin"),
                },
                "votes": {
                    "lower_garment": (aggregation.get("votes") or {}).get("lower_garment", {}),
                    "pants_length": (aggregation.get("votes") or {}).get("pants_length", {}),
                },
                "seconds": aggregation.get("seconds", []),
            }
        )

    sheet_path = output_dir / "lower_garment_audit.jpg"
    json_path = output_dir / "lower_garment_audit.json"
    build_sheet(groups, group_results, sheet_path)

    usable_groups = [group for group in report_groups if group["usable"]]
    summary = {
        "video_groups": len(report_groups),
        "usable_video_groups": len(usable_groups),
        "frames": sum(group["frames"] for group in report_groups),
        "analysis_width": args.analysis_width,
        "min_vote_frames": args.min_vote_frames,
        "sheet_path": str(sheet_path),
    }
    report = {"summary": summary, "groups": report_groups}
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
