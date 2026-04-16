# shinhan_stt

Live earnings-call audio caption service.

The main production path is now admin audio broadcast:

```text
Admin browser audio
  -> WebSocket PCM upload
  -> AssemblyAI Streaming STT
  -> OpenAI Korean translation
  -> Customer audio + English/Korean captions
```

This avoids Railway/YouTube bot blocking because the server does not need to fetch the YouTube video directly. The admin plays the YouTube earnings call in a browser and shares that tab/window/system audio. Customers only receive the audio and captions.

## Railway Variables

For the admin audio broadcast mode, these are the important variables:

```env
ASSEMBLYAI_API_KEY=your_assemblyai_key
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_FALLBACK_MODEL=gpt-4o-mini

ASSEMBLYAI_STREAMING_MODEL=u3-rt-pro
ASSEMBLYAI_SAMPLE_RATE=16000
ASSEMBLYAI_FORMAT_TURNS=true
ASSEMBLYAI_MIN_TURN_SILENCE_MS=250
ASSEMBLYAI_MAX_TURN_SILENCE_MS=550

TRANSLATION_CONTEXT_SIZE=4
PARTIAL_TRANSLATION_DELAY=0.0
PARTIAL_TRANSLATION_INTERVAL=1.0
PARTIAL_TRANSLATION_CONCURRENCY=3
FINAL_PUNCTUATION_DELAY=0.25
OPENAI_TIMEOUT=20
```

Optional URL fallback variables are still supported, but they can be blocked by YouTube on Railway:

```env
DEFAULT_STREAM_URL=
AUTO_START_STREAM=false
INGRESS_CHUNK_SECONDS=0.08

YTDLP_IMPERSONATE=chrome
YTDLP_PLAYER_CLIENTS=mweb,web_safari,web_embedded,web_creator,ios,android,tv
YTDLP_FORCE_IPV4=true
YTDLP_SLEEP_REQUESTS=0.2
YTDLP_PROXY_URL=
YTDLP_COOKIES=
YTDLP_COOKIES_B64=
YTDLP_COOKIES_FILE=
YTDLP_VISITOR_DATA=
YTDLP_PO_TOKEN=
YTDLP_DATA_SYNC_ID=
```

## How To Use

1. Deploy the service on Railway.
2. Customers open the Railway URL and click `Enable Audio`.
3. Admin opens the same Railway URL in Chrome or Edge.
4. Admin plays the YouTube earnings call in another tab, window, or browser.
5. Admin clicks `Start Admin Broadcast`.
6. In the browser sharing dialog, choose the tab/window/screen that contains the earnings call and enable `Share audio` or `System audio`.
7. Customers hear the same audio and see English/Korean captions.

Important: customers do not share their microphone or screen. Only the admin shares the call audio.

## Realtime Caption Behavior

- English partial captions appear as soon as AssemblyAI sends partial turns.
- Korean draft translations are requested continuously, roughly once per second when new English text arrives.
- The Korean draft can change while the speaker is still talking.
- When AssemblyAI marks a turn as complete, the final English sentence is translated again.
- The final Korean translation replaces the draft caption with a visual swap effect.

## Why This Solves YouTube Blocking

The previous server URL mode depended on Railway fetching YouTube directly with `yt-dlp`. YouTube can classify Railway data-center traffic as bot traffic and return `Sign in to confirm you're not a bot`.

Admin audio broadcast changes the path:

```text
YouTube plays in admin browser
  -> admin browser captures the already-playing audio
  -> your server receives raw audio
  -> customers receive audio and captions
```

So YouTube is loaded by a real browser session, not by Railway.

## Deploy

1. Push this repository to GitHub.
2. In Railway, create a new project from the GitHub repository.
3. Add the required variables above.
4. Redeploy.
5. Open:

```text
https://your-app.up.railway.app/health
https://your-app.up.railway.app/config
```

## Local Run

```powershell
$env:ASSEMBLYAI_API_KEY="your_assemblyai_key"
$env:OPENAI_API_KEY="your_openai_key"
$env:OPENAI_MODEL="gpt-4.1-mini"
```

```bash
pip install -r apps/gateway/requirements.txt
uvicorn app.main:app --app-dir apps/gateway --host 0.0.0.0 --port 8080
```

Open:

```text
http://localhost:8080
```
