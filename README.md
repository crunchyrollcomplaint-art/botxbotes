# Telegram Video Compressor Bot

This repository contains a separate public Telegram bot that accepts videos sent in a private chat, downloads them through Telegram's Local Bot API Server, compresses them with FFmpeg, and sends the resulting MP4 back to the same Telegram user. Any Telegram user can use it; there is no Telegram login, OTP flow, Google Drive connection, channel, MongoDB database, SQLite database, or persistent volume.

## How the workflow works

A user sends a video either as a Telegram video or as a document. The bot temporarily stores the file under `/tmp`, reads its duration and resolution with FFprobe, and shows resolution choices for **360p, 480p, 720p, 1080p, or original resolution**. After the user chooses a resolution, the bot displays an estimated output size. The user may either use that quality-based estimate or choose **Custom target size (MB)** and enter a size. Preset 200 MB, 500 MB, and 1 GB buttons are intentionally not shown. FFmpeg then encodes H.264/AAC MP4 and the bot returns it as a document.

The application does not set an upload-size limit when `MAX_INPUT_BYTES=0`. The Local Bot API Server is used in `--local` mode to avoid the normal cloud Bot API file-download restriction. This does **not** mean a Render free instance has unlimited RAM, disk, CPU time, or bandwidth. A 1 GB or larger Render instance is strongly recommended for large videos; the free 512 MB tier can run out of memory during large FFmpeg jobs.

## Repository layout

| Path | Purpose |
|---|---|
| `main.py` | Starts Local Bot API, polling, compression workers, and the health server. |
| `services/local_bot_api.py` | Starts the official Telegram Local Bot API binary inside the same container. |
| `services/ffmpeg_service.py` | FFprobe metadata, size estimates, quality-based encoding, and target-size two-pass encoding. |
| `services/compression_queue.py` | In-memory queue, per-user ordering, status messages, result delivery, and cleanup. |
| `services/memory_store.py` | Per-user pending selections and queue state. |
| `bot/handlers/` | Telegram commands, file intake, buttons, and custom target-size input. |
| `web/app.py` | Render health endpoint at `/health`. |
| `Dockerfile` | Compiles Local Bot API and installs FFmpeg/FFprobe. |
| `render.yaml` | Render Blueprint configuration. |

## Before deployment

Create a **new bot** in Telegram using `@BotFather` and copy its token. Keep the token private. This compressor bot should not reuse the token of the Google Drive bot because only one polling service should own a token at a time.

You can reuse the same Telegram API ID and API hash that were used for the existing Local Bot API deployment. They are Telegram developer credentials, not the bot token. The Local Bot API server needs them to start.

## Render deployment: simple steps

Create a new private GitHub repository and upload the complete contents of this folder. The `Dockerfile` must be in the repository root, next to `main.py` and `render.yaml`. Do not put the files inside an additional nested folder unless you also change Render's Docker context and Dockerfile path.

In Render, create a new **Web Service** from this GitHub repository. Select **Docker** as the runtime. The Dockerfile path is `./Dockerfile`, the Docker context is `.`, and the health check path is `/health`. The service must have one instance because Telegram polling should not be duplicated.

Add the following environment variables in Render. The values marked as private come from your own accounts.

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The new token from `@BotFather`. |
| `TELEGRAM_API_ID` | Your Telegram API ID, for example `30381557`. |
| `TELEGRAM_API_HASH` | Your Telegram API hash. |
| `LOCAL_BOT_API` | `true` |
| `TELEGRAM_API_BASE_URL` | `http://127.0.0.1:8081` |
| `LOCAL_BOT_API_PORT` | `8081` |
| `UPDATE_MODE` | `polling` |
| `TEMP_DIRECTORY` | `/tmp/telegram-video-compressor` |
| `MAX_INPUT_BYTES` | `0` |
| `MAX_OUTPUT_BYTES` | `0` |
| `PORT` | `10000` |

The remaining variables are already present in `render.yaml` and can normally be left unchanged. `MAX_INPUT_BYTES=0` and `MAX_OUTPUT_BYTES=0` mean that this application does not add a byte-size limit. You can set a positive value later if you want a safety cap.

Deploy the service and wait for the build to complete. Open the Render service URL followed by `/health`. A successful response looks like this:

```json
{"status":"ok","service":"telegram-video-compressor"}
```

The Telegram bot itself does not need a public domain. Render's public URL is only used for the health check; the bot receives updates through polling. There is no Google OAuth redirect URL for this compressor bot.

## First use

Open the new bot in Telegram and press Start. Send a video. Choose a resolution, review the estimated output size, optionally choose **Custom target size (MB)**, and press Start compression. The status message shows `Compression: XX%` and then `Uploading to Telegram: XX%`. Use `/status` to see whether the job is queued or processing, and `/cancel` to remove a pending video before it starts.

## Important operational notes

All sessions and pending selections are in memory. A Render restart clears them. Uploaded and compressed files are temporary and are deleted after the job finishes or fails. The bot does not retain a media library.

The queue defaults to one FFmpeg job at a time, which is intentional because simultaneous large encodes can exhaust memory. `MAX_CONCURRENT_JOBS=1` can be increased only after moving to a sufficiently large Render plan and testing real files. The bot edits one status message with compression percentage, followed by upload percentage while streaming the compressed file without loading the whole output into RAM.

The quality-based mode uses H.264 CRF 23 with AAC audio and avoids upscaling small videos. Target-size mode uses a two-pass H.264 encode to spend a predictable bitrate budget. Estimates are approximate because motion complexity, audio streams, subtitles, and container overhead vary. A very small requested target may be rejected when the duration cannot be represented at a reasonable bitrate.

## Local syntax checks

The project can be checked without Telegram credentials:

```bash
python -m compileall .
pytest -q
```

A complete live test requires the real environment variables and an FFmpeg installation. Do not commit a real `.env` file or a bot token to GitHub.

## Troubleshooting

If the log says that the Local Bot API binary is missing, the Docker build did not complete its first stage; redeploy the service and inspect the Docker build log. If Telegram requests time out, confirm that the two API URL variables are exactly `LOCAL_BOT_API=true` and `TELEGRAM_API_BASE_URL=http://127.0.0.1:8081`. If large jobs stop with an out-of-memory message, move the Render service to a 1 GB or larger plan and keep `MAX_CONCURRENT_JOBS=1`. If a video is rejected by FFprobe, send it again as a document or convert it to a normal video container such as MP4.
