# shinhan_stt

IR 웹캐스트 URL을 서버가 직접 가져와 AssemblyAI STT로 영어를 전사하고, OpenAI로 한국어 실시간 자막을 만드는 FastAPI 서비스입니다.

현재 기본 테스트 URL:

```text
https://ir.tesla.com/webcast-2026-01-28
```

## 구조

```text
IR Webcast URL
  └─ 서버: 웹페이지 HTML에서 iframe/미디어/YouTube/웹캐스트 링크 탐색
       └─ yt-dlp 또는 ffmpeg로 실제 오디오 추출
            └─ AssemblyAI Streaming STT
                 └─ OpenAI 번역
                      └─ WebSocket으로 영어/한국어 자막 표시
```

직접 HLS/MP4/audio URL도 지원합니다.

## Railway Variables

Railway `Variables`에 아래 값을 넣습니다.

```env
ASSEMBLYAI_API_KEY=your_assemblyai_key
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_FALLBACK_MODEL=gpt-4o-mini

DEFAULT_STREAM_URL=https://ir.tesla.com/webcast-2026-01-28
AUTO_START_STREAM=false

ASSEMBLYAI_STREAMING_MODEL=u3-rt-pro
ASSEMBLYAI_SAMPLE_RATE=16000
ASSEMBLYAI_FORMAT_TURNS=true
ASSEMBLYAI_MIN_TURN_SILENCE_MS=250
ASSEMBLYAI_MAX_TURN_SILENCE_MS=550

INGRESS_CHUNK_SECONDS=0.08
TRANSLATION_CONTEXT_SIZE=4
PARTIAL_TRANSLATION_DELAY=0.0
PARTIAL_TRANSLATION_INTERVAL=1.0
PARTIAL_TRANSLATION_CONCURRENCY=3
FINAL_PUNCTUATION_DELAY=0.25
OPENAI_TIMEOUT=20

YTDLP_IMPERSONATE=chrome
YTDLP_PLAYER_CLIENTS=mweb,web_safari,tv,android,web
YTDLP_PROXY_URL=
YTDLP_COOKIES=
YTDLP_COOKIES_B64=
YTDLP_COOKIES_FILE=
YTDLP_VISITOR_DATA=
YTDLP_PO_TOKEN=
YTDLP_DATA_SYNC_ID=
```

## Railway 배포

1. 변경사항을 GitHub에 push합니다.
2. Railway에서 `New Project`를 누릅니다.
3. `Deploy from GitHub repo`를 선택합니다.
4. 이 저장소를 연결합니다.
5. Railway가 루트 `Dockerfile`과 `railway.json`을 사용해 배포합니다.
6. Railway `Variables`에 위 값을 넣습니다.
7. Railway에서 public domain을 생성합니다.
8. 아래 주소를 열어 상태를 확인합니다.

```text
https://your-app.up.railway.app/health
https://your-app.up.railway.app/config
```

## 사용법

1. 서비스 URL을 엽니다.
2. 입력창에 아래 URL이 들어 있는지 확인합니다.

```text
https://ir.tesla.com/webcast-2026-01-28
```

3. `URL 시작`을 누릅니다.
4. 서버가 페이지 내부의 실제 웹캐스트/미디어 URL을 찾아 오디오를 추출합니다.
5. 영어 STT와 한국어 번역이 화면에 표시됩니다.

## 실시간 자막 방식

- 영어 partial STT가 오면 즉시 영어 자막을 표시합니다.
- 문장이 끝날 때까지 기다리지 않고 partial 한국어 번역을 계속 시도합니다.
- AssemblyAI가 `end_of_turn`을 보내면 최종 영어 문장으로 다시 한국어 확정 번역을 만듭니다.
- 최종 번역은 화면 효과와 함께 기존 초안 자막을 교체합니다.

## 웹캐스트 URL 해석 방식

`https://ir.tesla.com/webcast-2026-01-28` 같은 IR 페이지는 실제 미디어 URL이 페이지 내부 iframe이나 스크립트에 숨어 있을 수 있습니다.

이 프로젝트는 URL 시작 시 아래 순서로 시도합니다.

1. 입력 URL이 직접 미디어 URL인지 확인
2. `yt-dlp`로 입력 URL 직접 해석
3. 실패하면 HTML을 받아 iframe, href, src, JSON 안의 URL 후보 탐색
4. YouTube embed URL은 watch URL로 변환
5. m3u8, mp4, mp3, YouTube, Brightcove, livestream, ON24, Akamai, CloudFront 후보를 다시 해석
6. 성공한 실제 오디오를 ffmpeg로 16kHz PCM 변환

## 주의

- Tesla IR 페이지 내부가 YouTube로 연결되어 있고 Railway가 그 YouTube URL을 차단하면 쿠키/프록시가 필요할 수 있습니다.
- API key와 쿠키는 GitHub에 올리지 말고 Railway Variables에만 넣으세요.

## 로컬 실행

```powershell
$env:ASSEMBLYAI_API_KEY="your_assemblyai_key"
$env:OPENAI_API_KEY="your_openai_key"
$env:OPENAI_MODEL="gpt-4.1-mini"
$env:DEFAULT_STREAM_URL="https://ir.tesla.com/webcast-2026-01-28"
```

```bash
pip install -r apps/gateway/requirements.txt
uvicorn app.main:app --app-dir apps/gateway --host 0.0.0.0 --port 8080
```

브라우저에서 엽니다.

```text
http://localhost:8080
```
