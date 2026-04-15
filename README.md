# shinhan_stt

YouTube Live 또는 오디오 URL을 받아 AssemblyAI Streaming STT로 영어 음성을 전사하고, OpenAI Responses API로 자연스러운 한글 자막을 만드는 FastAPI 서비스입니다.

## 구조

- `apps/gateway/app/ingest.py`: `yt-dlp`로 URL을 해석하고 `ffmpeg`로 16kHz mono PCM 오디오를 생성합니다.
- `apps/gateway/app/assemblyai_stt.py`: AssemblyAI Streaming WebSocket에 오디오를 전송하고 turn 이벤트를 받습니다.
- `apps/gateway/app/translation.py`: OpenAI Responses API로 문맥 기반 한글 번역을 수행합니다.
- `apps/gateway/app/main.py`: 브라우저 WebSocket 세션을 관리하고 STT/번역 이벤트를 UI에 전달합니다.
- `apps/gateway/static/index.html`: 모바일/데스크톱용 실시간 화면입니다.

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
YTDLP_IMPERSONATE=chrome
```

6. Railway의 `Settings` 또는 `Networking`에서 public domain을 생성합니다.
7. 배포가 완료되면 `https://...railway.app/health`가 `status: ok`를 반환하는지 확인합니다.
8. 휴대폰에서 Railway public URL을 열고 YouTube Live URL을 입력합니다.

## YouTube 봇 확인 오류 해결

Railway에서 아래 오류가 뜨면 YouTube가 Railway 서버 IP를 봇으로 본 것입니다.

```text
Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.
```

Railway 컨테이너에는 브라우저가 없으므로 `--cookies-from-browser`는 사용할 수 없습니다. 대신 내 브라우저에서 YouTube 쿠키를 `cookies.txt`로 내보내고, 그 내용을 Railway 환경변수에 넣어야 합니다.

이 프로젝트는 `yt-dlp`의 브라우저 impersonation도 켜둡니다. Railway Variables에 아래 값이 있으면 Chrome처럼 요청을 보냅니다.

```env
YTDLP_IMPERSONATE=chrome
```

이 설정은 성공률을 올려주지만, YouTube가 로그인을 요구하는 경우에는 쿠키가 여전히 필요합니다.

### 권장 방식: YTDLP_COOKIES_B64

1. PC 브라우저에서 YouTube에 로그인합니다.
2. 브라우저 확장 프로그램으로 YouTube cookies.txt를 내보냅니다. Netscape `cookies.txt` 형식이어야 합니다.
3. 내보낸 파일 이름이 `cookies.txt`라고 가정하고 PowerShell에서 base64로 변환합니다.

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt")) | Set-Clipboard
```

4. Railway 프로젝트의 `Variables`에 아래 변수를 추가하고, 값에는 클립보드에 복사된 base64 문자열을 붙여넣습니다.

```env
YTDLP_COOKIES_B64=base64로_변환한_cookies_txt
```

5. Railway에서 `Redeploy`를 누릅니다.
6. 다시 서비스 URL에 접속해서 YouTube URL을 테스트합니다.

### 대안: 일반 텍스트 쿠키

Railway 변수에 여러 줄 텍스트를 안정적으로 붙여넣을 수 있다면 아래 방식도 됩니다.

```env
YTDLP_COOKIES=# Netscape HTTP Cookie File ...
```

다만 여러 줄 환경변수는 실수하기 쉬워서 `YTDLP_COOKIES_B64`를 권장합니다.

### 그래도 막히는 경우

쿠키를 넣었는데도 같은 오류가 나면 프록시가 필요할 수 있습니다.

```env
YTDLP_PROXY_URL=http://user:password@proxy-host:port
```

무료/데이터센터 프록시는 YouTube에서 같이 막히는 경우가 많습니다. 가능하면 안정적인 유료 프록시나 직접 HLS/audio URL fallback을 사용하세요.

## 로컬 실행

Python 3.11 환경에서 실행합니다.

```bash
pip install -r apps/gateway/requirements.txt
```

환경변수를 설정합니다.

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

## 보안

API key와 YouTube 쿠키는 코드에 넣지 말고 환경변수로만 설정하세요. 채팅이나 커밋에 노출된 key는 즉시 폐기하고 새로 발급하는 것이 안전합니다.
