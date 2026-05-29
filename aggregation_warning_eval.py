import json

from youtube_frame_fetcher import YouTubeFrameFetcher


def frame_item(second, lower_garment="unknown", lower_garment_family="unknown", pants_length="unknown"):
    return {
        "second": second,
        "frame": {"file_path": f"mock-{second}.jpg"},
        "result": {
            "ok": True,
            "analysis": {
                "upper_color": "purple",
                "lower_color": "purple",
                "lower_garment": lower_garment,
                "lower_garment_family": lower_garment_family,
                "pants_length": pants_length,
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
    partial_frames = [
        frame_item(5, "mini_skirt", "skirt", "shorts"),
        frame_item(10, "long_skirt", "skirt", "long"),
        *[frame_item(second) for second in (15, 20, 30, 45, 60)],
    ]
    partial_aggregate = fetcher.aggregate_clothing_results(partial_frames, min_frames=7)
    partial_analysis = (partial_aggregate.get("result") or {}).get("analysis") or {}
    partial_warnings = partial_aggregate.get("warnings", [])
    partial_output = {
        "usable": partial_aggregate.get("usable"),
        "warnings": partial_warnings,
        "analysis": partial_analysis,
        "needs_additional_sampling": fetcher.needs_additional_sampling(
            {"frames": partial_frames, "final_clothing": partial_aggregate},
            min_vote_frames=7,
        ),
    }
    output["partial_unknown"] = partial_output
    checks.extend(
        [
            partial_analysis.get("lower_garment_unknown_frames") == 5,
            partial_analysis.get("lower_garment_known_frames") == 2,
            partial_analysis.get("lower_garment") == "unknown",
            partial_output["usable"] is False,
            partial_output["needs_additional_sampling"] is True,
            any("only 2 voted frames" in warning for warning in partial_warnings),
        ]
    )
    sparse_frames = [
        frame_item(5, "mini_skirt", "unknown", "shorts"),
        frame_item(10, "mini_skirt", "unknown", "shorts"),
        frame_item(15, "long_skirt", "unknown", "long"),
        *[frame_item(second) for second in (20, 30, 45, 60, 75, 90, 120)],
    ]
    sparse_aggregate = fetcher.aggregate_clothing_results(sparse_frames, min_frames=7)
    sparse_analysis = (sparse_aggregate.get("result") or {}).get("analysis") or {}
    sparse_output = {
        "usable": sparse_aggregate.get("usable"),
        "warnings": sparse_aggregate.get("warnings", []),
        "decision": sparse_aggregate.get("lower_garment_decision", {}),
        "analysis": sparse_analysis,
    }
    output["sparse_known"] = sparse_output
    checks.extend(
        [
            sparse_analysis.get("lower_garment") == "mini_skirt",
            sparse_analysis.get("lower_garment_family") == "skirt",
            sparse_analysis.get("lower_garment_known_frames") == 3,
            sparse_analysis.get("lower_garment_family_known_frames") == 3,
            sparse_output["usable"] is False,
            sparse_output["decision"].get("reason") == "sparse_known",
            any("only 3 voted frames" in warning for warning in sparse_output["warnings"]),
        ]
    )
    output["ok"] = all(checks)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if output["ok"] else 1)


if __name__ == "__main__":
    main()
