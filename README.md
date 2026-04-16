# shinhan_stt

YouTube Live 어닝콜 URL을 받아 영상 위에 영어/한글 실시간 자막을 표시하는 FastAPI 서비스입니다.

## 핵심 구조

```text
YouTube Live URL
  ├─ 브라우저: YouTube iframe으로 영상 표시
  └─ 서버: yt-dlp + ffmpeg로 오디오 추출
       └─ AssemblyAI Streaming STT
            └─ OpenAI 번역
                 └─ WebSocket으로 영상 위 자막 표시
```

직접 HLS/MP4/audio URL도 보조 입력으로 지원하지만, 이 서비스의 기본 입력은 YouTube URL입니다.

## Railway 배포

1. 변경사항을 GitHub에 push합니다.
2. Railway에서 `New Project`를 누릅니다.
3. `Deploy from GitHub repo`를 선택합니다.
4. 이 저장소를 연결합니다.
5. Railway가 루트 `Dockerfile`과 `railway.json`을 사용해 배포합니다.
6. Railway `Variables`에 아래 값을 넣습니다.

```env
ASSEMBLYAI_API_KEY=your_assemblyai_key
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_FALLBACK_MODEL=gpt-4o-mini

DEFAULT_STREAM_URL=https://www.youtube.com/watch?v=replace-with-video-id
AUTO_START_STREAM=true

ASSEMBLYAI_STREAMING_MODEL=u3-rt-pro
ASSEMBLYAI_SAMPLE_RATE=16000
ASSEMBLYAI_MIN_TURN_SILENCE_MS=250
ASSEMBLYAI_MAX_TURN_SILENCE_MS=550

INGRESS_CHUNK_SECONDS=0.08
TRANSLATION_CONTEXT_SIZE=4
PARTIAL_TRANSLATION_DELAY=0.0
PARTIAL_TRANSLATION_INTERVAL=0.65
FINAL_PUNCTUATION_DELAY=0.25
OPENAI_TIMEOUT=20

YTDLP_IMPERSONATE=chrome
```

7. Railway에서 public domain을 생성합니다.
8. 아래 주소를 열어 정상 상태를 확인합니다.

```text
https://your-app.up.railway.app/health
https://your-app.up.railway.app/config
```

## YouTube 차단 대응

YouTube를 메인으로 쓰려면 서버 차단 대응이 필요합니다. Railway 같은 클라우드 서버에서 YouTube URL을 자주 가져오면 아래 오류가 날 수 있습니다.

```text
Sign in to confirm you're not a bot
```

이 경우 코드 문제가 아니라 YouTube가 Railway 서버 IP를 봇으로 판단한 것입니다. 제품 운영에서는 아래 중 하나를 넣어야 합니다.

### 1. 운영용 YouTube 쿠키

테스트용 Google 계정으로 YouTube에 로그인한 뒤 `cookies.txt`를 export하고 base64로 변환합니다.

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt")) | Set-Clipboard
```

Railway Variables에 추가합니다.

```env
YTDLP_COOKIES_B64=base64로_변환한_cookies_txt
```

재배포 후 `/health`에서 아래처럼 보여야 합니다.

```json
"youtube_cookies": "configured"
```

### 2. 프록시

쿠키만으로 부족하거나 Railway IP가 강하게 막히면 프록시를 붙입니다.

```env
YTDLP_PROXY_URL=http://user:password@proxy-host:port
```

무료 프록시는 YouTube에서 자주 막힙니다. 운영용이면 안정적인 유료 프록시를 권장합니다.

## 로컬 실행

```powershell
$env:ASSEMBLYAI_API_KEY="your_assemblyai_key"
$env:OPENAI_API_KEY="your_openai_key"
$env:OPENAI_MODEL="gpt-4.1-mini"
$env:DEFAULT_STREAM_URL="https://www.youtube.com/watch?v=replace-with-video-id"
```

```bash
pip install -r apps/gateway/requirements.txt
uvicorn app.main:app --app-dir apps/gateway --host 0.0.0.0 --port 8080
```

브라우저에서 엽니다.

```text
http://localhost:8080
```

## 주의

- YouTube URL은 메인 입력입니다.
- 하지만 YouTube가 서버 접근을 막으면 `YTDLP_COOKIES_B64` 또는 `YTDLP_PROXY_URL`이 필요합니다.
- API key와 쿠키는 GitHub에 올리지 말고 Railway Variables에만 넣으세요.
