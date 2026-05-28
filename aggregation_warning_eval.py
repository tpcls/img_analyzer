import json

from youtube_frame_fetcher import YouTubeFrameFetcher


def frame_item(second):
    return {
        "second": second,
        "frame": {"file_path": f"mock-{second}.jpg"},
        "result": {
            "ok": True,
            "analysis": {
                "upper_color": "purple",
                "lower_color": "purple",
                "lower_garment": "unknown",
                "lower_garment_family": "unknown",
                "pants_length": "unknown",
                "exposure": "medium",
                "skin_ratio": 0.16,
                "upper_skin_ratio": 0.18,
                "lower_skin_ratio": 0.12,
                "lower_coverage_ratio": 0.40,
                "lower_split_ratio": 0.0,
                "lower_center_fill_ratio": 0.0,
                "person_confidence": 0.50,
                "color_confidence": 0.45,
                "analysis_quality": "medium",
                "color_quality": "medium",
            },
        },
    }


def main():
    fetcher = YouTubeFrameFetcher()
    frames = [frame_item(second) for second in (5, 10, 15, 20, 30, 45, 60)]
    aggregate = fetcher.aggregate_clothing_results(frames, min_frames=7)
    warnings = aggregate.get("warnings", [])
    analysis = (aggregate.get("result") or {}).get("analysis") or {}
    output = {
        "ok": True,
        "warnings": warnings,
        "analysis": analysis,
        "needs_additional_sampling": fetcher.needs_additional_sampling(
            {"frames": frames, "final_clothing": aggregate},
            min_vote_frames=7,
        ),
    }

    checks = [
        analysis.get("lower_garment") == "unknown",
        analysis.get("lower_garment_unknown_frames") == 7,
        aggregate.get("lower_garment_decision", {}).get("reason") == "all_unknown",
        any("unknown in every voted frame" in warning for warning in warnings),
        not any("vote is weak" in warning for warning in warnings),
        any("purple clothing detected" in warning for warning in warnings),
        output["needs_additional_sampling"] is True,
    ]
    output["ok"] = all(checks)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if output["ok"] else 1)


if __name__ == "__main__":
    main()
