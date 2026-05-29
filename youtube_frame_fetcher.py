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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from lower_garment_model import load_model, lower_garment_family, pants_length_for_label, predict as predict_lower_garment


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
        self.stream_cache_dir = self.cache_dir / "streams"
        self.cache_ttl_seconds = 24 * 60 * 60
        self.search_ttl_seconds = 6 * 60 * 60
        self.stream_ttl_seconds = int(os.environ.get("YOUTUBE_STREAM_CACHE_TTL_SECONDS", "600"))
        self.stream_frame_format = os.environ.get("STREAM_FRAME_FORMAT", "ppm").lower()
        if self.stream_frame_format not in {"ppm", "jpg", "jpeg"}:
            self.stream_frame_format = "ppm"
        self.stream_frame_workers = max(1, int(os.environ.get("STREAM_FRAME_WORKERS", "4")))
        self.auto_sample_weak_vote = os.environ.get("AUTO_SAMPLE_WEAK_VOTE", "0") == "1"
        self.lower_garment_model = None
        model_path = os.environ.get("LOWER_GARMENT_MODEL", str(self.base_dir / "lower_garment_model.json"))
        if model_path and model_path != "0":
            self.lower_garment_model = load_model(model_path)
        self.yt_dlp_upgrade_interval_seconds = int(
            float(os.environ.get("YT_DLP_UPGRADE_INTERVAL_HOURS", "24")) * 60 * 60
        )
        self.analysis_width = analysis_width
        self.frame_width = int(os.environ.get("FRAME_CACHE_WIDTH", str(analysis_width)))
        self.yt_dlp = self.find_executable("yt-dlp")
        self.ffmpeg = self.find_executable("ffmpeg")

        for directory in (
            self.search_cache_dir,
            self.thumbnail_cache_dir,
            self.video_cache_dir,
            self.frame_cache_dir,
            self.ppm_cache_dir,
            self.stream_cache_dir,
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

    def normalize_search_text(self, value):
        value = str(value or "").lower()
        value = re.sub(r"[^0-9a-z가-힣]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def contains_search_term(self, text, term):
        text = self.normalize_search_text(text)
        term = self.normalize_search_text(term)
        if not text or not term:
            return False
        if re.fullmatch(r"[0-9a-z ]+", term):
            pattern = r"(?<![0-9a-z])" + r"\s+".join(re.escape(part) for part in term.split()) + r"(?![0-9a-z])"
            return re.search(pattern, text) is not None
        return term in text

    def query_match_groups(self, query_name):
        alias_groups = [
            ("ive", ("ive", "아이브"), False),
            ("wonyoung", ("wonyoung", "won young", "jang wonyoung", "jang won young", "원영", "장원영"), True),
            ("twice", ("twice", "트와이스"), False),
            ("jeongyeon", ("jeongyeon", "jungyeon", "정연", "유정연"), True),
            ("karina", ("karina", "카리나", "유지민"), True),
        ]
        generic_terms = {
            "fancam",
            "fan",
            "cam",
            "focus",
            "직캠",
            "4k",
            "8k",
            "stage",
            "live",
            "performance",
            "official",
            "뮤직뱅크",
            "인기가요",
            "엠카",
            "쇼음악중심",
        }
        normalized_query = self.normalize_search_text(query_name)
        groups = []
        consumed = set()

        for name, aliases, specific in alias_groups:
            if any(self.contains_search_term(normalized_query, alias) for alias in aliases):
                groups.append({"name": name, "aliases": aliases, "specific": specific})
                for alias in aliases:
                    consumed.update(self.normalize_search_text(alias).split())

        for token in normalized_query.split():
            if token in consumed or token in generic_terms or len(token) < 2:
                continue
            groups.append({"name": token, "aliases": (token,), "specific": len(token) >= 4})

        deduped = []
        seen = set()
        for group in groups:
            if group["name"] in seen:
                continue
            seen.add(group["name"])
            deduped.append(group)
        return deduped

    def score_search_result(self, query_name, title):
        groups = self.query_match_groups(query_name)
        matched = []
        for group in groups:
            if any(self.contains_search_term(title, alias) for alias in group["aliases"]):
                matched.append(group)

        all_matched = bool(groups) and len(matched) == len(groups)
        specific_matched = any(group["specific"] for group in matched)
        accepted = not groups or all_matched or specific_matched
        return {
            "accepted": accepted,
            "matched": [group["name"] for group in matched],
            "required": [group["name"] for group in groups],
            "score": len(matched) + (2 if specific_matched else 0) + (2 if all_matched else 0),
        }

    def filter_search_results(self, query_name, results, limit):
        filtered = []
        seen_ids = set()
        for item in results:
            video_id = item.get("id") or self.extract_youtube_id(item.get("url", ""))
            if not video_id or video_id in seen_ids:
                continue
            title = item.get("title", "")
            match = self.score_search_result(query_name, title)
            if not match["accepted"]:
                continue
            seen_ids.add(video_id)
            filtered.append({**item, "query_match": match})

        filtered.sort(key=lambda item: item.get("query_match", {}).get("score", 0), reverse=True)
        return filtered[:limit]

    def parse_video_info_line(self, line, fallback_url=""):
        if "|||" not in line:
            return {}
        parts = line.split("|||", 3)
        if len(parts) < 2:
            return {}
        video_id = parts[0].strip()
        title = parts[1].strip()
        thumbnail_url = parts[2].strip() if len(parts) > 2 else ""
        uploader = parts[3].strip() if len(parts) > 3 else ""
        if not video_id or video_id == "NA":
            video_id = self.extract_youtube_id(fallback_url)
        if not title or title == "NA":
            title = ""
        if not thumbnail_url or thumbnail_url == "NA":
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
        return {
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else fallback_url,
            "id": video_id,
            "thumbnail_url": thumbnail_url,
            "uploader": "" if uploader == "NA" else uploader,
        }

    def get_video_info(self, url):
        cmd = [
            *self.command_args(self.yt_dlp),
            "--no-playlist",
            "--no-warnings",
            "--skip-download",
            "--print",
            "%(id)s|||%(title)s|||%(thumbnail)s|||%(uploader)s",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or "yt-dlp metadata failed").strip()[-800:]}

        for line in result.stdout.strip().splitlines():
            info = self.parse_video_info_line(line, fallback_url=url)
            if info:
                return {"ok": True, **info}
        return {"ok": False, "error": "video metadata not found"}

    def get_fancams(self, query_name, limit=5):
        search_query = f"{query_name} 직캠 fancam"
        search_limit = max(limit * 4, limit + 8, 12)
        cache_path = self.search_cache_dir / f"{self.cache_key(f'{search_query}|limit={limit}|filter=v3')}.json"
        cached = self.read_json_cache(cache_path, self.search_ttl_seconds)
        if cached is not None:
            return cached

        cmd = [
            *self.command_args(self.yt_dlp),
            "--flat-playlist",
            "--no-warnings",
            "--print",
            "%(id)s|||%(title)s|||%(thumbnail)s|||%(uploader)s",
            f"ytsearch{search_limit}:{search_query}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "yt-dlp search failed").strip()[-800:])

        raw_fancams = []
        for line in result.stdout.strip().splitlines():
            info = self.parse_video_info_line(line)
            if not info.get("id") or not info.get("title"):
                continue
            raw_fancams.append(info)

        fancams = self.filter_search_results(query_name, raw_fancams, limit)
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

    def find_cached_video(self, video_id, max_height=None, sample_end_seconds=None):
        if not video_id:
            return None
        matches = []
        sample_end_seconds = int(sample_end_seconds or 0)
        for path in self.video_cache_dir.iterdir():
            if not path.is_file() or f"[{video_id}]" not in path.name:
                continue
            marker = re.search(r"\sh(\d+)s(\d+)(?=\.)", path.name)
            if marker and sample_end_seconds:
                cached_height = int(marker.group(1))
                cached_end = int(marker.group(2))
                if max_height and cached_height < int(max_height):
                    continue
                if cached_end < sample_end_seconds:
                    continue
            matches.append(path)

        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
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

    def sample_end_seconds(self, seconds=(), auto_seconds=(), enabled=True):
        sample_points = list(seconds or [])
        if enabled:
            sample_points.extend(auto_seconds or [])
        if not sample_points:
            return 0
        return max(30, int(max(sample_points)) + 5)

    def video_format_selectors(self, max_height=720, include_audio_fallback=True):
        selectors = [
            (
                f"bv*[vcodec^=avc1][height<={max_height}][ext=mp4]/"
                f"bv*[vcodec!=none][height<={max_height}][ext=mp4]/"
                f"bv*[vcodec!=none][height<={max_height}]/"
                f"b[vcodec!=none][height<={max_height}][ext=mp4]/"
                f"b[vcodec!=none][height<={max_height}]"
            ),
            (
                f"bv*[height<={max_height}]/"
                f"bestvideo[height<={max_height}]/"
                f"b[height<={max_height}]/"
                f"bestvideo[height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]"
            ),
            "bv*/bestvideo/b[vcodec!=none]/best",
        ]
        if include_audio_fallback:
            selectors.append("bv*+ba/bestvideo+bestaudio/best")
        return selectors

    def get_video_stream_url(self, url, max_height=720):
        self.maybe_upgrade_yt_dlp()
        video_id = self.extract_youtube_id(url) or self.cache_key(url)
        cache_path = self.stream_cache_dir / f"{video_id}-h{int(max_height)}.json"
        cached = self.read_json_cache(cache_path, self.stream_ttl_seconds)
        if cached and cached.get("stream_url"):
            cached["cached"] = True
            return cached

        last_error = ""
        result = None
        youtube_client_args = [
            [],
            ["--extractor-args", "youtube:player_client=android,web"],
            ["--extractor-args", "youtube:player_client=tv,web"],
            ["--force-ipv4"],
        ]
        for client_args in youtube_client_args:
            for format_selector in self.video_format_selectors(max_height, include_audio_fallback=False):
                cmd = [
                    *self.command_args(self.yt_dlp),
                    "--no-playlist",
                    "--no-warnings",
                    *client_args,
                    "--format",
                    format_selector,
                    "--get-url",
                    url,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    break
                last_error = (result.stderr or "yt-dlp stream URL failed").strip()[-800:]
            if result and result.returncode == 0:
                break

        if result is None or result.returncode != 0:
            return {"ok": False, "mode": "stream", "error": last_error or "yt-dlp stream URL failed"}

        stream_url = next((line.strip() for line in result.stdout.splitlines() if line.strip().startswith("http")), "")
        if not stream_url:
            return {"ok": False, "mode": "stream", "error": "yt-dlp returned no video stream URL"}

        payload = {
            "ok": True,
            "mode": "stream",
            "video_id": video_id,
            "max_height": int(max_height),
            "stream_url": stream_url,
            "cached": False,
        }
        self.write_json_cache(cache_path, payload)
        return payload

    def public_stream_payload(self, stream):
        return {key: value for key, value in stream.items() if key != "stream_url"}

    def extract_sample_frames_from_stream(self, stream_url, cache_id, seconds=(5, 10, 15), prefix="sample", max_height=720):
        frame_ext = "jpg" if self.stream_frame_format in {"jpg", "jpeg"} else "ppm"
        video_key = self.cache_key(f"stream|{cache_id}|h={max_height}|w={self.frame_width}|fmt={frame_ext}")
        frames = []
        missing_seconds = []

        for second in seconds:
            fp = self.frame_cache_dir / f"{prefix}-{video_key}-{second}.{frame_ext}"
            if fp.exists() and time.time() - fp.stat().st_mtime <= self.cache_ttl_seconds:
                frames.append(self.frame_payload(fp, second, cached=True))
            else:
                missing_seconds.append(second)

        if missing_seconds:
            if self.stream_frame_workers > 1 and len(missing_seconds) > 1:
                with ThreadPoolExecutor(max_workers=min(self.stream_frame_workers, len(missing_seconds))) as executor:
                    futures = [
                        executor.submit(
                            self.extract_single_stream_frame,
                            stream_url,
                            video_key,
                            second,
                            prefix,
                            frame_ext,
                        )
                        for second in missing_seconds
                    ]
                    for future in as_completed(futures):
                        frame = future.result()
                        if frame:
                            frames.append(frame)
                return sorted(frames, key=lambda f: f["second"])
            for second in missing_seconds:
                frame = self.extract_single_stream_frame(stream_url, video_key, second, prefix, frame_ext)
                if frame:
                    frames.append(frame)

        return sorted(frames, key=lambda f: f["second"])

    def extract_single_stream_frame(self, stream_url, video_key, second, prefix, frame_ext):
        dst = self.frame_cache_dir / f"{prefix}-{video_key}-{second}.{frame_ext}"
        tmp_path = self.frame_cache_dir / f".{prefix}-{video_key}-{second}-{os.getpid()}-{int(time.time() * 1000)}.tmp.{frame_ext}"
        cmd = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-nostdin",
            "-ss",
            str(second),
            "-i",
            stream_url,
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            "1",
            "-vf",
            f"scale={self.frame_width}:-2:force_original_aspect_ratio=decrease",
        ]
        if frame_ext == "jpg":
            cmd.extend(["-q:v", "4"])
        cmd.extend(["-f", "image2", str(tmp_path)])
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if tmp_path.exists():
            tmp_path.replace(dst)
        if dst.exists():
            return self.frame_payload(dst, second, cached=False)
        tmp_path.unlink(missing_ok=True)
        return None

    def download_fancam(self, url, max_height=720, sample_end_seconds=None):
        self.maybe_upgrade_yt_dlp()
        video_id = self.extract_youtube_id(url)
        sample_end_seconds = int(sample_end_seconds or 0)
        cached = self.find_cached_video(video_id, max_height=max_height, sample_end_seconds=sample_end_seconds)
        if cached:
            return self.file_payload(cached, cached=True)

        format_selectors = self.video_format_selectors(max_height)
        sample_suffix = f" h{int(max_height)}s{sample_end_seconds}" if sample_end_seconds else ""
        output_template = str(self.video_cache_dir / f"%(title).120s [%(id)s]{sample_suffix}.%(ext)s")
        last_error = ""
        result = None
        youtube_client_args = [
            [],
            ["--extractor-args", "youtube:player_client=android,web"],
            ["--extractor-args", "youtube:player_client=tv,web"],
            ["--force-ipv4"],
        ]
        section_args_options = (
            [["--download-sections", f"*0-{sample_end_seconds}"], []]
            if sample_end_seconds
            else [[]]
        )
        for client_args in youtube_client_args:
            for section_args in section_args_options:
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
                        "--no-mtime",
                        "--no-part",
                        *client_args,
                        *section_args,
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
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-nostdin",
                "-i",
                str(video),
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"select='{select_parts}',scale={self.frame_width}:-2:force_original_aspect_ratio=decrease",
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
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-nostdin",
                        "-ss",
                        str(second),
                        "-i",
                        str(video),
                        "-an",
                        "-sn",
                        "-dn",
                        "-frames:v",
                        "1",
                        "-vf",
                        f"scale={self.frame_width}:-2:force_original_aspect_ratio=decrease",
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

    def frame_ppm_path(self, image_path):
        source = Path(image_path)
        return self.ppm_cache_dir / f"{self.cache_key(f'{source.resolve()}|w={self.analysis_width}')}.ppm"

    def ensure_frame_ppms(self, frames):
        if not frames:
            return {}

        ready = {}
        missing = []
        for frame in frames:
            source = Path(frame["file_path"])
            key = str(source.resolve())
            if source.suffix.lower() == ".ppm" and source.exists():
                ready[key] = {"ok": True, "ppm_path": str(source.resolve()), "cached": frame.get("cached", False)}
                continue
            ppm_path = self.frame_ppm_path(source)
            if ppm_path.exists() and time.time() - ppm_path.stat().st_mtime <= self.cache_ttl_seconds:
                ready[key] = {"ok": True, "ppm_path": str(ppm_path.resolve()), "cached": True}
            else:
                missing.append((key, source, ppm_path))

        if not missing:
            return ready

        tmp_token = f"{os.getpid()}-{int(time.time() * 1000)}"
        tmp_paths = []
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-nostdin"]
        for _, source, _ in missing:
            cmd.extend(["-i", str(source)])
        for idx, (_, _, ppm_path) in enumerate(missing):
            tmp_path = ppm_path.with_name(f".{ppm_path.stem}.{tmp_token}.tmp.ppm")
            tmp_paths.append(tmp_path)
            cmd.extend(
                [
                    "-map",
                    f"{idx}:v:0",
                    f"-frames:v:{idx}",
                    "1",
                    f"-vf:v:{idx}",
                    f"scale={self.analysis_width}:-2:force_original_aspect_ratio=decrease",
                    "-update",
                    "1",
                    "-f",
                    "image2",
                    str(tmp_path),
                ]
            )
        subprocess.run(cmd, capture_output=True, text=True, timeout=90)

        for (key, source, ppm_path), tmp_path in zip(missing, tmp_paths):
            if tmp_path.exists():
                tmp_path.replace(ppm_path)
            if ppm_path.exists():
                ready[key] = {"ok": True, "ppm_path": str(ppm_path.resolve()), "cached": False}
            else:
                fallback = self.ensure_ppm(source)
                if fallback.get("ok"):
                    ready[key] = fallback
                else:
                    ready[key] = fallback

        for tmp_path in tmp_paths:
            tmp_path.unlink(missing_ok=True)

        return ready

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
        ppm_path = self.frame_ppm_path(source)
        if ppm_path.exists() and time.time() - ppm_path.stat().st_mtime <= self.cache_ttl_seconds:
            return {"ok": True, "ppm_path": str(ppm_path.resolve()), "cached": True}

        tmp_path = ppm_path.with_name(f".{ppm_path.stem}.{os.getpid()}-{int(time.time() * 1000)}.tmp.ppm")
        convert_cmd = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-nostdin",
            "-i",
            str(source),
            "-vf",
            f"scale={self.analysis_width}:-2:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            "-f",
            "image2",
            str(tmp_path),
        ]
        convert_result = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=30)
        if convert_result.returncode != 0 or not tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "error": (convert_result.stderr or "PPM conversion failed").strip()[-800:]}
        tmp_path.replace(ppm_path)
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

    def attach_lower_garment_model_prediction(self, analysis):
        if not self.lower_garment_model:
            return analysis
        prediction = predict_lower_garment(self.lower_garment_model, analysis)
        analysis["lower_garment_model_label"] = prediction["label"]
        analysis["lower_garment_model_confidence"] = prediction["confidence"]
        analysis["lower_garment_model_probabilities"] = prediction["probabilities"]
        return analysis

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
        if analysis.get("lower_garment") == "unknown":
            score_value -= 0.15
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

    def needs_additional_sampling(self, output, min_vote_frames):
        final = output.get("final_clothing") or {}
        analysis = (final.get("result") or {}).get("analysis") or {}
        aggregation = final.get("aggregation") or {}
        if len(output.get("frames") or []) < min_vote_frames:
            return True
        low_confidence = (
            float(analysis.get("person_confidence", 0.0)) < 0.30
            or float(analysis.get("color_confidence", 0.0)) < 0.30
        )
        if final and not final.get("usable", False) and low_confidence:
            return True
        if analysis.get("lower_garment") == "unknown" or analysis.get("lower_garment_family") == "unknown":
            return True
        lower_unknown = int(aggregation.get("unknown_counts", {}).get("lower_garment", 0))
        lower_known = len(output.get("frames") or []) - lower_unknown
        if lower_unknown >= min_vote_frames:
            return True
        if lower_known < min(4, min_vote_frames) and len(output.get("frames") or []) < min_vote_frames + 3:
            return True
        if float(analysis.get("lower_garment_vote_confidence", 0.0)) < 0.75 and len(output.get("frames") or []) < min_vote_frames + 3:
            return self.auto_sample_weak_vote
        return False

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
            unknown_count = 0
            for entry in selected:
                value = entry["analysis"].get(name)
                if not value or value == "unknown":
                    unknown_count += 1
                    continue
                counts[value] += 1
                tie_scores[value] += entry["score"]
            if not counts:
                return "unknown", {}, 0.0, 0, unknown_count
            winner = max(counts, key=lambda value: (counts[value], tie_scores[value]))
            ordered_counts = sorted(counts.values(), reverse=True)
            runner_up = ordered_counts[1] if len(ordered_counts) > 1 else 0
            confidence = counts[winner] / sum(counts.values())
            return winner, dict(counts), round(confidence, 4), counts[winner] - runner_up, unknown_count

        def lower_garment_family(value):
            if value in {"shorts", "knee_length_pants", "cropped_pants", "long_pants"}:
                return "pants"
            if value in {"mini_skirt", "knee_length_skirt", "midi_skirt", "long_skirt"}:
                return "skirt"
            return "unknown"

        analysis = {}
        votes = {}
        vote_confidence = {}
        vote_margin = {}
        unknown_counts = {}
        for key in ("upper_color", "lower_color", "lower_garment", "lower_garment_family", "pants_length", "exposure"):
            analysis[key], votes[key], vote_confidence[key], vote_margin[key], unknown_counts[key] = vote(key)
        derived_family_counts = Counter()
        for lower_value, count in votes["lower_garment"].items():
            family = lower_garment_family(lower_value)
            if family != "unknown":
                derived_family_counts[family] += count
        derived_family_total = sum(derived_family_counts.values())
        raw_family_known = len(selected) - unknown_counts["lower_garment_family"]
        if derived_family_total > raw_family_known:
            family_winner = max(derived_family_counts, key=lambda value: derived_family_counts[value])
            family_counts = sorted(derived_family_counts.values(), reverse=True)
            family_runner_up = family_counts[1] if len(family_counts) > 1 else 0
            analysis["lower_garment_family"] = family_winner
            votes["lower_garment_family"] = dict(derived_family_counts)
            vote_confidence["lower_garment_family"] = round(
                derived_family_counts[family_winner] / derived_family_total,
                4,
            )
            vote_margin["lower_garment_family"] = derived_family_counts[family_winner] - family_runner_up
            unknown_counts["lower_garment_family"] = len(selected) - derived_family_total
        derived_lower_family = lower_garment_family(analysis["lower_garment"])
        if (
            derived_lower_family != "unknown"
            and (
                analysis["lower_garment_family"] == "unknown"
                or (
                    analysis["lower_garment_family"] != derived_lower_family
                    and vote_confidence["lower_garment_family"] < 0.75
                )
            )
        ):
            analysis["lower_garment_family"] = derived_lower_family

        analysis["skin_ratio"] = numeric_mean("skin_ratio")
        analysis["upper_skin_ratio"] = numeric_mean("upper_skin_ratio")
        analysis["lower_skin_ratio"] = numeric_mean("lower_skin_ratio")
        analysis["lower_coverage_ratio"] = numeric_mean("lower_coverage_ratio")
        analysis["lower_split_ratio"] = numeric_mean("lower_split_ratio")
        analysis["lower_center_fill_ratio"] = numeric_mean("lower_center_fill_ratio")
        analysis["lower_garment_vote_confidence"] = vote_confidence["lower_garment"]
        analysis["lower_garment_vote_margin"] = vote_margin["lower_garment"]
        analysis["lower_garment_family_vote_confidence"] = vote_confidence["lower_garment_family"]
        analysis["lower_garment_family_vote_margin"] = vote_margin["lower_garment_family"]
        analysis["lower_garment_unknown_frames"] = unknown_counts["lower_garment"]
        analysis["lower_garment_family_unknown_frames"] = unknown_counts["lower_garment_family"]
        analysis["lower_garment_known_frames"] = len(selected) - unknown_counts["lower_garment"]
        analysis["lower_garment_family_known_frames"] = len(selected) - unknown_counts["lower_garment_family"]
        model_votes = Counter()
        model_confidence_sum = Counter()
        for entry in selected:
            label = entry["analysis"].get("lower_garment_model_label")
            confidence = float(entry["analysis"].get("lower_garment_model_confidence", 0.0) or 0.0)
            if not label or confidence < 0.45:
                continue
            model_votes[label] += 1
            model_confidence_sum[label] += confidence
        model_label = "unknown"
        model_vote_confidence = 0.0
        model_average_confidence = 0.0
        if model_votes:
            model_label = max(model_votes, key=lambda value: (model_votes[value], model_confidence_sum[value]))
            model_total = sum(model_votes.values())
            model_vote_confidence = round(model_votes[model_label] / model_total, 4)
            model_average_confidence = round(model_confidence_sum[model_label] / model_votes[model_label], 4)
        analysis["lower_garment_model_vote"] = model_label
        analysis["lower_garment_model_vote_confidence"] = model_vote_confidence
        analysis["lower_garment_model_average_confidence"] = model_average_confidence
        analysis["lower_garment_model_votes"] = dict(model_votes)
        model_assisted = False
        model_override_reason = ""
        model_enough_votes = sum(model_votes.values()) >= min(4, min_frames)
        model_strong = (
            model_label != "unknown"
            and model_enough_votes
            and model_vote_confidence >= 0.75
            and model_average_confidence >= 0.60
        )
        if analysis["lower_garment_known_frames"] < min(3, min_frames):
            if (
                model_label != "unknown"
                and sum(model_votes.values()) >= min(3, min_frames)
                and model_vote_confidence >= 0.67
                and model_average_confidence >= 0.55
            ):
                analysis["lower_garment"] = model_label
                analysis["lower_garment_family"] = lower_garment_family(model_label)
                analysis["pants_length"] = pants_length_for_label(model_label)
                model_assisted = True
                model_override_reason = "model_sparse_known"
            else:
                analysis["lower_garment"] = "unknown"
                analysis["lower_garment_family"] = "unknown"
                analysis["pants_length"] = "unknown"
        elif (
            model_strong
            and (
                analysis["lower_garment_vote_confidence"] < 0.75
                or analysis["lower_garment_known_frames"] < min_frames
            )
            and (
                model_label != analysis["lower_garment"]
                or analysis["lower_garment_known_frames"] < min(4, min_frames)
            )
        ):
            analysis["lower_garment"] = model_label
            analysis["lower_garment_family"] = lower_garment_family(model_label)
            analysis["pants_length"] = pants_length_for_label(model_label)
            model_assisted = True
            model_override_reason = "model_override_weak_vote"
        analysis["person_confidence"] = numeric_mean("person_confidence")
        analysis["color_confidence"] = numeric_mean("color_confidence")
        analysis["analysis_quality"] = (
            "high" if analysis["person_confidence"] >= 0.62 else "medium" if analysis["person_confidence"] >= 0.36 else "low"
        )
        analysis["color_quality"] = (
            "high" if analysis["color_confidence"] >= 0.58 else "medium" if analysis["color_confidence"] >= 0.30 else "low"
        )
        lower_garment_reason = (
            "all_unknown"
            if analysis["lower_garment"] == "unknown" and unknown_counts["lower_garment"] >= len(selected)
            else model_override_reason
            if model_assisted
            else "sparse_known"
            if analysis["lower_garment_known_frames"] < min(4, min_frames)
            else "weak_vote"
            if analysis["lower_garment_vote_confidence"] < 0.75
            else "ok"
        )
        lower_garment_decision = {
            "label": analysis["lower_garment"],
            "family": analysis["lower_garment_family"],
            "confidence": analysis["lower_garment_vote_confidence"],
            "family_confidence": analysis["lower_garment_family_vote_confidence"],
            "margin": analysis["lower_garment_vote_margin"],
            "family_margin": analysis["lower_garment_family_vote_margin"],
            "votes": votes["lower_garment"],
            "family_votes": votes["lower_garment_family"],
            "known_frames": analysis["lower_garment_known_frames"],
            "family_known_frames": analysis["lower_garment_family_known_frames"],
            "model_label": model_label,
            "model_confidence": model_vote_confidence,
            "model_average_confidence": model_average_confidence,
            "model_votes": dict(model_votes),
            "model_assisted": model_assisted,
            "needs_review": analysis["lower_garment_vote_confidence"] < 0.75,
            "reason": lower_garment_reason,
        }

        representative = selected[0]["item"]
        strict_usable = analysis["person_confidence"] >= 0.36 and analysis["color_confidence"] >= 0.40
        lower_label = analysis["lower_garment"]
        lower_family = analysis["lower_garment_family"]
        enough_lower_frames = analysis["lower_garment_known_frames"] >= min(4, min_frames)
        exact_lower_vote = (
            analysis["lower_garment_vote_confidence"] >= 0.67
            and analysis["lower_garment_family_vote_confidence"] >= 0.60
        )
        short_family_vote = (
            lower_label in {"mini_skirt", "shorts"}
            and analysis["lower_garment_vote_confidence"] >= 0.50
            and analysis["lower_garment_family_vote_confidence"] >= 0.85
        )
        stable_lower_vote = (
            enough_lower_frames
            and lower_label != "unknown"
            and lower_family != "unknown"
            and (exact_lower_vote or short_family_vote)
        )
        vote_usable = (
            stable_lower_vote
            and analysis["person_confidence"] >= 0.30
            and analysis["color_confidence"] >= 0.34
        )
        result = {
            "second": representative.get("second"),
            "frame": representative.get("frame", {}),
            "result": {"ok": True, "analysis": analysis},
            "usable": (strict_usable or vote_usable) and stable_lower_vote,
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
                "unknown_counts": unknown_counts,
            },
        }

        warnings = []
        if len(selected) < min_frames:
            warnings.append(f"only {len(selected)} valid frames were available for voting")
        all_lower_unknown = analysis["lower_garment"] == "unknown" and unknown_counts["lower_garment"] >= len(selected)
        if all_lower_unknown:
            warnings.append("lower garment type was unknown in every voted frame; collect more frames or inspect manually")
        elif analysis["lower_garment_known_frames"] < min(4, min_frames):
            warnings.append(
                f"only {analysis['lower_garment_known_frames']} voted frames had a lower garment type; collect more frames or inspect manually"
            )
        if not strict_usable:
            warnings.append("aggregated confidence is low; frame set is likely wide/group/poorly lit")
        if lower_garment_decision["needs_review"] and not all_lower_unknown:
            warnings.append("lower garment vote is weak; add more frames or inspect manually")
        if (
            analysis.get("color_quality") == "medium"
            and (analysis.get("upper_color") == "purple" or analysis.get("lower_color") == "purple")
        ):
            warnings.append("purple clothing detected with medium color quality; fine tone distinctions may be limited")
        if warnings:
            result["warnings"] = warnings
        return result

    def analyze_frames_with_c_model(self, frames, video_path=None, prefix="sample"):
        frame_ppms = self.ensure_frame_ppms(frames)
        prepared = []
        results = []
        for frame in frames:
            frame_key = str(Path(frame["file_path"]).resolve())
            ppm = frame_ppms.get(frame_key) or self.ensure_ppm(frame["file_path"])
            if ppm.get("ok"):
                prepared.append((frame, ppm))
            else:
                results.append({"second": frame["second"], "frame": frame, "result": ppm})

        if prepared:
            batch = self.run_c_model_batch([ppm["ppm_path"] for _, ppm in prepared])
            if batch.get("ok"):
                for (frame, ppm), analysis in zip(prepared, batch["analyses"]):
                    self.attach_lower_garment_model_prediction(analysis)
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
        include_thumbnail=True,
        include_thumbnail_analysis=False,
    ):
        selected = {
            "title": "",
            "url": url,
            "id": self.extract_youtube_id(url),
            "thumbnail_url": thumbnail_url,
        }
        video_info = None
        if query:
            video_info = self.get_video_info(url)
            if video_info.get("ok"):
                selected.update(
                    {
                        "title": video_info.get("title", ""),
                        "url": video_info.get("url", url),
                        "id": video_info.get("id") or selected["id"],
                        "thumbnail_url": video_info.get("thumbnail_url") or thumbnail_url,
                        "uploader": video_info.get("uploader", ""),
                    }
                )
                thumbnail_url = selected["thumbnail_url"]
                selected["query_match"] = self.score_search_result(query, selected["title"])
        thumbnail = (
            self.get_thumbnail_frame(url, thumbnail_url)
            if include_thumbnail or include_thumbnail_analysis
            else {"ok": False, "skipped": True}
        )
        output = {
            "ok": True,
            "query": query,
            "selected": selected,
            "search_results": [selected],
            "thumbnail": thumbnail,
        }
        warnings = []
        if video_info and not video_info.get("ok"):
            output["video_metadata"] = video_info
        if query and selected.get("query_match") and not selected["query_match"].get("accepted"):
            output["link_query_mismatch"] = True
            warnings.append("provided URL title does not match query; analyzer will still inspect the linked video")
        if warnings:
            output["warnings"] = warnings

        if include_thumbnail_analysis and analyze_clothing and output["thumbnail"].get("ok"):
            output["thumbnail_clothing"] = self.analyze_with_c_model(output["thumbnail"]["file_path"])

        if skip_video:
            return output

        initial_sample_end = self.sample_end_seconds(seconds)
        video = None
        stream = None
        output["frames"] = []
        use_stream = os.environ.get("YOUTUBE_FRAME_SOURCE", "stream").lower() not in {"download", "file", "local"}
        if use_stream:
            stream = self.get_video_stream_url(url, max_height=max_height)
            if stream.get("ok"):
                output["video"] = self.public_stream_payload(stream)
                output["frames"] = self.extract_sample_frames_from_stream(
                    stream["stream_url"],
                    selected["id"] or url,
                    seconds=seconds,
                    prefix="youtube-only",
                    max_height=max_height,
                )
            else:
                output["video_stream"] = self.public_stream_payload(stream)

        if len(output["frames"]) < len(seconds):
            stream = None
            video = self.download_fancam(
                url,
                max_height=max_height,
                sample_end_seconds=initial_sample_end,
            )
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

        output["frame_clothing"] = self.analyze_frames_with_c_model(
            output["frames"],
            video_path=(video or {}).get("file_path"),
            prefix="youtube-only",
        )
        output["best_frame_clothing"] = self.select_best_clothing_result(output["frame_clothing"])
        output["final_clothing"] = self.aggregate_clothing_results(
            output["frame_clothing"],
            min_frames=min_vote_frames,
        )

        if not no_auto_sample and self.needs_additional_sampling(output, min_vote_frames):
            already = {frame["second"] for frame in output["frames"]}
            extra_seconds = tuple(s for s in auto_seconds if s not in already)
            output["auto_sampled"] = False
            if extra_seconds:
                if stream and stream.get("ok"):
                    extra_frames = self.extract_sample_frames_from_stream(
                        stream["stream_url"],
                        selected["id"] or url,
                        seconds=extra_seconds,
                        prefix="youtube-only",
                        max_height=max_height,
                    )
                else:
                    extra_video = video
                    extra_sample_end = self.sample_end_seconds(extra_seconds)
                    if extra_sample_end > initial_sample_end:
                        extra_video = self.download_fancam(
                            url,
                            max_height=max_height,
                            sample_end_seconds=extra_sample_end,
                        )
                        output["video_extended"] = extra_video
                        if not extra_video.get("ok"):
                            output.setdefault("warnings", []).append(
                                "additional sampling was skipped because extended video download failed"
                            )
                            return output
                    extra_frames = self.extract_sample_frames(
                        extra_video["file_path"],
                        seconds=extra_seconds,
                        prefix="youtube-only",
                    )
                output["frames"] = self.merge_frames(output["frames"], extra_frames)
                output["frame_clothing"] = self.merge_frame_clothing(
                    output["frame_clothing"],
                    self.analyze_frames_with_c_model(
                        extra_frames,
                        video_path=(video or {}).get("file_path"),
                        prefix="youtube-only",
                    ),
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


def compact_clothing_entry(entry, include_decision=False):
    if not entry:
        return entry
    result = entry.get("result") or {}
    compact = {
        "second": entry.get("second"),
        "usable": entry.get("usable"),
        "result": {"ok": result.get("ok", False), "analysis": result.get("analysis", {})},
    }
    if include_decision:
        compact["lower_garment_decision"] = entry.get("lower_garment_decision")
        compact["aggregation"] = entry.get("aggregation")
    if entry.get("warnings"):
        compact["warnings"] = entry.get("warnings")
    return compact


def summarize_output(output):
    summary = {
        "ok": output.get("ok", False),
        "query": output.get("query", ""),
        "selected": output.get("selected", {}),
        "video": output.get("video", {}),
        "thumbnail": output.get("thumbnail", {}),
        "frames_count": len(output.get("frames") or []),
        "sampled_seconds": [frame.get("second") for frame in output.get("frames") or []],
        "auto_sampled": output.get("auto_sampled", False),
        "best_frame_clothing": compact_clothing_entry(output.get("best_frame_clothing")),
        "final_clothing": compact_clothing_entry(output.get("final_clothing"), include_decision=True),
        "warnings": output.get("warnings", []),
    }
    for key in ("error", "link_query_mismatch", "video_metadata", "video_stream", "video_extended"):
        if key in output:
            summary[key] = output[key]
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="")
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
    parser.add_argument("--skip-thumbnail", action="store_true")
    parser.add_argument("--include-thumbnail-analysis", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
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
            include_thumbnail=not args.skip_thumbnail,
            include_thumbnail_analysis=args.include_thumbnail_analysis,
        )
        if args.summary_only:
            output = summarize_output(output)
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":") if args.summary_only else None, indent=None if args.summary_only else 2))
        return
    else:
        if not args.query:
            raise SystemExit("--query is required when --url is not provided")
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
    if args.include_thumbnail_analysis and args.analyze_clothing and thumbnail.get("ok"):
        output["thumbnail_clothing"] = fetcher.analyze_with_c_model(thumbnail["file_path"])

    if not args.skip_video:
        initial_seconds = parse_seconds(args.seconds)
        auto_seconds = parse_seconds(args.auto_seconds)
        video = fetcher.download_fancam(
            selected["url"],
            max_height=args.max_height,
            sample_end_seconds=fetcher.sample_end_seconds(initial_seconds),
        )
        initial_sample_end = fetcher.sample_end_seconds(initial_seconds)
        output["video"] = video
        if video.get("ok"):
            output["frames"] = fetcher.extract_sample_frames(
                video["file_path"],
                seconds=initial_seconds,
                prefix="youtube-only",
            )
            if args.analyze_clothing:
                output["frame_clothing"] = fetcher.analyze_frames_with_c_model(
                    output["frames"],
                    video_path=video["file_path"],
                    prefix="youtube-only",
                )
                output["best_frame_clothing"] = fetcher.select_best_clothing_result(output["frame_clothing"])
                output["final_clothing"] = fetcher.aggregate_clothing_results(
                    output["frame_clothing"],
                    min_frames=args.min_vote_frames,
                )
                if not args.no_auto_sample and fetcher.needs_additional_sampling(output, args.min_vote_frames):
                    already = {frame["second"] for frame in output["frames"]}
                    extra_seconds = tuple(s for s in parse_seconds(args.auto_seconds) if s not in already)
                    output["auto_sampled"] = False
                    if extra_seconds:
                        extra_video = video
                        extra_sample_end = fetcher.sample_end_seconds(extra_seconds)
                        if extra_sample_end > initial_sample_end:
                            extra_video = fetcher.download_fancam(
                                selected["url"],
                                max_height=args.max_height,
                                sample_end_seconds=extra_sample_end,
                            )
                            output["video_extended"] = extra_video
                            if not extra_video.get("ok"):
                                output.setdefault("warnings", []).append(
                                    "additional sampling was skipped because extended video download failed"
                                )
                                if args.summary_only:
                                    output = summarize_output(output)
                                print(json.dumps(output, ensure_ascii=False, separators=(",", ":") if args.summary_only else None, indent=None if args.summary_only else 2))
                                return
                        extra_frames = fetcher.extract_sample_frames(
                            extra_video["file_path"],
                            seconds=extra_seconds,
                            prefix="youtube-only",
                        )
                        output["frames"] = fetcher.merge_frames(output["frames"], extra_frames)
                        output["frame_clothing"] = fetcher.merge_frame_clothing(
                            output["frame_clothing"],
                            fetcher.analyze_frames_with_c_model(
                                extra_frames,
                                video_path=video["file_path"],
                                prefix="youtube-only",
                            ),
                        )
                        output["best_frame_clothing"] = fetcher.select_best_clothing_result(output["frame_clothing"])
                        output["final_clothing"] = fetcher.aggregate_clothing_results(
                            output["frame_clothing"],
                            min_frames=args.min_vote_frames,
                        )
                        output["auto_sampled"] = True

    if args.summary_only:
        output = summarize_output(output)
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":") if args.summary_only else None, indent=None if args.summary_only else 2))


if __name__ == "__main__":
    main()
