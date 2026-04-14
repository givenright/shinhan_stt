# shinhan_stt

Linux GPU Nemotron speech service with a URL ingest gateway and browser UI for English live speech transcription and Korean translation.

## Required model

Put the model here:

`artifacts/models/nvidia/nemotron-speech-streaming-en-0.6b/nemotron-speech-streaming-en-0.6b.nemo`

## Main scripts

Build images:

`bash scripts/build.sh`

Run services:

`bash scripts/run.sh`

Stop services:

`bash scripts/stop.sh`

See logs:

`bash scripts/logs.sh`

Gateway logs only:

`bash scripts/logs.sh gateway`

Nemotron logs only:

`bash scripts/logs.sh nemotron-asr`

## Offline prep

Download model:

`python scripts/download_nemotron_model.py`

Prefetch gateway wheels:

`python scripts/prefetch_gateway_wheels.py`

Prefetch nemotron wheels:

`python scripts/prefetch_nemotron_wheels.py`

Export NeMo base image:

`bash scripts/export_nemo_base_image.sh`

Save built images:

`bash scripts/save_internal_images.sh`

Load saved images:

`bash scripts/load_internal_images.sh`

## Linux run

1. Put the `.nemo` file in the required model path.
2. Edit `infra/gateway.env.example` and `infra/nemotron.env.example`.
3. Build or load images.
4. Start with `bash scripts/run.sh`
5. Open `http://<linux-host>:8080/`
