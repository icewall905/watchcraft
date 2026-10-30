# Local content and summaries

WatchCraft's goal is to keep relevant material on the host so it can be consumed in WatchCraft, with concise agent summaries and source URLs retained for provenance.

The first increment captures readable text from matching HTML discoveries, stores it in the existing content database, indexes it for search, and shows it on the item page. An existing item can be captured from its item page. When Hub AI is enabled for a curator, captured text is summarized separately from source metadata; failed retrieval or summarization does not discard the discovery. Scout Reports prefer an available AI summary. Retrieval uses the existing public HTTPS validation, response type and size limits. Text extraction currently has a 200,000-character limit and does not retain layout or embedded assets. Summary input is capped at 12,000 characters per item. AI may be disabled or unavailable; such items retain source summaries when present.

Still required to satisfy the overall goal:

- Durable, resumable background queues with per-item status, retries, rate limits, and an explicit backfill of existing relevant items; monitor storage and provide retention controls.
- Capture and locally serve images, documents and video with profile authorization, respecting existing video downloads and media paths; include appropriate size limits and deduplication.
- Preserve useful article structure and links, map captured links to local items, and handle multi-page and JavaScript-rendered content where possible; record inaccessible, partial and truncated captures honestly.
- Summarize all eligible existing and new content in bounded background jobs, track failure/retry and summary provenance, and ground long-content summaries in the whole captured text.
- Test on real sources, including disconnected browsing, TV navigation, media playback, child/profile isolation, host storage limits and recovery after restart.
