# Clothing Analyzer in C

AI 모델 없이 이미지 픽셀 통계만으로 옷차림을 추정하는 C 기반 휴리스틱 분석기입니다.

분석 항목:

- 바지 길이: `shorts`, `knee_length`, `cropped`, `long`, `unknown`
- 상의 색상
- 하의 색상
- 하의 종류: `shorts`, `long_pants`, `mini_skirt`, `knee_length_skirt`, `long_skirt` 등
- 노출도: `low`, `medium`, `high`
- 영역별 피부 비율과 처리 시간

## Build

```sh
make
```

Linux에서 C 빌드, Python venv 준비, 기본 검증을 한 번에 하려면:

```sh
./build_linux.sh
```

시스템 패키지까지 자동 설치하려면:

```sh
./build_linux.sh --install-system-deps
```

## Python Pipeline Setup

YouTube 직캠에서 썸네일/프레임을 가져와 C 모델에 넣으려면 venv를 사용합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

```sh
./clothing_analyzer image.ppm
```

출력은 JSON 형태입니다.

```json
{
  "upper_color": "blue",
  "lower_color": "black",
  "lower_garment": "long_pants",
  "lower_garment_family": "pants",
  "pants_length": "long",
  "exposure": "low"
}
```

## Image Format

외부 이미지 라이브러리 없이 순수 C로 동작하도록 Netpbm PPM 이미지를 입력으로 받습니다.

JPG/PNG가 있다면 ImageMagick이 설치된 환경에서 변환할 수 있습니다.

```sh
magick input.jpg -auto-orient -resize 512x512 image.ppm
```

## YouTube Frame + Clothing Analysis

```sh
.venv/bin/python youtube_frame_fetcher.py \
  --query "IVE Wonyoung" \
  --limit 1 \
  --seconds 5,15,30 \
  --auto-seconds 5,10,15,20,30,45,60,75,90,120 \
  --max-height 480 \
  --analysis-width 384 \
  --analyze-clothing
```

출력에는 `best_frame_clothing`이 포함됩니다.

## Express API Server

유튜브 링크를 받아 분석 결과를 JSON으로 반환하는 Node.js + Express 서버입니다.

```sh
npm install
npm start
```

Linux에서는 `./build_linux.sh`가 C 바이너리, Python venv, Node 의존성을 같이 준비합니다.

다른 컴퓨터에서 접근할 때 `localhost`를 쓰면 안 됩니다. `localhost`는 요청을 보내는 컴퓨터 자신을 뜻합니다.

- 서버가 GCP e2-micro에 있으면: `http://GCP_EXTERNAL_IP:8000/analyze`
- 서버가 내 컴퓨터에 있으면: 공유기 포트포워딩, VPN, ngrok, Cloudflare Tunnel, SSH reverse tunnel 중 하나가 필요합니다.

GCP e2-micro에서 서버를 열 때:

```sh
SERV_API_API='change-this-secret' HOST=0.0.0.0 PORT=8000 npm start
```

GCP 방화벽에서 TCP `8000`을 허용한 뒤 내 컴퓨터에서 호출합니다.

```sh
curl -X POST http://GCP_EXTERNAL_IP:8000/analyze \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: change-this-secret' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'
```

요청 예시:

```sh
curl -X POST http://localhost:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "query": "IVE Wonyoung",
    "seconds": [5, 10, 15, 20, 30, 45, 60],
    "min_vote_frames": 7,
    "analysis_width": 384,
    "max_height": 480
  }'
```

`query`는 선택값입니다. URL만 보내면 시스템이 인물명을 자동으로 붙이지 않고 빈 값으로 둡니다. `query`를 같이 보내면 URL 영상 제목과 쿼리의 매칭 결과가 `selected.query_match`에 포함되고, 제목이 맞지 않으면 `link_query_mismatch: true` 경고가 붙습니다. 응답에는 `final_clothing`에 7프레임 다수결 결과가 들어갑니다.

환경 변수:

- `PORT`: 서버 포트. 기본값 `8000`
- `HOST`: 바인딩 호스트. 기본값 `0.0.0.0`
- `PYTHON_BIN`: 사용할 Python 경로. 기본값 `.venv/bin/python`
- `MAX_JOBS`: 동시에 처리할 분석 작업 수. 기본값은 CPU에 따라 자동 설정, 최소 `2`
- `MAX_QUEUE`: 동시에 처리하지 못한 요청을 대기열에 쌓는 최대 개수. 기본값 `20`
- `REQUEST_TIMEOUT_MS`: 요청 타임아웃. 기본값 `900000`
- `RESULT_CACHE_TTL_MS`: 같은 URL/옵션 분석 결과를 메모리에 재사용하는 시간. 기본값 `300000`, `0`이면 비활성화
- `RESULT_CACHE_MAX`: 메모리에 유지할 분석 결과 최대 개수. 기본값 `50`
- `DEFAULT_ANALYSIS_WIDTH`: 서버 기본 분석 폭. 기본값 `384`
- `DEFAULT_MAX_HEIGHT`: 서버 기본 YouTube 스트림 높이. 기본값 `480`
- `YOUTUBE_FRAME_SOURCE`: `stream`이면 영상 파일 저장 없이 프레임만 추출, `download`이면 기존 구간 다운로드 방식. 기본값 `stream`
- `YOUTUBE_STREAM_CACHE_TTL_SECONDS`: YouTube 스트림 URL 캐시 시간. 기본값 `600`
- `STREAM_FRAME_FORMAT`: 스트림 프레임 저장 형식. `ppm`이면 변환 없이 C 모델에 바로 넣습니다. 기본값 `ppm`
- `STREAM_FRAME_WORKERS`: 스트림 프레임을 병렬 추출할 ffmpeg 작업 수. 기본값 `4`, 저사양이면 `1`
- `ANALYSIS_WORKERS`: 한 요청 안에서 C 모델 batch 분석을 병렬로 나눠 돌릴 작업 수. 기본값 `4`
- `AUTO_SAMPLE_WEAK_VOTE`: `1`이면 하의 투표가 약할 때 75/90/120초 프레임을 추가 수집합니다. 기본값 `0`
- `LOWER_GARMENT_MODEL`: 작은 하의 보조 모델 JSON 경로. 기본값 `lower_garment_model.json`, `0`이면 비활성화
- `INCLUDE_THUMBNAIL`: `1`이면 서버 응답에 썸네일 다운로드 결과를 포함합니다. 기본값은 제외
- `INCLUDE_THUMBNAIL_ANALYSIS`: `1`이면 썸네일 의상 분석을 포함합니다. 기본값은 제외
- `SERV_API_API`: 설정하면 `/analyze` 요청에 `x-api-key` 헤더가 필요합니다.
- `SERV_API_KEY`: `SERV_API_API`와 같은 용도의 호환 환경 변수입니다.
- `YT_DLP_UPGRADE_INTERVAL_HOURS`: `yt-dlp` 자동 업그레이드 체크 주기. 기본값 `24`, `0`이면 비활성화

동일한 URL/옵션 요청이 동시에 들어오면 서버가 중복으로 다운로드/분석하지 않고 하나의 작업 결과를 여러 응답에 나눠줍니다. 반복 호출은 짧은 시간 동안 `cached_response: true`로 빠르게 반환됩니다.

서버 기본 응답은 `summary-only`라서 프레임별 원본 로그를 빼고 `final_clothing` 중심으로 반환합니다. 전체 프레임 로그가 필요하면 요청 JSON에 `"full_output": true`를 넣으면 됩니다.

기본 분석 경로는 YouTube 영상을 파일로 저장하지 않고 스트림 URL에서 필요한 프레임만 추출합니다. 스트림 추출이 실패하면 기존처럼 필요한 앞 구간만 내려받는 방식으로 자동 전환됩니다.

- `usable`: 분석 결과를 실제로 써도 되는지 여부
- `person_confidence`: 단일 인물 박스 신뢰도
- `color_confidence`: 피부색 보정과 조명 상태를 반영한 색상 신뢰도
- `lower_garment`: 하체 실루엣 분리 기준으로 추정한 하의 종류
- `lower_garment_family`: API에서 바로 쓰기 쉬운 `pants`, `skirt`, `unknown` 분류
- `lower_garment_vote_confidence`: 여러 프레임 다수결에서 최종 하의 종류가 차지한 표 비율
- `lower_garment_vote_margin`: 최종 하의 종류와 2위 후보의 표 차이
- `lower_garment_family_vote_confidence`: 여러 프레임 다수결에서 최종 바지/치마 family가 차지한 표 비율
- `lower_garment_decision`: 최종 하의 라벨, family, 표 비율, 표 차이, votes, `needs_review`를 담은 요약 객체
- `lower_split_ratio`: 하의가 두 다리 형태로 갈라지는 정도
- `lower_center_fill_ratio`: 하의 중앙부가 치마처럼 이어져 보이는 정도
- `analysis_quality`: 사람 박스 품질
- `color_quality`: 옷 색상 품질
- `warnings`: 그룹샷, 넓은 무대샷, 조명 왜곡 경고

여러 테스트를 한 번에 돌리려면:

```sh
.venv/bin/python batch_clothing_test.py
```

캐시된 프레임의 하의 판별을 눈으로 검수하려면:

```sh
.venv/bin/python lower_garment_audit.py
```

이 명령은 `eval_outputs/lower_garment_audit.jpg`와 `eval_outputs/lower_garment_audit.json`을 생성합니다.

CI/로컬 게이트로는 `golden_clothing_eval.py`를 사용합니다. 이 평가는 하의 세부 라벨 정확도와 `pants/skirt` family 정확도, 그리고 family 다수결 신뢰도가 모두 목표치 이상인지 확인합니다.

작은 하의 보조 모델을 다시 학습하려면:

```bash
.venv/bin/python train_lower_garment_model.py --analysis-width 384
```

모델은 C 분석 결과의 수치/카테고리/교차 특징을 입력으로 쓰는 작은 softmax 모델입니다. 골든 캐시와 weak/sparse 영상 샘플을 함께 학습합니다. 결과는 `lower_garment_model.json`에 저장되고, API 응답의 `lower_garment_decision`에 `model_label`, `model_confidence`, `model_votes`로 함께 반환됩니다. C 투표가 `weak_vote`이거나 `sparse_known`이고 모델 투표가 충분히 강하면 `model_override_weak_vote` 또는 `model_sparse_known`으로 최종 라벨에 개입합니다.

검색 기반 실행은 제목에 쿼리 인물/그룹 키워드가 없는 YouTube 결과를 버립니다. `search_filter_eval.py`는 `IVE Wonyoung` 검색에 트와이스 정연 영상이 섞이는 회귀 케이스를 막는 테스트입니다.

`aggregation_warning_eval.py`는 7프레임 모두 하의 종류가 `unknown`인 경우 추가 프레임 수집/수동 검토 경고와 purple 계열 medium 색상 품질 경고가 유지되는지 확인합니다.

## Performance Benchmark

```sh
.venv/bin/python benchmark_performance.py --repeat 5 --analysis-width 384
```

현재 파이프라인은 분석용 이미지를 기본 384px 폭으로 줄여 C 모델에 넣습니다. 캐시가 준비된 상태에서는 C 단일 분석이 대략 수 ms, 3개 쿼리 배치 테스트가 수백 ms 단위로 동작합니다.

## Algorithm

1. 이미지 가장자리 색 평균으로 배경색을 추정합니다.
2. 배경과 다른 픽셀을 전경 후보로 잡고 전경 바운딩 박스를 구합니다.
3. 바운딩 박스를 사람 영역으로 보고 상체/하체/다리 영역으로 세로 분할합니다.
4. YCbCr 기반 피부색 규칙으로 피부 픽셀을 제외합니다.
5. HSV 색상 히스토그램으로 상의/하의 대표 색상을 구합니다.
6. 하체 영역에서 옷 픽셀이 어디까지 내려오는지로 바지 길이를 추정합니다.
7. 전체 피부 비율로 노출도를 추정합니다.

이 방식은 빠르고 설명 가능하지만, 포즈가 크거나 여러 사람이 있거나 배경과 옷 색이 비슷한 경우 정확도가 떨어질 수 있습니다.
