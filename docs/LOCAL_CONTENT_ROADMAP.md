# Local content and summaries

WatchCraft is intended to keep relevant material on the host, present a concise agent summary first, and retain the source URL as provenance.

The local library now uses persistent profile-owned capture jobs. Newly discovered items are queued; older visible discoveries and saved items are backfilled automatically. A single worker claims jobs with an expiring lease and retries failures with backoff. Agent Activity shows queue counts and has administrator controls to pause capture, retry failures, and set the shared image/document storage limit (default 512 MB). The video downloader continues to use its own configured storage cap. Pausing prevents new work from starting; a running job can finish.

Public HTTPS HTML is extracted into searchable text with paragraph breaks; known links to other visible local items are offered in WatchCraft. One representative image and PDFs are retained in the database under a bounded quota and served only after profile access checks. PDFs are text-extracted with Poppler for local reading/search and summarization. YouTube videos with valid IDs enter the existing pinned offline-download queue and use a previously stored transcript if available. The item view shows capture, summary and video-download status; an unavailable fetch never silently counts as a successful local copy. Captures retain source URLs as provenance and do not render untrusted source HTML.

When Hub AI is enabled, the worker summarizes captured text in bounded chunks and combines their summaries. It retries when AI fails, and retries summary work after AI becomes available. Summaries are labelled as AI-generated and source descriptions remain separate. A report prefers available AI summaries.

Limits and remaining work:

- HTML capture stores extracted text, a representative image, and links to already visible local items; it does not reproduce source layouts, all embedded images or interactive applications. JavaScript-only or access-restricted pages may fail or be partial.
- HTML bodies and PDF text are bounded to 200,000 characters; PDF extraction is bounded to the first 100 pages. The item view flags text that reaches those limits. Binary PDFs are limited to 20 MB and images to 5 MB.
- PDF text extraction does not perform OCR. Video summarization requires a transcript already stored by the video agent; capture does not independently obtain captions. A queued download is not necessarily already playable offline.
- Capture cannot guarantee that an entire website, paid material, or unavailable source can be reproduced locally. Per-item failure and retry status should be checked. Local asset storage has a quota, and pinned video downloads stop when the existing download cap is full.
- Future work: multiple inline media assets and layout-aware reading, durable caption acquisition and OCR, more document formats, richer capture management and deletion/retention policies, automatic discovery of linked relevant pages, disconnected browser acceptance on physical TV hardware, and improved support for dynamic sites.
