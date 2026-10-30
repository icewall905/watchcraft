# Voice search

The microphone button is available beside video search in TV mode, in the video-search input, in Find, and in the desktop search dialog. Select it with the remote or click it, allow microphone access when the browser asks, and say a short search phrase. A short pause finishes the recording and the recognized text performs the appropriate search. Done finishes manually; Back or Cancel discards the recording. Recordings stop after 20 seconds. The browser uses its selected/default microphone, including a remote microphone exposed by the operating system.

Microphone capture requires a secure browser context: use WatchCraft's HTTPS address. Browser permission failures, a missing microphone, empty speech, and an unavailable STT service have explicit retry messages. Audio is only captured after activating the button. Capture stops on cancellation, navigation, profile change, transcription completion, or component teardown. Clips are not retained by WatchCraft.

## Service configuration

The inspected production service is `/root/compose/stt-qwen-wyoming/docker-compose.yml` on `10.0.10.73`. Its `asr-proxy` listens on published port **8011** and forwards the OpenAI-compatible API to the Qwen3-ASR vLLM service. The model currently advertises `Qwen/Qwen3-ASR-1.7B`.

The WatchCraft Compose override supplies these defaults to its API container:

- `WC_STT_BASE_URL=http://10.0.10.73:8011/v1`
- `WC_STT_MODEL=Qwen/Qwen3-ASR-1.7B`

Optional `WC_STT_API_KEY` and `WC_STT_LANGUAGE` can be supplied through the existing environment file. An empty language uses provider detection. Generic installs need a base URL configured to enable voice search. Browser recordings go to WatchCraft, which calls the private STT service; the browser does not contact port 8011 directly.

`POST /api/speech/transcribe` accepts a raw audio body and requires the normal authenticated profile/PIN session. It accepts the audio formats used by browser recorders, limits uploads to 3 MB and 30 seconds of decoded audio, and allows two concurrent requests. FFmpeg decodes to mono 16 kHz WAV with a processing deadline and network protocols disabled. Empty/silent clips are rejected before inference. Provider timeouts and errors are mapped to messages that do not expose internal configuration. `GET /api/speech/status` reports whether a base URL is configured.

## Verification

Backend coverage includes real WebM decoding, normalization, silence/duration/size limits, profile PIN restrictions, request concurrency, and upstream failures. Browser checks use a fake microphone to exercise recording, silence detection, remote activation, cancellation, query updates, and error handling. A separate disposable container tested the new client against the real port-8011 service with synthetic speech and received “Show me woodworking videos.” Production WatchCraft and STT were not restarted for that test. Physical remote microphone selection still needs checking in its browser after deployment.
