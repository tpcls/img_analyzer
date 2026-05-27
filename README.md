# Clothing Analyzer in C

AI 모델 없이 이미지 픽셀 통계만으로 옷차림을 추정하는 C 기반 휴리스틱 분석기입니다.

분석 항목:

- 바지 길이: `shorts`, `knee_length`, `cropped`, `long`, `unknown`
- 상의 색상
- 하의 색상
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
    "seconds": [5, 10, 15, 20, 30, 45, 60],
    "min_vote_frames": 7,
    "analysis_width": 384,
    "max_height": 480
  }'
```

응답에는 `final_clothing`에 7프레임 다수결 결과가 들어갑니다.

환경 변수:

- `PORT`: 서버 포트. 기본값 `8000`
- `HOST`: 바인딩 호스트. 기본값 `0.0.0.0`
- `PYTHON_BIN`: 사용할 Python 경로. 기본값 `.venv/bin/python`
- `MAX_JOBS`: 동시에 처리할 분석 작업 수. 기본값 `1`
- `REQUEST_TIMEOUT_MS`: 요청 타임아웃. 기본값 `900000`
- `SERV_API_API`: 설정하면 `/analyze` 요청에 `x-api-key` 헤더가 필요합니다.
- `SERV_API_KEY`: `SERV_API_API`와 같은 용도의 호환 환경 변수입니다.

- `usable`: 분석 결과를 실제로 써도 되는지 여부
- `person_confidence`: 단일 인물 박스 신뢰도
- `color_confidence`: 피부색 보정과 조명 상태를 반영한 색상 신뢰도
- `analysis_quality`: 사람 박스 품질
- `color_quality`: 옷 색상 품질
- `warnings`: 그룹샷, 넓은 무대샷, 조명 왜곡 경고

여러 테스트를 한 번에 돌리려면:

```sh
.venv/bin/python batch_clothing_test.py
```

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
