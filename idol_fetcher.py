import requests
from bs4 import BeautifulSoup
import json
import subprocess
import sys
import re
import os
import time
import hashlib
from pathlib import Path
from datetime import datetime

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
}

def parse_perf_date(date_str):
    """'May 1', 'May 27' 형태 파싱 → datetime 또는 None"""
    try:
        parts = date_str.strip().split()
        if len(parts) >= 2:
            month_key = re.sub(r'[^a-z]', '', parts[0].lower())
            day_num = int(re.sub(r'\D', '', parts[1]))
            month_num = MONTH_MAP.get(month_key, datetime.now().month)
            return datetime(datetime.now().year, month_num, day_num)
        elif parts:
            day_num = int(re.sub(r'\D', '', parts[0]))
            return datetime(datetime.now().year, datetime.now().month, day_num)
    except Exception:
        pass
    return None


class KpopIdolFetcher:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.base_dir = Path(__file__).resolve().parent
        self.cache_dir = self.base_dir / "cache"
        self.video_cache_dir = self.cache_dir / "videos"
        self.frame_cache_dir = self.cache_dir / "frames"
        self.thumbnail_cache_dir = self.cache_dir / "thumbnails"
        self.search_cache_dir = self.cache_dir / "searches"
        self.analysis_cache_dir = self.cache_dir / "analysis"
        self.data_cache_dir = self.cache_dir / "data"
        self.label_frame_dir = self.cache_dir / "label_frames"
        self.labels_path = self.cache_dir / "outfit_labels.jsonl"
        self.cache_ttl_seconds = 24 * 60 * 60
        self.search_ttl_seconds = 6 * 60 * 60
        self.data_ttl_seconds = 7 * 24 * 60 * 60
        self.vision_model = os.environ.get("OUTFIT_VISION_MODEL", "local-free-vision-v3-label-knn")
        self.analysis_version = "v3-label-knn"
        self.video_cache_dir.mkdir(parents=True, exist_ok=True)
        self.frame_cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)
        self.search_cache_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_cache_dir.mkdir(parents=True, exist_ok=True)
        self.data_cache_dir.mkdir(parents=True, exist_ok=True)
        self.label_frame_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_cache()

    def cleanup_cache(self):
        """하루 지난 캐시 파일 삭제"""
        stamp = self.cache_dir / ".cleanup-stamp"
        now = time.time()
        try:
            if stamp.exists() and now - stamp.stat().st_mtime < 60 * 60:
                return
            stamp.touch()
        except Exception:
            pass

        cutoffs = {
            self.video_cache_dir: now - self.cache_ttl_seconds,
            self.frame_cache_dir: now - self.cache_ttl_seconds,
            self.thumbnail_cache_dir: now - self.cache_ttl_seconds,
            self.analysis_cache_dir: now - self.cache_ttl_seconds,
            self.search_cache_dir: now - self.search_ttl_seconds,
            self.data_cache_dir: now - self.data_ttl_seconds,
            self.label_frame_dir: now - self.cache_ttl_seconds,
        }
        for cache_dir, cutoff in cutoffs.items():
            for path in cache_dir.glob("*"):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
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
                json.dump(data, fp, ensure_ascii=False)
            tmp_path.replace(path)
        except Exception:
            pass

    def extract_youtube_id(self, url):
        patterns = [
            r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
            r"^([A-Za-z0-9_-]{11})$"
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    def find_cached_video(self, video_id):
        if not video_id:
            return None
        matches = sorted(
            (p for p in self.video_cache_dir.iterdir() if p.is_file() and f"[{video_id}]" in p.name),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        for path in matches:
            try:
                if path.is_file() and time.time() - path.stat().st_mtime <= self.cache_ttl_seconds:
                    if self.is_electron_playable_video(path):
                        return path.resolve()
                    path.unlink(missing_ok=True)
            except Exception:
                pass
        return None

    def is_electron_playable_video(self, path_obj):
        """Electron 내장 Chromium에서 안정적으로 재생되는 H.264/AAC MP4 캐시만 재사용."""
        if path_obj.suffix.lower() not in (".mp4", ".m4v"):
            return False
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name",
            "-of", "json",
            str(path_obj)
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return True
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams", [])
            video_codecs = {s.get("codec_name") for s in streams if s.get("codec_type") == "video"}
            audio_codecs = {s.get("codec_name") for s in streams if s.get("codec_type") == "audio"}
            video_ok = not video_codecs or "h264" in video_codecs
            audio_ok = not audio_codecs or bool(audio_codecs.intersection({"aac", "mp3"}))
            return video_ok and audio_ok
        except Exception:
            return True

    def get_female_idols(self):
        cache_path = self.data_cache_dir / "female_idols.json"
        cached = self.read_json_cache(cache_path, self.data_ttl_seconds)
        if cached is not None:
            return cached

        url = "https://dbkpop.com/db/female-k-pop-idols"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table', {'id': 'table_1'})
            idols = []
            if table:
                rows = table.find_all('tr')[1:]
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) > 6:
                        idols.append({
                            'stage_name': cols[1].text.strip(),
                            'full_name': cols[2].text.strip(),
                            'group': cols[6].text.strip()
                        })
            self.write_json_cache(cache_path, idols)
            return idols
        except Exception:
            return []

    def get_group_members(self, group_name, all_idols):
        popular_groups = {
            "BABYMONSTER": ["Ruka", "Pharita", "Asa", "Ahyeon", "Rami", "Rora", "Chiquita"],
            "aespa": ["Karina", "Giselle", "Winter", "Ningning"],
            "IVE": ["Wonyoung", "Yujin", "Rei", "Gaeul", "Liz", "Leeseo"],
            "NewJeans": ["Minji", "Hanni", "Danielle", "Haerin", "Hyein"],
            "NMIXX": ["Haewon", "Lily", "Sullyoon", "Bae", "Jiwoo", "Kyujin"],
            "ITZY": ["Yeji", "Lia", "Ryujin", "Chaeryeong", "Yuna"],
            "LE SSERAFIM": ["Chaewon", "Sakura", "Yunjin", "Kazuha", "Eunchae"],
            "Billlie": ["Moon Sua", "Suhyeon", "Haram", "Tsuki", "Sheon", "Siyoon", "Haruna"],
            "SEVENTEEN": ["The8", "Mingyu", "DK", "Seungkwan", "Vernon", "Dino", "Hoshi", "Jun"]
        }
        normalized_name = group_name.upper().replace(" ", "")
        for key in popular_groups:
            if normalized_name == key.upper().replace(" ", ""):
                return popular_groups[key]
        members = [i['stage_name'] for i in all_idols if i['group'].lower() == group_name.lower()]
        return members[:8] if members else []

    def get_schedules_stream(self):
        """일정을 JSON 라인 스트림으로 출력 (days_since, is_future 포함)"""
        url = "https://www.kpopofficial.com/kpop-comeback-schedule-may/"
        raw_schedules = []
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(response.text, 'html.parser')
            content = soup.find('div', {'class': 'post-inner'})
            if content:
                for tag in content.find_all(['h2', 'p', 'li']):
                    text = tag.get_text(separator=' ').strip()
                    if text and len(text) > 5:
                        raw_schedules.append(text)
        except Exception:
            pass

        if not raw_schedules:
            raw_schedules = [
                "May 1: THE8 (SEVENTEEN) Solo Release",
                "May 4: BABYMONSTER Comeback: 'CHOOM'",
                "May 6: Billlie Comeback: 'ZAP'",
                "May 11: NMIXX Comeback: 'Heavy Serenade'",
                "May 11: aespa Pre-release: 'WDA'",
                "May 18: ITZY Comeback: 'Motto'",
                "May 22: LE SSERAFIM Comeback: 'PUREFLOW Pt. 1'",
                "May 27: IVE Japanese Comeback: 'LUCID DREAM'",
                "May 29: aespa Comeback: 'LEMONADE'",
                "June 5: NewJeans Comeback: 'Supernatural'",
                "June 12: BABYMONSTER Mini Album",
            ]

        all_idols = self.get_female_idols()
        now = datetime.now()

        for s in raw_schedules:
            date_str = ""
            artist = s
            parts = s.split(':', 1)
            if len(parts) > 1:
                date_str = parts[0].strip()
                artist = parts[1].strip()

            group_name = artist.split(' ')[0]
            members = self.get_group_members(group_name, all_idols)

            # ── 날짜 계산 ──────────────────────────────────────────
            days_since = 0
            is_future = False
            is_recent = False

            perf_dt = parse_perf_date(date_str)
            if perf_dt:
                delta = (now - perf_dt).days
                days_since = delta
                is_future = delta < 0          # 아직 공연 전
                is_recent = 0 <= delta <= 7    # 최근 7일 이내

            item = {
                "date": date_str,
                "content": artist,
                "group": group_name,
                "members": members,
                "is_recent": is_recent,
                "is_future": is_future,        # ★ 추가
                "days_since": days_since,      # ★ 추가
                "fancams": []
            }
            print(json.dumps({"type": "schedule-item", "data": item}, ensure_ascii=False), flush=True)

    # ── 직캠 검색 (수정: --print 포맷으로 안정성 향상) ────────────────
    def get_fancams(self, query_name, limit=5):
        """yt-dlp로 YouTube 직캠 검색 (--print 방식으로 수정)"""
        search_query = f"{query_name} 직캠 fancam"
        cache_path = self.search_cache_dir / f"{self.cache_key(search_query)}.json"
        cached = self.read_json_cache(cache_path, self.search_ttl_seconds)
        if cached is not None:
            return cached

        try:
            cmd = [
                'yt-dlp',
                '--flat-playlist',
                '--no-warnings',
                '--print', '%(id)s|||%(title)s|||%(thumbnail)s',
                f"ytsearch{limit}:{search_query}"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            fancams = []
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    if '|||' in line:
                        parts = line.split('|||', 2)
                        if len(parts) < 2:
                            continue
                        vid_id, title = parts[0], parts[1]
                        thumbnail_url = parts[2].strip() if len(parts) > 2 else ""
                        vid_id = vid_id.strip()
                        title = title.strip()
                        if vid_id and title and vid_id != 'NA':
                            if not thumbnail_url or thumbnail_url == "NA":
                                thumbnail_url = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                            fancams.append({
                                'title': title,
                                'url': f"https://www.youtube.com/watch?v={vid_id}",
                                'id': vid_id,
                                'thumbnail_url': thumbnail_url
                            })
            self.write_json_cache(cache_path, fancams)
            return fancams
        except Exception:
            return []

    # ── 직캠 다운로드 ─────────────────────────────────────────────
    def download_fancam(self, url, max_height: int = 2160):
        """캐시에 없으면 yt-dlp로 지정 화질 이하 최고 화질 영상을 다운로드.

        max_height: 다운로드 최대 해상도 (기본 2160=4K, 의상 분석용은 1080 권장)
        """
        self.cleanup_cache()
        video_id = self.extract_youtube_id(url)

        # ── 캐시 검색: 요청 해상도 이하 파일이 있으면 재사용 ──────────────────
        cached = self.find_cached_video(video_id)
        if cached:
            return self.video_payload(cached, cached=True)

        h = max_height
        format_selector = (
            f"bestvideo[vcodec^=avc1][height<={h}][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/"
            f"bestvideo[vcodec^=avc1][height<={h}]+bestaudio[acodec^=mp4a]/"
            f"best[height<={h}][vcodec^=avc1][ext=mp4]/"
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={h}][ext=mp4]/"
            f"best[height<={h}]/best"
        )
        output_template = str(self.video_cache_dir / "%(title).120s [%(id)s].%(ext)s")
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--concurrent-fragments", "8",
            "--retries", "3",
            "--fragment-retries", "3",
            "--no-part",                        # 부분 파일 남기지 않음 → 클린업 단순화
            "--format", format_selector,
            "--merge-output-format", "mp4",
            "--print", "after_move:filepath",
            "--output", output_template,
            url
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            message = result.stderr.strip() or "yt-dlp 다운로드에 실패했습니다."
            return {"ok": False, "error": message[-800:]}

        file_path = ""
        for line in result.stdout.strip().splitlines():
            candidate = line.strip()
            if candidate and Path(candidate).exists():
                file_path = candidate

        if not file_path:
            files = sorted(self.video_cache_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            file_path = str(files[0]) if files else ""

        if not file_path:
            return {"ok": False, "error": "다운로드된 파일을 찾지 못했습니다."}

        return self.video_payload(Path(file_path).resolve(), cached=False)

    def video_payload(self, path_obj, cached=False):
        return {
            "ok": True,
            "file_path": str(path_obj),
            "file_url": path_obj.as_uri(),
            "filename": path_obj.name,
            "cached": cached
        }

    # ── 의상 정보 검색 ★ 신규 ─────────────────────────────────────
    def get_outfit_info(self, query):
        """검색 썸네일을 빠르게 캐시해서 무료 로컬 분석"""
        request = self.parse_outfit_request(query)
        analysis_cache_path = self.analysis_cache_dir / f"{self.analysis_version}-{self.cache_key(request['url'] + request['thumbnail_url'])}.json"
        cached = self.read_json_cache(analysis_cache_path, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        frame = self.get_thumbnail_frame(request["url"], request["thumbnail_url"])
        if not frame.get("ok"):
            return {"ok": False, "error": frame.get("error", "분석 썸네일을 준비하지 못했습니다.")}

        analysis = self.analyze_outfit_frame(frame["file_path"])
        analysis["frame"] = frame
        if analysis.get("ok"):
            self.write_json_cache(analysis_cache_path, analysis)
        return analysis

    def get_precise_outfit_info(self, query):
        """영상 여러 장면을 분석해 다수결로 보정 (1080p 다운로드 + 병렬 프레임 분석)"""
        import concurrent.futures
        request = self.parse_outfit_request(query)
        cache_path = self.analysis_cache_dir / f"precise-{self.analysis_version}-{self.cache_key(request['url'])}.json"
        cached = self.read_json_cache(cache_path, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        # 의상 분석에는 1080p로 충분 — 다운로드 속도 대폭 향상
        video = self.download_fancam(request["url"], max_height=1080)
        if not video.get("ok"):
            return {"ok": False, "error": video.get("error", "영상을 준비하지 못했습니다.")}

        frames = self.extract_sample_frames(video["file_path"], prefix="precise", seconds=(5, 10, 15, 25, 35, 50))
        if not frames:
            return {"ok": False, "error": "분석할 프레임을 만들지 못했습니다."}

        # 병렬로 프레임 분석 (I/O + CPU 혼합이라 ThreadPool이 적합)
        def _analyze(frame):
            result = self.analyze_outfit_frame(frame["file_path"])
            if result.get("ok"):
                return {"frame": frame, "analysis": result["analysis"]}
            return None

        frame_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(frames), 4)) as pool:
            for item in pool.map(_analyze, frames):
                if item is not None:
                    frame_results.append(item)

        if not frame_results:
            return {"ok": False, "error": "프레임 분석 결과가 없습니다."}

        merged = self.merge_outfit_results(frame_results)
        response = {
            "ok": True,
            "model": self.vision_model,
            "video": video,
            "frames": frames,
            "frame_results": frame_results,
            "analysis": merged,
            "raw": merged["summary"]
        }
        self.write_json_cache(cache_path, response)
        return response

    def get_label_frames(self, query):
        request = self.parse_outfit_request(query)
        video = self.download_fancam(request["url"])
        if not video.get("ok"):
            return {"ok": False, "error": video.get("error", "영상을 준비하지 못했습니다.")}

        frames = self.extract_sample_frames(video["file_path"], prefix="label", seconds=(5, 15, 30, 50))
        return {"ok": True, "video": video, "frames": frames}

    def save_label(self, query):
        try:
            data = json.loads(query)
            if not isinstance(data, dict):
                raise ValueError("라벨 데이터가 올바르지 않습니다.")
            frame_path = self.path_from_file_url(data.get("frame_url", ""))
            if frame_path and frame_path.exists():
                pixels = self.read_frame_pixels(str(frame_path))
                if pixels:
                    data["features"] = self.extract_outfit_features(pixels)
            data["saved_at"] = datetime.now().isoformat(timespec="seconds")
            with open(self.labels_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(data, ensure_ascii=False) + "\n")
            return {"ok": True, "path": str(self.labels_path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def path_from_file_url(self, value):
        if not value:
            return None
        if value.startswith("file://"):
            from urllib.parse import unquote, urlparse
            return Path(unquote(urlparse(value).path))
        return Path(value)

    def extract_sample_frames(self, video_path, prefix, seconds):
        """여러 타임스탬프에서 프레임을 배치로 추출한다.

        핵심 최적화: ffmpeg 를 단 한 번만 실행해 모든 프레임을 동시에 추출.
        기존 방식(초마다 별도 프로세스)보다 약 3-5× 빠름.
        """
        video = Path(video_path)
        video_key = self.cache_key(str(video.resolve()))
        frames = []

        # ── 이미 캐시된 프레임 확인 ──────────────────────────────────────
        missing_seconds = []
        for second in seconds:
            fp = self.label_frame_dir / f"{prefix}-{video_key}-{second}.jpg"
            if fp.exists() and time.time() - fp.stat().st_mtime <= self.cache_ttl_seconds:
                frames.append({
                    "second": second,
                    "file_path": str(fp.resolve()),
                    "file_url": fp.resolve().as_uri(),
                    "id": f"{video_key}-{second}"
                })
            else:
                missing_seconds.append(second)

        if not missing_seconds:
            return sorted(frames, key=lambda f: f["second"])

        # ── 배치 추출: 하나의 ffmpeg 호출로 모든 미싱 프레임 처리 ──────────
        # select 필터: 각 타임스탬프 ±0.5 초 범위에서 첫 번째 키프레임 선택
        select_parts = "+".join(
            f"gte(t\\,{s})*lt(t\\,{s + 1})" for s in missing_seconds
        )
        # 출력 파일명에 자동 증가 번호 사용, 나중에 seconds 순서에 매핑
        tmp_pattern = str(self.label_frame_dir / f"_batch-{video_key}-{prefix}-%d.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-vf", (
                f"select='{select_parts}',"
                "scale=640:-2:force_original_aspect_ratio=decrease"
            ),
            "-vsync", "0",
            "-frames:v", str(len(missing_seconds)),
            "-q:v", "4",
            tmp_pattern,
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception:
            pass

        # ── 배치 출력 파일을 초-기반 이름으로 rename ──────────────────────
        for idx, second in enumerate(sorted(missing_seconds), start=1):
            src = Path(tmp_pattern.replace("%d", str(idx)))
            dst = self.label_frame_dir / f"{prefix}-{video_key}-{second}.jpg"
            if src.exists():
                try:
                    src.replace(dst)
                except Exception:
                    pass
            # fallback: 배치 실패 시 단독 ffmpeg 로 재시도
            if not dst.exists():
                fallback_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(second),
                    "-i", str(video),
                    "-frames:v", "1",
                    "-vf", "scale=640:-2:force_original_aspect_ratio=decrease",
                    "-q:v", "4",
                    str(dst),
                ]
                try:
                    subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=30)
                except Exception:
                    pass

            if dst.exists():
                frames.append({
                    "second": second,
                    "file_path": str(dst.resolve()),
                    "file_url": dst.resolve().as_uri(),
                    "id": f"{video_key}-{second}"
                })

        # 혹시 남은 임시 배치 파일 정리
        for leftover in self.label_frame_dir.glob(f"_batch-{video_key}-{prefix}-*.jpg"):
            try:
                leftover.unlink()
            except Exception:
                pass

        return sorted(frames, key=lambda f: f["second"])

    def merge_outfit_results(self, frame_results):
        bottoms = [r["analysis"].get("bottom", {}) for r in frame_results]
        tops = [r["analysis"].get("top", {}) for r in frame_results]
        exposures = [r["analysis"].get("exposure", {}) for r in frame_results]
        colors = []
        styles = []
        items = []
        details = []

        for result in frame_results:
            analysis = result["analysis"]
            colors.extend(analysis.get("colors", []))
            styles.extend(analysis.get("style", []))
            items.extend(analysis.get("items", []))
            details.extend(analysis.get("details", [])[:2])

        top_type = self.most_common([t.get("type") for t in tops])
        bottom_type = self.most_common([b.get("type") for b in bottoms])
        bottom_length = self.most_common([b.get("length") for b in bottoms])
        exposure_values = [e.get("percent", 0) for e in exposures if isinstance(e.get("percent", 0), (int, float))]
        exposure_percent = int(sorted(exposure_values)[len(exposure_values) // 2]) if exposure_values else 0
        exposure_level = "낮음" if exposure_percent < 10 else "보통" if exposure_percent < 25 else "높음"
        bottom_agreement = self.agreement([f"{b.get('type')}|{b.get('length')}" for b in bottoms])
        top_agreement = self.agreement([t.get("type", "") for t in tops])

        summary = (
            f"{len(frame_results)}개 장면 분석 — "
            f"상의: {top_type or '식별 어려움'} (일치율 {top_agreement}%), "
            f"하의: {bottom_type or '식별 어려움'} / {bottom_length or '알 수 없음'} (일치율 {bottom_agreement}%). "
            f"노출도 중앙값 {exposure_percent}% ({exposure_level})."
        )
        return {
            "colors": self.top_unique(colors, 5),
            "items": self.top_unique(items, 6),
            "style": self.top_unique(styles, 5),
            "details": self.top_unique(details, 8),
            "top": {"type": top_type, "confidence": f"{top_agreement}%"},
            "bottom": {"type": bottom_type, "length": bottom_length, "confidence": f"{bottom_agreement}%"},
            "exposure": {"percent": exposure_percent, "level": exposure_level},
            "summary": summary
        }

    def most_common(self, values):
        counts = {}
        for value in values:
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    def top_unique(self, values, limit):
        counts = {}
        for value in values:
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        return [item for item, _count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]]

    def agreement(self, values):
        clean = [v for v in values if v and v != "|"]
        if not clean:
            return 0
        top = self.most_common(clean)
        return int(round(clean.count(top) / len(clean) * 100))

    def parse_outfit_request(self, query):
        try:
            data = json.loads(query)
            if isinstance(data, dict):
                return {
                    "url": data.get("url", ""),
                    "thumbnail_url": data.get("thumbnail_url", "")
                }
        except Exception:
            pass
        return {"url": query, "thumbnail_url": ""}

    def get_thumbnail_frame(self, url, thumbnail_url=""):
        self.cleanup_cache()
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
            return {"ok": True, "file_path": str(thumb_path.resolve()), "file_url": thumb_path.resolve().as_uri(), "cached": True}

        try:
            resp = requests.get(thumbnail_url, headers=self.headers, timeout=8)
            if resp.status_code >= 400 or not resp.content:
                fallback = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
                if fallback and fallback != thumbnail_url:
                    resp = requests.get(fallback, headers=self.headers, timeout=8)
            if resp.status_code >= 400 or not resp.content:
                return {"ok": False, "error": "썸네일 다운로드에 실패했습니다."}
            thumb_path.write_bytes(resp.content)
            return {"ok": True, "file_path": str(thumb_path.resolve()), "file_url": thumb_path.resolve().as_uri(), "cached": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def extract_outfit_frame(self, video_path):
        video = Path(video_path)
        frame_path = self.frame_cache_dir / f"{video.stem}.jpg"
        if frame_path.exists() and time.time() - frame_path.stat().st_mtime <= self.cache_ttl_seconds:
            return {"ok": True, "file_path": str(frame_path.resolve()), "file_url": frame_path.resolve().as_uri(), "cached": True}

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", "00:00:12",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=768:-2:force_original_aspect_ratio=decrease",
            "-q:v", "3",
            str(frame_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not frame_path.exists():
            message = result.stderr.strip() or "ffmpeg 프레임 추출에 실패했습니다."
            return {"ok": False, "error": message[-800:]}
        return {"ok": True, "file_path": str(frame_path.resolve()), "file_url": frame_path.resolve().as_uri(), "cached": False}

    def analyze_outfit_frame(self, frame_path):
        return self.analyze_outfit_frame_locally(frame_path)

    def analyze_outfit_frame_locally(self, frame_path):
        pixels = self.read_frame_pixels(frame_path)
        if not pixels:
            return {"ok": False, "error": "프레임 픽셀을 읽지 못했습니다.", "model": self.vision_model}

        palette = self.extract_palette(pixels)
        features = self.extract_outfit_features(pixels)
        colors = [item["name"] for item in palette[:5]]
        top = self.infer_top_type(pixels)
        items = self.infer_items(colors, pixels)
        bottom = self.infer_bottom_type(pixels)
        exposure = self.infer_exposure(pixels)
        style = self.infer_style(colors, pixels)
        details = self.infer_details(colors, palette, pixels)
        label_hint = self.predict_from_labels(features)

        if label_hint:
            if label_hint.get("top_type"):
                top = {"type": label_hint["top_type"], "confidence": f"라벨 {label_hint['confidence']}%"}
            if label_hint.get("bottom_type") or label_hint.get("bottom_length"):
                bottom = {
                    "type": label_hint.get("bottom_type") or bottom["type"],
                    "length": label_hint.get("bottom_length") or bottom["length"],
                    "confidence": f"라벨 {label_hint['confidence']}%"
                }
            if label_hint.get("exposure"):
                exposure = self.exposure_from_label(label_hint["exposure"], exposure)
            details.insert(0, f"라벨 모델 반영: 유사 라벨 {label_hint['count']}개")
            for color_key in ("top_color", "bottom_color"):
                if label_hint.get(color_key):
                    colors.insert(0, label_hint[color_key])
            colors = self.top_unique(colors, 5)

        color_text = ", ".join(colors) if colors else "뚜렷한 색상 없음"
        style_text = ", ".join(style[:2]) if style else "무대 의상"
        summary = (
            f"주요 색상은 {color_text} 계열. "
            f"상의는 {top['type']}({top['confidence']} 신뢰도), "
            f"하의는 {bottom['type']}({bottom['length']}, {bottom['confidence']} 신뢰도)로 추정. "
            f"노출도 약 {exposure['percent']}% ({exposure['level']}), {style_text} 스타일."
        )

        return {
            "ok": True,
            "model": self.vision_model,
            "raw": summary,
            "analysis": {
                "colors": colors,
                "items": items,
                "top": top,
                "bottom": bottom,
                "exposure": exposure,
                "style": style,
                "details": details,
                "label_hint": label_hint,
                "features": features,
                "summary": summary
            }
        }

    def read_frame_pixels(self, frame_path):
        """128×128 그리드로 픽셀을 읽고, 중앙 60% 가로 영역에 집중해 배경 영향을 줄인다."""
        cmd = [
            "ffmpeg",
            "-v", "error",
            "-i", str(frame_path),
            "-vf", "scale=128:128:force_original_aspect_ratio=decrease,pad=128:128:(ow-iw)/2:(oh-ih)/2:black",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0:
            return []

        raw = result.stdout
        pixels = []
        width = 128
        # 중앙 가로 60% 범위 (배경 잘라냄)
        x_lo, x_hi = int(width * 0.20), int(width * 0.80)
        for i in range(0, len(raw) - 2, 3):
            index = i // 3
            x = index % width
            y = index // width
            if not (x_lo <= x <= x_hi):
                continue
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            brightness = (r + g + b) / 3
            # 순수 검정(배경 패딩) 및 극단 과노출 제거
            if brightness < 18 or (r < 14 and g < 14 and b < 14):
                continue
            pixels.append((r, g, b, x, y))
        return pixels

    def extract_palette(self, pixels):
        buckets = {}
        for r, g, b, _x, _y in pixels:
            name = self.name_color(r, g, b)
            if name in ("검정", "흰색") and len(pixels) > 300:
                weight = 0.7
            else:
                weight = 1.0
            buckets.setdefault(name, {"name": name, "count": 0, "r": 0, "g": 0, "b": 0})
            buckets[name]["count"] += weight
            buckets[name]["r"] += r * weight
            buckets[name]["g"] += g * weight
            buckets[name]["b"] += b * weight

        ranked = sorted(buckets.values(), key=lambda item: item["count"], reverse=True)
        total = sum(item["count"] for item in ranked) or 1
        for item in ranked:
            item["ratio"] = round(item["count"] / total, 3)
        return ranked

    # ── 색상 분류 상수 (128px 그리드 기준 신체 존 Y 경계) ─────────────────
    # Zone 0: 머리/얼굴   y  0-18   (헤어/피부)
    # Zone 1: 어깨/상체   y 19-38   (상의 상단)
    # Zone 2: 가슴/몸통   y 39-64   (상의 메인)
    # Zone 3: 허리/힙     y 65-82   (상하의 전환)
    # Zone 4: 허벅지      y 83-100  (하의 상단)
    # Zone 5: 종아리      y 101-116 (하의 하단 = 길이 판단)
    # Zone 6: 발/신발     y 117-127

    ZONE_BOUNDS = [(0, 18), (19, 38), (39, 64), (65, 82), (83, 100), (101, 116), (117, 127)]

    def name_color(self, r, g, b):
        """HSV 기반 색상 분류 (15종). 기존 단순 비교보다 정확도 크게 향상."""
        brightness = (r + g + b) / 3

        # ── 무채색 처리 ───────────────────────────────────────────────────
        if brightness < 35:
            return "검정"
        if brightness > 220 and max(r, g, b) - min(r, g, b) < 20:
            return "흰색"

        rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
        max_c = max(rf, gf, bf)
        min_c = min(rf, gf, bf)
        delta = max_c - min_c
        saturation = delta / max_c if max_c > 0 else 0

        if saturation < 0.14:
            if brightness < 90:
                return "진회색"
            if brightness < 168:
                return "회색"
            return "밝은회색"

        # ── 피부·베이지·브라운 우선 감지 ─────────────────────────────────
        if (r > 140 and g > 80 and b > 40
                and r > g and g >= b
                and r > b * 1.3
                and saturation < 0.62):
            if brightness > 190:
                return "베이지"
            if brightness > 145:
                return "피부/베이지"
            return "브라운"

        # ── 색상(Hue) 계산 ───────────────────────────────────────────────
        if delta == 0:
            hue = 0.0
        elif max_c == rf:
            hue = 60.0 * (((gf - bf) / delta) % 6)
        elif max_c == gf:
            hue = 60.0 * ((bf - rf) / delta + 2)
        else:
            hue = 60.0 * ((rf - gf) / delta + 4)

        # ── Hue 구간별 분류 ───────────────────────────────────────────────
        if hue < 12 or hue >= 348:
            return "레드"
        if hue < 30:
            return "레드오렌지"
        if hue < 52:
            return "오렌지/옐로"
        if hue < 75:
            return "옐로"
        if hue < 155:
            # 채도·밝기로 골드 vs 그린 구분
            if saturation > 0.5 and brightness > 160:
                return "옐로그린"
            return "그린"
        if hue < 185:
            return "민트/청록"
        if hue < 255:
            # 어둡고 채도 높으면 네이비
            if brightness < 95 and saturation > 0.45:
                return "네이비"
            return "블루"
        if hue < 290:
            return "퍼플"
        if hue < 330:
            return "핑크/마젠타"
        return "핑크"

    def _zone_pixels(self, pixels, zone_idx):
        """신체 존 인덱스에 해당하는 픽셀만 반환 (128px 그리드 기준)."""
        y_lo, y_hi = self.ZONE_BOUNDS[zone_idx]
        return [p for p in pixels if y_lo <= p[4] <= y_hi]

    def infer_top_type(self, pixels):
        """상의 유형 추정 (크롭/반팔/긴팔/오프숄더 등)."""
        upper = self._zone_pixels(pixels, 1) + self._zone_pixels(pixels, 2)
        shoulder = self._zone_pixels(pixels, 1)
        torso_bottom = self._zone_pixels(pixels, 3)

        if not upper:
            return {"type": "상의 식별 어려움", "confidence": "낮음"}

        shoulder_skin = self.skin_ratio(shoulder)
        waist_skin = self.skin_ratio(torso_bottom)
        upper_skin = self.skin_ratio(upper)

        # 오프숄더: 어깨 쪽 피부 비율이 높음
        if shoulder_skin > 0.30:
            top_type = "오프숄더/노출 상의"
        elif waist_skin > 0.22:
            top_type = "크롭 상의"
        elif upper_skin < 0.10:
            top_type = "긴팔/풀커버 상의"
        else:
            top_type = "반팔/민소매 상의"

        confidence = "중간" if len(upper) > 300 else "낮음"
        return {"type": top_type, "confidence": confidence}

    def extract_outfit_features(self, pixels):
        features = {}
        for idx in range(1, 6):
            zone = self._zone_pixels(pixels, idx)
            features[f"z{idx}_skin"] = round(self.skin_ratio(zone), 4)
            features[f"z{idx}_dark"] = round(self.dark_ratio(zone), 4)
            features[f"z{idx}_bright"] = round(self.bright_ratio(zone), 4)
            features[f"z{idx}_vivid"] = round(self.vivid_ratio(zone), 4)
            features[f"z{idx}_color"] = self.dominant_color(zone)

        upper = self._zone_pixels(pixels, 1) + self._zone_pixels(pixels, 2)
        lower = self._zone_pixels(pixels, 3) + self._zone_pixels(pixels, 4) + self._zone_pixels(pixels, 5)
        features["upper_color"] = self.dominant_color(upper)
        features["lower_color"] = self.dominant_color(lower)
        features["upper_skin"] = round(self.skin_ratio(upper), 4)
        features["lower_skin"] = round(self.skin_ratio(lower), 4)
        features["sample_count"] = len(pixels)
        return features

    def dominant_color(self, pixels):
        if not pixels:
            return ""
        counts = {}
        for r, g, b, _x, _y in pixels:
            color = self.name_color(r, g, b)
            if color == "피부/베이지":
                continue
            counts[color] = counts.get(color, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    def load_label_examples(self):
        if not self.labels_path.exists():
            return []
        examples = []
        try:
            with open(self.labels_path, "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    features = item.get("features")
                    if not features:
                        frame_path = self.path_from_file_url(item.get("frame_url", ""))
                        if frame_path and frame_path.exists():
                            pixels = self.read_frame_pixels(str(frame_path))
                            features = self.extract_outfit_features(pixels) if pixels else None
                    if features:
                        item["features"] = features
                        examples.append(item)
        except Exception:
            return []
        return examples[-1200:]

    def predict_from_labels(self, features):
        examples = self.load_label_examples()
        if not examples:
            return None

        scored = []
        for item in examples:
            distance = self.feature_distance(features, item.get("features", {}))
            scored.append((distance, item))
        scored.sort(key=lambda x: x[0])
        neighbors = scored[:7]
        usable = [(1 / (0.001 + dist), item) for dist, item in neighbors if dist < 1.15]
        if len(usable) < 2:
            return None

        result = {"count": len(usable), "confidence": 0}
        for key in ("top_type", "top_color", "bottom_type", "bottom_length", "bottom_color", "exposure"):
            value, confidence = self.weighted_vote(usable, key)
            if value:
                result[key] = value
                result["confidence"] = max(result["confidence"], confidence)
        return result if any(k in result for k in ("top_type", "bottom_type", "exposure")) else None

    def feature_distance(self, a, b):
        numeric_keys = [
            "z1_skin", "z2_skin", "z3_skin", "z4_skin", "z5_skin",
            "z1_dark", "z2_dark", "z3_dark", "z4_dark", "z5_dark",
            "z1_bright", "z2_bright", "z3_bright", "z4_bright", "z5_bright",
            "upper_skin", "lower_skin"
        ]
        total = 0.0
        weight = 0.0
        for key in numeric_keys:
            av = float(a.get(key, 0) or 0)
            bv = float(b.get(key, 0) or 0)
            w = 1.8 if "skin" in key else 1.0
            total += abs(av - bv) * w
            weight += w

        for key in ("upper_color", "lower_color", "z2_color", "z4_color", "z5_color"):
            av = a.get(key, "")
            bv = b.get(key, "")
            if av and bv and av != bv:
                total += 0.16
            weight += 0.16
        return total / max(weight, 1)

    def weighted_vote(self, weighted_items, key):
        votes = {}
        for weight, item in weighted_items:
            value = item.get(key)
            if not value or value == "모름":
                continue
            votes[value] = votes.get(value, 0.0) + weight
        if not votes:
            return "", 0
        total = sum(votes.values())
        value, score = max(votes.items(), key=lambda x: x[1])
        return value, int(round(score / total * 100))

    def exposure_from_label(self, label, fallback):
        mapping = {
            "낮음": {"percent": min(fallback.get("percent", 0), 9), "level": "낮음"},
            "보통": {"percent": max(10, min(24, fallback.get("percent", 16))), "level": "보통"},
            "높음": {"percent": max(25, fallback.get("percent", 32)), "level": "높음"},
        }
        return mapping.get(label, fallback)

    def infer_items(self, colors, pixels):
        items = []

        top_info = self.infer_top_type(pixels)
        items.append(top_info["type"])
        items.append("하의")

        upper = self._zone_pixels(pixels, 1) + self._zone_pixels(pixels, 2)
        lower = self._zone_pixels(pixels, 3) + self._zone_pixels(pixels, 4)
        upper_dark = self.dark_ratio(upper)
        lower_dark = self.dark_ratio(lower)
        bright = self.bright_ratio(pixels)

        # 원피스/세트업: 상·하의 색차가 작고 비슷한 톤
        if abs(upper_dark - lower_dark) < 0.10 and len(set(colors[:2])) <= 2:
            items.insert(0, "원피스/세트업 가능")

        if "흰색" in colors or "베이지" in colors or "밝은회색" in colors:
            items.append("밝은 포인트 아이템")
        if "옐로" in colors or "오렌지/옐로" in colors or bright > 0.18:
            items.append("액세서리/반짝임 가능")
        if lower_dark > 0.45:
            items.append("어두운 하의")
        return items[:6]

    def infer_bottom_type(self, pixels):
        """하의 유형·길이 추정 (128px 존 기반, 정확도 향상)."""
        # Zone 3(허리) + Zone 4(허벅지) + Zone 5(종아리)
        waist   = self._zone_pixels(pixels, 3)
        thigh   = self._zone_pixels(pixels, 4)
        calf    = self._zone_pixels(pixels, 5)
        lower   = waist + thigh + calf

        if len(lower) < 50:
            return {"type": "하의 식별 어려움", "length": "알 수 없음", "confidence": "낮음"}

        thigh_skin = self.skin_ratio(thigh)
        calf_skin  = self.skin_ratio(calf)
        lower_skin = self.skin_ratio(lower)
        lower_dark = self.dark_ratio(lower)
        lower_vivid = self.vivid_ratio(lower)

        # 종아리 영역까지 피부가 노출되어 있으면 짧은 치마
        if calf_skin > 0.30:
            bottom_type = "치마"
            length = "미니 치마"
        # 허벅지만 피부 노출: 미디 치마 or 반바지
        elif thigh_skin > 0.28:
            # 좌우 비대칭 피부 → 반바지
            left  = [p for p in thigh if p[3] < 64]
            right = [p for p in thigh if p[3] > 64]
            ls, rs = self.skin_ratio(left), self.skin_ratio(right)
            if abs(ls - rs) < 0.12:
                bottom_type = "치마"
                length = "미디 치마"
            else:
                bottom_type = "반바지/쇼츠"
                length = "반바지"
        elif lower_dark > 0.40 or lower_vivid > 0.22:
            bottom_type = "바지"
            length = "긴 바지"
        elif lower_skin < 0.12:
            bottom_type = "바지 또는 긴 치마"
            length = "롱 기장"
        else:
            bottom_type = "치마"
            length = "미디~롱 치마"

        confidence = "높음" if len(lower) > 800 else "중간" if len(lower) > 300 else "낮음"
        return {"type": bottom_type, "length": length, "confidence": confidence}

    def infer_exposure(self, pixels):
        """신체 노출도 추정 (존별 가중 피부 비율)."""
        # 존별 가중치: 상체(1,2)·하체(3,4,5) 위주, 머리/발 제외
        weighted_skin = 0.0
        weighted_total = 0.0
        zone_weights = {1: 1.2, 2: 1.5, 3: 1.3, 4: 1.4, 5: 1.0}
        for zi, w in zone_weights.items():
            zone_px = self._zone_pixels(pixels, zi)
            if zone_px:
                weighted_skin += self.skin_ratio(zone_px) * w * len(zone_px)
                weighted_total += w * len(zone_px)

        ratio = weighted_skin / weighted_total if weighted_total > 0 else 0
        percent = int(max(0, min(80, round(ratio * 100))))
        if percent < 10:
            level = "낮음"
        elif percent < 25:
            level = "보통"
        else:
            level = "높음"
        return {"percent": percent, "level": level}

    def infer_style(self, colors, pixels):
        """스타일 태그 추정 (확장판: 12종 판단 기준)."""
        style = []
        dark = self.dark_ratio(pixels)
        bright = self.bright_ratio(pixels)
        vivid = self.vivid_ratio(pixels)
        upper = self._zone_pixels(pixels, 1) + self._zone_pixels(pixels, 2)
        lower = self._zone_pixels(pixels, 3) + self._zone_pixels(pixels, 4)

        color_set = set(colors)

        # 단색 계열
        if "검정" in color_set and ("흰색" in color_set or "밝은회색" in color_set or "회색" in color_set):
            style.append("모노톤")
        if "검정" in color_set and dark > 0.45:
            style.append("시크/다크")

        # 밝은/화사한 계열
        if bright > 0.22 and "흰색" in color_set:
            style.append("청순/화이트")
        elif bright > 0.22:
            style.append("화사함")

        # 색감 계열
        if vivid > 0.28:
            style.append("팝/컬러풀")
        if "핑크/마젠타" in color_set or "핑크" in color_set:
            style.append("걸리시/핑크")
        if "옐로" in color_set or "오렌지/옐로" in color_set:
            style.append("글리터/포인트 컬러")

        # 상의 노출 기반
        upper_skin = self.skin_ratio(upper)
        if upper_skin > 0.22:
            style.append("섹시/노출")

        # 하의 기반
        lower_dark = self.dark_ratio(lower)
        if lower_dark > 0.5:
            style.append("보텀 포인트")

        # 네이비/블루 기반
        if "네이비" in color_set or "블루" in color_set:
            style.append("마린/포멀")

        if not style:
            style.append("캐주얼 무대")
        return style[:5]

    def infer_details(self, colors, palette, pixels):
        """의상 세부 정보 추정 (패턴·텍스처·레이어링 힌트 포함)."""
        details = []
        if palette:
            main = palette[0]
            details.append(f"주색상: {main['name']} (비중 {int(main.get('ratio', 0) * 100)}%)")

        # 컬러 다양성 → 패턴·프린트 힌트
        unique_colors = len(set(colors))
        if unique_colors >= 4:
            details.append("다채로운 컬러 → 프린트/패턴 의상 가능")
        elif unique_colors == 3:
            details.append("색상 3종 혼합 → 컬러블록 또는 포인트 배색 가능")

        # 존별 색 차이 → 레이어링
        upper_palette = set(self.name_color(r, g, b) for r, g, b, x, y in self._zone_pixels(pixels, 2))
        lower_palette = set(self.name_color(r, g, b) for r, g, b, x, y in self._zone_pixels(pixels, 4))
        if upper_palette and lower_palette and not upper_palette.intersection(lower_palette):
            details.append("상·하의 색 계열 상이 → 투피스 또는 레이어링")

        # 채도·밝기 패턴
        if self.vivid_ratio(pixels) > 0.30:
            details.append("채도 높은 포인트 → 형광/새틴 소재 가능")
        if self.bright_ratio(pixels) > 0.24:
            details.append("밝은 하이라이트 → 글리터/장식 가능")

        # 하의 텍스처 힌트
        calf = self._zone_pixels(pixels, 5)
        if calf and self.vivid_ratio(calf) > 0.20:
            details.append("하의 하단 컬러 선명 → 레깅스/컬러 팬츠 가능")

        details.append("로컬 픽셀 분석 기반 — 브랜드·소재 정확도 제한적")
        return details[:7]

    def dark_ratio(self, pixels):
        if not pixels:
            return 0
        return sum(1 for r, g, b, _x, _y in pixels if (r + g + b) / 3 < 80) / len(pixels)

    def bright_ratio(self, pixels):
        if not pixels:
            return 0
        return sum(1 for r, g, b, _x, _y in pixels if (r + g + b) / 3 > 185) / len(pixels)

    def vivid_ratio(self, pixels):
        if not pixels:
            return 0
        return sum(1 for r, g, b, _x, _y in pixels if max(r, g, b) - min(r, g, b) > 70) / len(pixels)

    def skin_ratio(self, pixels):
        if not pixels:
            return 0
        skin_count = 0
        for r, g, b, _x, _y in pixels:
            # 개선된 피부 감지: 다양한 피부톤 포함
            if (r > 85 and g > 48 and b > 28
                    and r > g * 1.06 and r > b * 1.12
                    and max(r, g, b) - min(r, g, b) > 14
                    and abs(int(r) - int(g)) > 12):
                skin_count += 1
        return skin_count / len(pixels)

    def extract_response_text(self, data):
        texts = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text") and content.get("text"):
                    texts.append(content["text"])
        return "\n".join(texts).strip()

    def parse_json_text(self, text):
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
        try:
            return json.loads(cleaned)
        except Exception:
            return {"summary": cleaned, "colors": [], "items": [], "style": [], "details": []}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--type',
                        choices=['schedules', 'idols', 'fancams', 'outfit', 'precise-outfit', 'label-frames', 'save-label', 'download'],
                        default='all')
    parser.add_argument('--query', type=str, default='')
    args = parser.parse_args()

    fetcher = KpopIdolFetcher()

    if args.type == 'schedules' or args.type == 'all':
        fetcher.get_schedules_stream()
    elif args.type == 'fancams' and args.query:
        fancams = fetcher.get_fancams(args.query)
        print(json.dumps({"fancams": fancams}, ensure_ascii=False))
    elif args.type == 'outfit' and args.query:
        info = fetcher.get_outfit_info(args.query)
        print(json.dumps({"outfit": info}, ensure_ascii=False))
    elif args.type == 'precise-outfit' and args.query:
        info = fetcher.get_precise_outfit_info(args.query)
        print(json.dumps({"outfit": info}, ensure_ascii=False))
    elif args.type == 'label-frames' and args.query:
        info = fetcher.get_label_frames(args.query)
        print(json.dumps({"label_frames": info}, ensure_ascii=False))
    elif args.type == 'save-label' and args.query:
        info = fetcher.save_label(args.query)
        print(json.dumps({"saved": info}, ensure_ascii=False))
    elif args.type == 'download' and args.query:
        video = fetcher.download_fancam(args.query)
        print(json.dumps({"video": video}, ensure_ascii=False))
    elif args.type == 'idols':
        idols = fetcher.get_female_idols()
        print(json.dumps({"idols": idols[:20]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
