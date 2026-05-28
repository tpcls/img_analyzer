import argparse
import json

from youtube_frame_fetcher import YouTubeFrameFetcher, parse_seconds


DEFAULT_QUERIES = [
    "IVE Wonyoung",
    "Lisa fancam solo focus",
    "Karina 입덕직캠",
]


def run_case(fetcher, query, seconds, auto_seconds, max_height, min_vote_frames):
    fancams = fetcher.get_fancams(query, limit=4)
    if not fancams:
        return {"query": query, "ok": False, "error": "no video found"}

    attempts = []
    candidate_results = []
    initial_seconds = parse_seconds(seconds)
    extra_sample_seconds = parse_seconds(auto_seconds)

    for selected in fancams:
        video = fetcher.download_fancam(
            selected["url"],
            max_height=max_height,
            sample_end_seconds=fetcher.sample_end_seconds(initial_seconds, extra_sample_seconds),
        )
        if not video.get("ok"):
            attempts.append({"selected": selected.get("title", ""), "ok": False, "error": video.get("error")})
            continue

        frames = fetcher.extract_sample_frames(
            video["file_path"],
            seconds=initial_seconds,
            prefix="youtube-only",
        )
        frame_clothing = fetcher.analyze_frames_with_c_model(
            frames,
            video_path=video["file_path"],
            prefix="youtube-only",
        )
        best = fetcher.aggregate_clothing_results(frame_clothing, min_frames=min_vote_frames)
        if best and (len(frames) < min_vote_frames or not best.get("usable", False)):
            already = {frame["second"] for frame in frames}
            extra_seconds = tuple(s for s in extra_sample_seconds if s not in already)
            if extra_seconds:
                extra_frames = fetcher.extract_sample_frames(
                    video["file_path"],
                    seconds=extra_seconds,
                    prefix="youtube-only",
                )
                frames = fetcher.merge_frames(frames, extra_frames)
                frame_clothing = fetcher.merge_frame_clothing(
                    frame_clothing,
                    fetcher.analyze_frames_with_c_model(
                        extra_frames,
                        video_path=video["file_path"],
                        prefix="youtube-only",
                    ),
                )
                best = fetcher.aggregate_clothing_results(frame_clothing, min_frames=min_vote_frames)

        best = best or {}
        analysis = (best.get("result") or {}).get("analysis") or {}
        attempts.append(
            {
                "selected": selected.get("title", ""),
                "ok": True,
                "usable": best.get("usable", False),
                "lower_garment": analysis.get("lower_garment"),
                "lower_garment_family": analysis.get("lower_garment_family"),
                "person_confidence": analysis.get("person_confidence"),
                "color_confidence": analysis.get("color_confidence"),
            }
        )
        candidate_results.append((selected, best))
        if best.get("usable", False):
            break

    successful = [attempt for attempt in attempts if attempt.get("ok")]
    if not successful:
        return {"query": query, "ok": False, "selected": attempts[0].get("selected", ""), "attempts": attempts, "error": attempts[0].get("error")}

    best_result_index = next((idx for idx, (_selected, item) in enumerate(candidate_results) if item.get("usable", False)), None)
    if best_result_index is None:
        best_result_index = 0
    selected, best = candidate_results[best_result_index]
    analysis = (best.get("result") or {}).get("analysis") or {}
    return {
        "query": query,
        "ok": True,
        "selected": selected.get("title", ""),
        "attempts": attempts,
        "best_second": best.get("second"),
        "usable": best.get("usable", False),
        "warnings": best.get("warnings", []),
        "frame": (best.get("frame") or {}).get("file_path", ""),
        "aggregation": best.get("aggregation", {}),
        "analysis": {
            "upper_color": analysis.get("upper_color"),
            "lower_color": analysis.get("lower_color"),
            "lower_garment": analysis.get("lower_garment"),
            "lower_garment_family": analysis.get("lower_garment_family"),
            "lower_garment_vote_confidence": analysis.get("lower_garment_vote_confidence"),
            "lower_garment_vote_margin": analysis.get("lower_garment_vote_margin"),
            "lower_garment_family_vote_confidence": analysis.get("lower_garment_family_vote_confidence"),
            "lower_garment_family_vote_margin": analysis.get("lower_garment_family_vote_margin"),
            "pants_length": analysis.get("pants_length"),
            "exposure": analysis.get("exposure"),
            "person_confidence": analysis.get("person_confidence"),
            "color_confidence": analysis.get("color_confidence"),
            "analysis_quality": analysis.get("analysis_quality"),
            "color_quality": analysis.get("color_quality"),
            "subject_bbox": analysis.get("subject_bbox"),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("queries", nargs="*", default=DEFAULT_QUERIES)
    parser.add_argument("--seconds", default="5,10,15,20,30,45,60")
    parser.add_argument("--auto-seconds", default="5,10,15,20,30,45,60,75,90,120")
    parser.add_argument("--max-height", type=int, default=480)
    parser.add_argument("--analysis-width", type=int, default=384)
    parser.add_argument("--min-vote-frames", type=int, default=7)
    args = parser.parse_args()

    fetcher = YouTubeFrameFetcher(analysis_width=args.analysis_width)
    results = [
        run_case(fetcher, query, args.seconds, args.auto_seconds, args.max_height, args.min_vote_frames)
        for query in args.queries
    ]
    ok_results = [result for result in results if result.get("ok")]
    usable_results = [result for result in ok_results if result.get("usable")]
    summary = {
        "total": len(results),
        "ok": len(ok_results),
        "usable": len(usable_results),
        "usable_rate_percent": round((len(usable_results) / len(ok_results) * 100.0), 1) if ok_results else 0.0,
        "target_percent": 90.0,
        "target_met": (len(usable_results) / len(ok_results) * 100.0) >= 90.0 if ok_results else False,
    }
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
