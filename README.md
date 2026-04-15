# shinhan_stt

YouTube Live 또는 오디오 URL을 받아 AssemblyAI Streaming STT로 영어 음성을 전사하고, OpenAI Responses API로 자연스러운 한글 자막을 만드는 FastAPI 서비스입니다.

## 구조

- `apps/gateway/app/ingest.py`: `yt-dlp`로 URL을 해석하고 `ffmpeg`로 16kHz mono PCM 오디오를 생성합니다.
- `apps/gateway/app/assemblyai_stt.py`: AssemblyAI Streaming WebSocket에 오디오를 전송하고 turn 이벤트를 받습니다.
- `apps/gateway/app/translation.py`: OpenAI Responses API로 문맥 기반 한글 번역을 수행합니다.
- `apps/gateway/app/main.py`: 브라우저 WebSocket 세션을 관리하고 STT/번역 이벤트를 UI에 전달합니다.
- `apps/gateway/static/index.html`: 모바일/데스크톱용 실시간 화면입니다.

## 로컬 실행

Python 3.11 환경에서 실행합니다.

```bash
pip install -r apps/gateway/requirements.txt
```

환경변수를 설정합니다.

```bash
set ASSEMBLYAI_API_KEY=your_assemblyai_key
set OPENAI_API_KEY=your_openai_key
set OPENAI_MODEL=gpt-5.3
```

PowerShell에서는 다음처럼 설정할 수 있습니다.

```powershell
$env:ASSEMBLYAI_API_KEY="your_assemblyai_key"
$env:OPENAI_API_KEY="your_openai_key"
$env:OPENAI_MODEL="gpt-5.3"
```

서버를 실행합니다.

```bash
uvicorn app.main:app --app-dir apps/gateway --host 0.0.0.0 --port 8080
```

브라우저에서 엽니다.

```text
http://localhost:8080
```

## Docker 실행

```bash
docker build -t shinhan-live-stt .
docker run --rm -p 8080:8080 \
  -e ASSEMBLYAI_API_KEY=your_assemblyai_key \
  -e OPENAI_API_KEY=your_openai_key \
  -e OPENAI_MODEL=gpt-5.3 \
  shinhan-live-stt
```

## Railway 배포

1. 이 저장소를 GitHub에 push합니다.
2. Railway에서 `New Project`를 누릅니다.
3. `Deploy from GitHub repo`를 선택하고 이 저장소를 연결합니다.
4. Railway가 루트 `Dockerfile`과 `railway.json`을 자동으로 사용합니다.
5. Railway 프로젝트의 `Variables`에 아래 값을 추가합니다.

```env
ASSEMBLYAI_API_KEY=your_assemblyai_key
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-5.3
ASSEMBLYAI_STREAMING_MODEL=u3-rt-pro
ASSEMBLYAI_SAMPLE_RATE=16000
ASSEMBLYAI_FORMAT_TURNS=true
ASSEMBLYAI_MIN_TURN_SILENCE_MS=700
ASSEMBLYAI_MAX_TURN_SILENCE_MS=1600
INGRESS_CHUNK_SECONDS=0.16
TRANSLATION_CONTEXT_SIZE=4
OPENAI_TIMEOUT=20
```

6. Railway의 `Settings` 또는 `Networking`에서 public domain을 생성합니다.
7. 배포가 완료되면 `https://...railway.app/health`가 `status: ok`를 반환하는지 확인합니다.
8. 휴대폰에서 Railway public URL을 열고 YouTube Live URL을 입력합니다.

## YouTube 차단 대응

Railway 같은 클라우드 서버에서는 YouTube가 `yt-dlp` 요청을 차단할 수 있습니다. 이 경우 아래 순서로 대응합니다.

1. 먼저 같은 영상의 직접 HLS 또는 오디오 URL을 입력해 확인합니다.
2. Railway Variables에 프록시를 설정합니다.

```env
YTDLP_PROXY_URL=http://user:password@proxy-host:port
```

3. 로그인 쿠키가 필요한 영상이면 Netscape cookies.txt 내용을 Railway 변수로 넣습니다. 일반 텍스트 또는 base64 문자열 모두 지원합니다.

```env
YTDLP_COOKIES=# Netscape HTTP Cookie File ...
```

4. 쿠키 파일을 컨테이너에 직접 둘 수 있는 환경이면 파일 경로를 지정합니다.

```env
YTDLP_COOKIES_FILE=/app/cookies.txt
```

YouTube 차단은 서비스 코드만으로 100% 해결할 수 있는 문제가 아니라 서버 IP, 영상 정책, 로그인 요구 여부에 따라 달라집니다. 이 프로젝트는 최신 `yt-dlp`, 브라우저 User-Agent, Android/Web player client, 프록시, 쿠키 fallback을 지원하도록 구성되어 있습니다.

## 보안

API key는 코드에 넣지 말고 환경변수로만 설정하세요. 채팅이나 커밋에 노출된 key는 즉시 폐기하고 새로 발급하는 것이 안전합니다.
