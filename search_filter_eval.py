import json

from youtube_frame_fetcher import YouTubeFrameFetcher


def main():
    fetcher = YouTubeFrameFetcher()
    raw_results = [
        {
            "id": "good0000001",
            "title": "[MPD직캠] IVE WONYOUNG - REBEL HEART",
            "url": "https://www.youtube.com/watch?v=good0000001",
            "thumbnail_url": "",
        },
        {
            "id": "bad00000001",
            "title": "[MPD직캠] TWICE JEONGYEON - Cheer Up",
            "url": "https://www.youtube.com/watch?v=bad00000001",
            "thumbnail_url": "",
        },
        {
            "id": "bad00000002",
            "title": "[MPD직캠] 정연 - FANCY",
            "url": "https://www.youtube.com/watch?v=bad00000002",
            "thumbnail_url": "",
        },
        {
            "id": "bad00000002",
            "title": "[MPD직캠] 정연 - FANCY",
            "url": "https://www.youtube.com/watch?v=bad00000002",
            "thumbnail_url": "",
        },
        {
            "id": "wrongmember",
            "title": "[MPD직캠] IVE LIZ - REBEL HEART",
            "url": "https://www.youtube.com/watch?v=wrongmember",
            "thumbnail_url": "",
        },
        {
            "id": "good0000002",
            "title": "장원영 REBEL HEART 직캠",
            "url": "https://www.youtube.com/watch?v=good0000002",
            "thumbnail_url": "",
        },
    ]

    filtered = fetcher.filter_search_results("IVE Wonyoung", raw_results, limit=4)
    selected_ids = [item["id"] for item in filtered]
    expected_ids = ["good0000001", "good0000002"]
    parsed = fetcher.parse_video_info_line(
        "VpV5h0VCrt8|||[MPD직캠] TWICE JEONGYEON - FANCY|||NA|||M2",
        fallback_url="https://www.youtube.com/watch?v=VpV5h0VCrt8",
    )
    mismatch = fetcher.score_search_result("IVE Wonyoung", parsed["title"])
    ok = (
        selected_ids == expected_ids
        and parsed["id"] == "VpV5h0VCrt8"
        and parsed["thumbnail_url"].endswith("/VpV5h0VCrt8/hqdefault.jpg")
        and mismatch["accepted"] is False
    )
    output = {
        "ok": ok,
        "query": "IVE Wonyoung",
        "selected_ids": selected_ids,
        "expected_ids": expected_ids,
        "filtered": filtered,
        "direct_url_mismatch": {
            "selected": parsed,
            "query_match": mismatch,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
