import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import requests


class YouTubeFrameFetcher:
    def __init__(self, analysis_width=384):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        self.base_dir = Path(__file__).resolve().parent
        self.cache_dir = self.base_dir / "cache" / "youtube_only"
        self.search_cache_dir = self.cache_dir / "searches"
        self.thumbnail_cache_dir = self.cache_dir / "thumbnails"
        self.video_cache_dir = self.cache_dir / "videos"
        self.frame_cache_dir = self.cache_dir / "frames"
        self.ppm_cache_dir = self.cache_dir / "ppm"
        self.cache_ttl_seconds = 24 * 60 * 60
        self.search_ttl_seconds = 6 * 60 * 60
        self.yt_dlp_upgrade_interval_seconds = int(
            float(os.environ.get("YT_DLP_UPGRADE_INTERVAL_HOURS", "24")) * 60 * 60
        )
        self.analysis_width = analysis_width
        self.yt_dlp = self.find_executable("yt-dlp")
        self.ffmpeg = self.find_executable("ffmpeg")

        for directory in (
            self.search_cache_dir,
            self.thumbnail_cache_dir,
            self.video_cache_dir,
            self.frame_cache_dir,
            self.ppm_cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def find_executable(self, name):
        venv_candidate = Path(sys.executable).resolve().parent / name
        if venv_candidate.exists():
            return str(venv_candidate)
        found = shutil.which(name)
        if found:
            return found
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(prefix) / name
            if candidate.exists():
                return str(candidate)
        if name == "yt-dlp":
            probe = subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if probe.returncode == 0:
                return [sys.executable, "-m", "yt_dlp"]
        return name

    def command_args(self, executable):
        if isinstance(executable, (list, tuple)):
            return [str(part) for part in executable]
        return [str(executable)]

    def maybe_upgrade_yt_dlp(self):
        if self.yt_dlp_upgrade_interval_seconds <= 0:
            return

        marker = self.cache_dir / ".yt_dlp_upgrade_check"
        lock = self.cache_dir / ".yt_dlp_upgrade.lock"
        now = time.time()
        try:
            if marker.exists() and now - marker.stat().st_mtime < self.yt_dlp_upgrade_interval_seconds:
                return
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
        except OSError:
            return

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(str(int(now)))
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode == 0:
                marker.write_text(str(int(time.time())), encoding="utf-8")
                self.yt_dlp = self.find_executable("yt-dlp")
            elif not marker.exists():
                marker.write_text(str(int(time.time())), encoding="utf-8")
        except Exception:
            if not marker.exists():
                try:
                    marker.write_text(str(int(time.time())), encoding="utf-8")
                except Exception:
                    pass
        finally:
            try:
                lock.unlink(missing_ok=True)
            except Exception:
                pass

    def cache_key(self, value):
        return hashlib.sha1(str(value).encode("utf-8")).hexdigest()

    def read_json_cache(self, path, ttl_seconds):
        try:
            if path.exists() and time.time() - path.stat().st_mtime <= ttl_seconds:
                with open(path, "r", encoding="utf-8") as fp:
                    return json.load(fp)
        except Exception:
            pass
        return None

    def write_json_cache(self, path, data):
        try:
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            tmp_path.replace(path)
        except Exception:
            pass

    def extract_youtube_id(self, url):
        patterns = [
            r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
            r"^([A-Za-z0-9_-]{11})$",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    def get_fancams(self, query_name, limit=5):
        search_query = f"{query_name} 직캠 fancam"
        cache_path = self.search_cache_dir / f"{self.cache_key(f'{search_query}|limit={limit}')}.json"
        cached = self.read_json_cache(cache_path, self.search_ttl_seconds)
        if cached is not None:
            return cached

        cmd = [
            *self.command_args(self.yt_dlp),
            "--flat-playlist",
            "--no-warnings",
            "--print",
            "%(id)s|||%(title)s|||%(thumbnail)s",
            f"ytsearch{limit}:{search_query}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "yt-dlp search failed").strip()[-800:])

        fancams = []
        for line in result.stdout.strip().splitlines():
            if "|||" not in line:
                continue
            parts = line.split("|||", 2)
            if len(parts) < 2:
                continue
            video_id = parts[0].strip()
            title = parts[1].strip()
            thumbnail_url = parts[2].strip() if len(parts) > 2 else ""
            if not video_id or not title or video_id == "NA":
                continue
            if not thumbnail_url or thumbnail_url == "NA":
                thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            fancams.append(
                {
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "id": video_id,
                    "thumbnail_url": thumbnail_url,
                }
            )

        self.write_json_cache(cache_path, fancams)
        return fancams

    def get_thumbnail_frame(self, url, thumbnail_url=""):
        video_id = self.extract_youtube_id(url)
        if not thumbnail_url:
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else url

        suffix = ".jpg"
        if ".webp" in thumbnail_url.lower():
            suffix = ".webp"
        elif ".png" in thumbnail_url.lower():
            suffix = ".png"

        thumb_path = self.thumbnail_cache_dir / f"{video_id or self.cache_key(thumbnail_url)}{suffix}"
        if thumb_path.exists() and time.time() - thumb_path.stat().st_mtime <= self.cache_ttl_seconds:
            return self.file_payload(thumb_path, cached=True)

        resp = requests.get(thumbnail_url, headers=self.headers, timeout=8)
        if resp.status_code >= 400 or not resp.content:
            fallback = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
            if fallback and fallback != thumbnail_url:
                resp = requests.get(fallback, headers=self.headers, timeout=8)

        if resp.status_code >= 400 or not resp.content:
            return {"ok": False, "error": "thumbnail download failed"}

        thumb_path.write_bytes(resp.content)
        return self.file_payload(thumb_path, cached=False)

    def find_cached_video(self, video_id):
        if not video_id:
            return None
        matches = sorted(
            (p for p in self.video_cache_dir.iterdir() if p.is_file() and f"[{video_id}]" in p.name),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in matches:
            if path.is_file() and time.time() - path.stat().st_mtime <= self.cache_ttl_seconds:
                if self.has_video_stream(path):
                    return path.resolve()
        return None

    def has_video_stream(self, path):
        cmd = [
            self.find_executable("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0 and "video" in result.stdout
        except Exception:
            return False

    def download_fancam(self, url, max_height=720):
        self.maybe_upgrade_yt_dlp()
        video_id = self.extract_youtube_id(url)
        cached = self.find_cached_video(video_id)
        if cached:
            return self.file_payload(cached, cached=True)

        format_selectors = [
            (
                f"bv*[vcodec^=avc1][height<={max_height}][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/"
                f"bv*[vcodec!=none][height<={max_height}][ext=mp4]+ba[acodec!=none]/"
                f"b[vcodec!=none][height<={max_height}][ext=mp4]/"
                f"bv*[vcodec!=none][height<={max_height}]+ba[acodec!=none]/"
                f"b[vcodec!=none][height<={max_height}]"
            ),
            (
                f"bv*[height<={max_height}]+ba/"
                f"b[height<={max_height}]/"
                f"bestvideo[height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]"
            ),
            "bv*+ba/bestvideo+bestaudio/best",
        ]
        output_template = str(self.video_cache_dir / "%(title).120s [%(id)s].%(ext)s")
        last_error = ""
        result = None
        youtube_client_args = [
            [],
            ["--extractor-args", "youtube:player_client=android,web"],
            ["--extractor-args", "youtube:player_client=tv,web"],
            ["--force-ipv4"],
        ]
        for client_args in youtube_client_args:
            for format_selector in format_selectors:
                cmd = [
                    *self.command_args(self.yt_dlp),
                    "--no-playlist",
                    "--no-warnings",
                    "--concurrent-fragments",
                    "4",
                    "--retries",
                    "2",
                    "--fragment-retries",
                    "2",
                    "--no-part",
                    *client_args,
                    "--format",
                    format_selector,
                    "--merge-output-format",
                    "mp4",
                    "--print",
                    "after_move:filepath",
                    "--output",
                    output_template,
                    url,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    break
                last_error = (result.stderr or "yt-dlp download failed").strip()[-800:]
            if result and result.returncode == 0:
                break
        if result is None or result.returncode != 0:
            return {"ok": False, "error": last_error or "yt-dlp download failed"}

        file_path = ""
        for line in result.stdout.strip().splitlines():
            candidate = line.strip()
            if candidate and Path(candidate).exists():
                file_path = candidate

        if not file_path:
            files = sorted(self.video_cache_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            file_path = str(next((p for p in files if self.has_video_stream(p)), "")) if files else ""
        if not file_path:
            return {"ok": False, "error": "downloaded file not found"}
        if not self.has_video_stream(file_path):
            return {"ok": False, "error": f"downloaded file has no video stream: {Path(file_path).name}"}

        return self.file_payload(Path(file_path).resolve(), cached=False)

    def extract_sample_frames(self, video_path, seconds=(5, 10, 15), prefix="sample"):
        video = Path(video_path)
        video_key = self.cache_key(str(video.resolve()))
        frames = []
        missing_seconds = []

        for second in seconds:
            fp = self.frame_cache_dir / f"{prefix}-{video_key}-{second}.jpg"
            if fp.exists() and time.time() - fp.stat().st_mtime <= self.cache_ttl_seconds:
                frames.append(self.frame_payload(fp, second, cached=True))
            else:
                missing_seconds.append(second)

        if missing_seconds:
            select_parts = "+".join(f"gte(t\\,{s})*lt(t\\,{s + 1})" for s in missing_seconds)
            tmp_pattern = str(self.frame_cache_dir / f"_batch-{video_key}-{prefix}-%d.jpg")
            cmd = [
                self.ffmpeg,
                "-y",
                "-i",
                str(video),
                "-vf",
                f"select='{select_parts}',scale=640:-2:force_original_aspect_ratio=decrease",
                "-vsync",
                "0",
                "-frames:v",
                str(len(missing_seconds)),
                "-q:v",
                "4",
                tmp_pattern,
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=90)

            for idx, second in enumerate(sorted(missing_seconds), start=1):
                src = Path(tmp_pattern.replace("%d", str(idx)))
                dst = self.frame_cache_dir / f"{prefix}-{video_key}-{second}.jpg"
                if src.exists():
                    src.replace(dst)

                if not dst.exists():
                    fallback_cmd = [
                        self.ffmpeg,
                        "-y",
                        "-ss",
                        str(second),
                        "-i",
                        str(video),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=640:-2:force_original_aspect_ratio=decrease",
                        "-q:v",
                        "4",
                        str(dst),
                    ]
                    subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=30)

                if dst.exists():
                    frames.append(self.frame_payload(dst, second, cached=False))

            for leftover in self.frame_cache_dir.glob(f"_batch-{video_key}-{prefix}-*.jpg"):
                leftover.unlink(missing_ok=True)

        return sorted(frames, key=lambda f: f["second"])

    def file_payload(self, path, cached):
        path = Path(path).resolve()
        return {
            "ok": True,
            "file_path": str(path),
            "file_url": path.as_uri(),
            "filename": path.name,
            "cached": cached,
            "bytes": path.stat().st_size if path.exists() else 0,
        }

    def frame_payload(self, path, second, cached):
        payload = self.file_payload(path, cached)
        payload["second"] = second
        return payload

    def analyze_with_c_model(self, image_path):
        source = Path(image_path)
        ppm = self.ensure_ppm(source)
        if not ppm.get("ok"):
            return ppm

        batch = self.run_c_model_batch([ppm["ppm_path"]])
        if not batch.get("ok"):
            return batch
        analysis = batch["analyses"][0]
        return {
            "ok": True,
            "source": str(source.resolve()),
            "ppm_path": ppm["ppm_path"],
            "analysis": analysis,
        }

    def ensure_analyzer(self):
        analyzer = self.base_dir / "clothing_analyzer"
        if analyzer.exists():
            return {"ok": True, "path": str(analyzer)}
        make_result = subprocess.run(
            ["make"],
            cwd=self.base_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if make_result.returncode != 0:
            return {"ok": False, "error": (make_result.stderr or "make failed").strip()[-800:]}
        return {"ok": True, "path": str(analyzer)}

    def ensure_ppm(self, image_path):
        source = Path(image_path)
        ppm_path = self.ppm_cache_dir / f"{self.cache_key(f'{source.resolve()}|w={self.analysis_width}')}.ppm"
        if ppm_path.exists() and time.time() - ppm_path.stat().st_mtime <= self.cache_ttl_seconds:
            return {"ok": True, "ppm_path": str(ppm_path.resolve()), "cached": True}

        convert_cmd = [
            self.ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vf",
            f"scale={self.analysis_width}:-2:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            "-f",
            "image2",
            str(ppm_path),
        ]
        convert_result = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=30)
        if convert_result.returncode != 0 or not ppm_path.exists():
            return {"ok": False, "error": (convert_result.stderr or "PPM conversion failed").strip()[-800:]}
        return {"ok": True, "ppm_path": str(ppm_path.resolve()), "cached": False}

    def run_c_model_batch(self, ppm_paths):
        analyzer = self.ensure_analyzer()
        if not analyzer.get("ok"):
            return analyzer
        run_env = os.environ.copy()
        run_env["LC_ALL"] = "C"
        result = subprocess.run(
            [analyzer["path"], *ppm_paths],
            cwd=self.base_dir,
            capture_output=True,
            text=True,
            env=run_env,
            timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or "clothing analyzer failed").strip()[-800:]}

        try:
            analyses = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": "clothing analyzer returned invalid JSON", "raw": result.stdout}

        if isinstance(analyses, dict):
            analyses = [analyses]
        return {"ok": True, "analyses": analyses}

    def clothing_result_score(self, item):
        analysis = item["result"].get("analysis", {})
        person_confidence = float(analysis.get("person_confidence", 0.0))
        color_confidence = float(analysis.get("color_confidence", 0.0))
        skin_ratio = float(analysis.get("skin_ratio", 0.0))
        score_value = person_confidence * 0.55 + color_confidence * 0.35
        if analysis.get("exposure") != "low":
            score_value += 0.06
        if analysis.get("pants_length") != "unknown":
            score_value += 0.03
        if skin_ratio < 0.08:
            score_value -= 0.10
        elif skin_ratio > 0.90:
            score_value -= 0.06
        return score_value

    def is_clothing_result_usable(self, item):
        analysis = item.get("result", {}).get("analysis", {})
        return (
            float(analysis.get("person_confidence", 0.0)) >= 0.36
            and float(analysis.get("color_confidence", 0.0)) >= 0.40
        )

    def scored_clothing_items(self, frame_clothing):
        scored = []
        for item in frame_clothing:
            if not item.get("result", {}).get("ok"):
                continue
            analysis = item.get("result", {}).get("analysis") or {}
            score = self.clothing_result_score(item)
            usable = self.is_clothing_result_usable(item)
            scored.append({"item": item, "analysis": analysis, "score": score, "usable": usable})
        scored.sort(key=lambda entry: entry["score"], reverse=True)
        return scored

    def select_best_clothing_result(self, frame_clothing):
        scored = self.scored_clothing_items(frame_clothing)
        if not scored:
            return None

        best_entry = scored[0]
        best = best_entry["item"]
        analysis = best.get("result", {}).get("analysis", {})
        best["usable"] = best_entry["usable"]
        warnings = []
        if not best["usable"]:
            warnings.append("low person confidence; frame is likely a wide/group/poorly lit shot")
        if float(analysis.get("color_confidence", 0.0)) < 0.30:
            warnings.append("low color confidence; stage lighting or missing skin calibration may distort clothing colors")
        if warnings:
            best["warnings"] = warnings
        return best

    def aggregate_clothing_results(self, frame_clothing, min_frames=7):
        ranked = self.scored_clothing_items(frame_clothing)
        if not ranked:
            return None

        selected = [entry for entry in ranked if entry["usable"]]
        if len(selected) < min_frames:
            selected_ids = {id(entry["item"]) for entry in selected}
            for entry in ranked:
                if id(entry["item"]) not in selected_ids:
                    selected.append(entry)
                    if len(selected) >= min_frames:
                        break
        if not selected:
            selected = ranked[:1]

        def numeric_mean(name):
            values = [float(entry["analysis"].get(name, 0.0)) for entry in selected]
            return round(sum(values) / len(values), 4) if values else 0.0

        def vote(name):
            counts = Counter()
            tie_scores = Counter()
            for entry in selected:
                value = entry["analysis"].get(name)
                if not value or value == "unknown":
                    continue
                counts[value] += 1
                tie_scores[value] += entry["score"]
            if not counts:
                return "unknown", {}, 0.0, 0
            winner = max(counts, key=lambda value: (counts[value], tie_scores[value]))
            ordered_counts = sorted(counts.values(), reverse=True)
            runner_up = ordered_counts[1] if len(ordered_counts) > 1 else 0
            confidence = counts[winner] / sum(counts.values())
            return winner, dict(counts), round(confidence, 4), counts[winner] - runner_up

        analysis = {}
        votes = {}
        vote_confidence = {}
        vote_margin = {}
        for key in ("upper_color", "lower_color", "lower_garment", "pants_length", "exposure"):
            analysis[key], votes[key], vote_confidence[key], vote_margin[key] = vote(key)

        analysis["skin_ratio"] = numeric_mean("skin_ratio")
        analysis["upper_skin_ratio"] = numeric_mean("upper_skin_ratio")
        analysis["lower_skin_ratio"] = numeric_mean("lower_skin_ratio")
        analysis["lower_coverage_ratio"] = numeric_mean("lower_coverage_ratio")
        analysis["lower_split_ratio"] = numeric_mean("lower_split_ratio")
        analysis["lower_center_fill_ratio"] = numeric_mean("lower_center_fill_ratio")
        analysis["lower_garment_vote_confidence"] = vote_confidence["lower_garment"]
        analysis["lower_garment_vote_margin"] = vote_margin["lower_garment"]
        analysis["person_confidence"] = numeric_mean("person_confidence")
        analysis["color_confidence"] = numeric_mean("color_confidence")
        analysis["analysis_quality"] = (
            "high" if analysis["person_confidence"] >= 0.62 else "medium" if analysis["person_confidence"] >= 0.36 else "low"
        )
        analysis["color_quality"] = (
            "high" if analysis["color_confidence"] >= 0.58 else "medium" if analysis["color_confidence"] >= 0.30 else "low"
        )
        lower_garment_decision = {
            "label": analysis["lower_garment"],
            "confidence": analysis["lower_garment_vote_confidence"],
            "margin": analysis["lower_garment_vote_margin"],
            "votes": votes["lower_garment"],
            "needs_review": analysis["lower_garment_vote_confidence"] < 0.75,
        }

        representative = selected[0]["item"]
        result = {
            "second": representative.get("second"),
            "frame": representative.get("frame", {}),
            "result": {"ok": True, "analysis": analysis},
            "usable": analysis["person_confidence"] >= 0.36 and analysis["color_confidence"] >= 0.40,
            "lower_garment_decision": lower_garment_decision,
            "aggregation": {
                "method": "majority_vote",
                "min_frames": min_frames,
                "used_frames": len(selected),
                "available_frames": len(ranked),
                "seconds": [entry["item"].get("second") for entry in selected],
                "votes": votes,
                "vote_confidence": vote_confidence,
                "vote_margin": vote_margin,
            },
        }

        warnings = []
        if len(selected) < min_frames:
            warnings.append(f"only {len(selected)} valid frames were available for voting")
        if not result["usable"]:
            warnings.append("aggregated confidence is low; frame set is likely wide/group/poorly lit")
        if result["usable"] and lower_garment_decision["needs_review"]:
            warnings.append("lower garment vote is weak; add more frames or inspect manually")
        if warnings:
            result["warnings"] = warnings
        return result

    def analyze_frames_with_c_model(self, frames):
        prepared = []
        results = []
        for frame in frames:
            ppm = self.ensure_ppm(frame["file_path"])
            if ppm.get("ok"):
                prepared.append((frame, ppm))
            else:
                results.append({"second": frame["second"], "frame": frame, "result": ppm})

        if prepared:
            batch = self.run_c_model_batch([ppm["ppm_path"] for _, ppm in prepared])
            if batch.get("ok"):
                for (frame, ppm), analysis in zip(prepared, batch["analyses"]):
                    results.append(
                        {
                            "second": frame["second"],
                            "frame": frame,
                            "result": {
                                "ok": True,
                                "source": str(Path(frame["file_path"]).resolve()),
                                "ppm_path": ppm["ppm_path"],
                                "analysis": analysis,
                            },
                        }
                    )
            else:
                for frame, _ppm in prepared:
                    results.append({"second": frame["second"], "frame": frame, "result": batch})

        return sorted(results, key=lambda item: item["second"])

    def merge_frame_clothing(self, existing, additional):
        by_second = {item["second"]: item for item in existing}
        for item in additional:
            by_second[item["second"]] = item
        return [by_second[second] for second in sorted(by_second)]

    def merge_frames(self, existing, additional):
        by_second = {frame["second"]: frame for frame in existing}
        for frame in additional:
            by_second[frame["second"]] = frame
        return [by_second[second] for second in sorted(by_second)]

    def analyze_youtube_url(
        self,
        url,
        query="",
        thumbnail_url="",
        seconds=(5, 10, 15, 20, 30, 45, 60),
        auto_seconds=(5, 10, 15, 20, 30, 45, 60, 75, 90, 120),
        max_height=480,
        min_vote_frames=7,
        analyze_clothing=True,
        skip_video=False,
        no_auto_sample=False,
        include_thumbnail_analysis=False,
    ):
        selected = {
            "title": "",
            "url": url,
            "id": self.extract_youtube_id(url),
            "thumbnail_url": thumbnail_url,
        }
        output = {
            "ok": True,
            "query": query,
            "selected": selected,
            "search_results": [selected],
            "thumbnail": self.get_thumbnail_frame(url, thumbnail_url),
        }

        if include_thumbnail_analysis and analyze_clothing and output["thumbnail"].get("ok"):
            output["thumbnail_clothing"] = self.analyze_with_c_model(output["thumbnail"]["file_path"])

        if skip_video:
            return output

        video = self.download_fancam(url, max_height=max_height)
        output["video"] = video
        if not video.get("ok"):
            output["ok"] = False
            output["error"] = video.get("error", "video download failed")
            return output

        output["frames"] = self.extract_sample_frames(
            video["file_path"],
            seconds=seconds,
            prefix="youtube-only",
        )
        if not analyze_clothing:
            return output

        output["frame_clothing"] = self.analyze_frames_with_c_model(output["frames"])
        output["best_frame_clothing"] = self.select_best_clothing_result(output["frame_clothing"])
        output["final_clothing"] = self.aggregate_clothing_results(
            output["frame_clothing"],
            min_frames=min_vote_frames,
        )

        if (
            not no_auto_sample
            and (
                len(output["frames"]) < min_vote_frames
                or (
                    output["final_clothing"]
                    and not output["final_clothing"].get("usable", False)
                )
            )
        ):
            already = {frame["second"] for frame in output["frames"]}
            extra_seconds = tuple(s for s in auto_seconds if s not in already)
            output["auto_sampled"] = False
            if extra_seconds:
                extra_frames = self.extract_sample_frames(
                    video["file_path"],
                    seconds=extra_seconds,
                    prefix="youtube-only",
                )
                output["frames"] = self.merge_frames(output["frames"], extra_frames)
                output["frame_clothing"] = self.merge_frame_clothing(
                    output["frame_clothing"],
                    self.analyze_frames_with_c_model(extra_frames),
                )
                output["best_frame_clothing"] = self.select_best_clothing_result(output["frame_clothing"])
                output["final_clothing"] = self.aggregate_clothing_results(
                    output["frame_clothing"],
                    min_frames=min_vote_frames,
                )
                output["auto_sampled"] = True

        return output


def parse_seconds(value):
    return tuple(int(v.strip()) for v in value.split(",") if v.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="IVE Wonyoung")
    parser.add_argument("--url", default="")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--seconds", default="5,10,15,20,30,45,60")
    parser.add_argument("--auto-seconds", default="5,10,15,20,30,45,60,75,90,120")
    parser.add_argument("--max-height", type=int, default=480)
    parser.add_argument("--analysis-width", type=int, default=384)
    parser.add_argument("--min-vote-frames", type=int, default=7)
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--analyze-clothing", action="store_true")
    parser.add_argument("--no-auto-sample", action="store_true")
    args = parser.parse_args()

    fetcher = YouTubeFrameFetcher(analysis_width=args.analysis_width)
    if args.url:
        output = fetcher.analyze_youtube_url(
            args.url,
            query=args.query,
            seconds=parse_seconds(args.seconds),
            auto_seconds=parse_seconds(args.auto_seconds),
            max_height=args.max_height,
            min_vote_frames=args.min_vote_frames,
            analyze_clothing=args.analyze_clothing,
            skip_video=args.skip_video,
            no_auto_sample=args.no_auto_sample,
            include_thumbnail_analysis=True,
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    else:
        fancams = fetcher.get_fancams(args.query, limit=args.limit)
        selected = fancams[0] if fancams else None

    if not selected:
        print(json.dumps({"ok": False, "error": "no video found"}, ensure_ascii=False))
        return

    thumbnail = fetcher.get_thumbnail_frame(selected["url"], selected.get("thumbnail_url", ""))
    output = {
        "ok": True,
        "query": args.query,
        "selected": selected,
        "search_results": fancams,
        "thumbnail": thumbnail,
    }
    if args.analyze_clothing and thumbnail.get("ok"):
        output["thumbnail_clothing"] = fetcher.analyze_with_c_model(thumbnail["file_path"])

    if not args.skip_video:
        video = fetcher.download_fancam(selected["url"], max_height=args.max_height)
        output["video"] = video
        if video.get("ok"):
            initial_seconds = parse_seconds(args.seconds)
            output["frames"] = fetcher.extract_sample_frames(
                video["file_path"],
                seconds=initial_seconds,
                prefix="youtube-only",
            )
            if args.analyze_clothing:
                output["frame_clothing"] = fetcher.analyze_frames_with_c_model(output["frames"])
                output["best_frame_clothing"] = fetcher.select_best_clothing_result(output["frame_clothing"])
                output["final_clothing"] = fetcher.aggregate_clothing_results(
                    output["frame_clothing"],
                    min_frames=args.min_vote_frames,
                )
                if (
                    not args.no_auto_sample
                    and (
                        len(output["frames"]) < args.min_vote_frames
                        or (
                            output["final_clothing"]
                            and not output["final_clothing"].get("usable", False)
                        )
                    )
                ):
                    already = {frame["second"] for frame in output["frames"]}
                    extra_seconds = tuple(s for s in parse_seconds(args.auto_seconds) if s not in already)
                    output["auto_sampled"] = False
                    if extra_seconds:
                        extra_frames = fetcher.extract_sample_frames(
                            video["file_path"],
                            seconds=extra_seconds,
                            prefix="youtube-only",
                        )
                        output["frames"] = fetcher.merge_frames(output["frames"], extra_frames)
                        output["frame_clothing"] = fetcher.merge_frame_clothing(
                            output["frame_clothing"],
                            fetcher.analyze_frames_with_c_model(extra_frames),
                        )
                        output["best_frame_clothing"] = fetcher.select_best_clothing_result(output["frame_clothing"])
                        output["final_clothing"] = fetcher.aggregate_clothing_results(
                            output["frame_clothing"],
                            min_frames=args.min_vote_frames,
                        )
                        output["auto_sampled"] = True

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
