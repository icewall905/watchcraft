"""Deterministic narration for real research milestones.

Every tone states exactly the same facts; only the wording differs, and playful
wording never claims work, findings or outcomes that did not happen. Variant
choice derives from run-specific seeds, so a step never flaps between variants
and nothing depends on the clock or randomness.
"""
import hashlib

TONES = 'professional, friendly, playful'.split(', ')
FALLBACK = 'friendly'

# text[key][tone] -> variants; placeholders are format(**facts).
VOICE: dict[str, dict[str, list[str]]] = {
    # ---------- curator runs
    'curator.started': {
        'professional': ['Run started; checking {sources}.'],
        'friendly': ['Starting a run with {sources}.'],
        'playful': ['Off to the research fair — {sources} to check.'],
    },
    'curator.checking': {
        'professional': ['Checking {label}.'],
        'friendly': ['Checking {label}.'],
        'playful': ['Knock knock, {label}: coming in for a look.'],
    },
    'curator.source_unavailable': {
        'professional': ['{label} is unavailable.'],
        'friendly': ['{label} is unavailable.'],
        'playful': ['{label} isn\'t answering right now.'],
    },
    'curator.checked': {
        'professional': ['Received results from {label}: {got}.'],
        'friendly': ['Received results from {label}: {got}.'],
        'playful': ['{label} shared {got}, fresh from the source.'],
    },
    'curator.reused': {
        'professional': ['Reused a recent fetch from {label}: {got}.'],
        'friendly': ['Reused a recent fetch from {label}: {got}.'],
        'playful': ['{label}\'s recent fetch is still warm — reused {got}.'],
    },
    'curator.source_error': {
        'professional': ['Checked {label}: {got}, with a check error.'],
        'friendly': ['Checked {label}: {got}, but with a check error.'],
        'playful': ['{label} handed over {got}, with a check error attached.'],
    },
    'curator.reviewing': {
        'professional': ['Reviewing {got} against the mission.'],
        'friendly': ['Reviewing {got} against the mission.'],
        'playful': ['{got} on the workbench, measured against the mission.'],
    },
    'curator.matched': {
        'professional': ['Found {fresh} ready for review.'],
        'friendly': ['{fresh} ready for review.'],
        'playful': ['{fresh} made it onto the shortlist for review.'],
    },
    'curator.ranking': {
        'professional': ['Asking the configured AI to rank {fresh}.'],
        'friendly': ['Asking the configured AI to rank {fresh}.'],
        'playful': ['Getting a second opinion on {fresh}.'],
    },
    'curator.ranking_error': {
        'professional': ['AI ranking was unavailable; kept the keyword scores.'],
        'friendly': ['AI ranking was unavailable; kept the keyword scores.'],
        'playful': ['The AI judge was out; the keyword clippers did the tally.'],
    },
    'curator.completed': {
        'professional': ['Run complete. New: {new}; checked: {seen}; issues: {issues}.'],
        'friendly': ['Finished: {new}, {seen} checked, {issues}.'],
        'playful': ['Found {new} among {seen} checked, {issues}.'],
    },
    'curator.writing_report': {
        'professional': ['Writing a cited report from the research findings.'],
        'friendly': ['Writing a cited report from the research findings.'],
        'playful': ['Tying the findings into a tidy report, footnotes and all.'],
    },
    'curator.report_ready': {
        'professional': ['The research report is ready.'],
        'friendly': ['The research report is ready.'],
        'playful': ['The research report is ready.'],
    },
    'curator.report_error': {
        'professional': ['The findings were saved, but report writing failed.'],
        'friendly': ['The findings were saved, but report writing failed.'],
        'playful': ['The findings were saved, but the report took a wrong turn.'],
    },
    'curator.failed': {
        'professional': ['The run stopped because of an internal error.'],
        'friendly': ['The run stopped because of an internal error.'],
        'playful': ['The run stopped — something snapped inside the workshop.'],
    },
    # ---------- briefings
    'briefing.started': {
        'professional': ['Gathering new discoveries for {name}.'],
        'friendly': ['Gathering new discoveries for {name}.'],
        'playful': ['Gathering the newest finds for {name}.'],
    },
    'briefing.selected': {
        'professional': ['Selected {new} after topic filtering and duplicate checks.'],
        'friendly': ['Selected {new} after topic filtering and duplicate checks.'],
        'playful': ['{new} survived the topic filter and duplicate checks.'],
    },
    'briefing.empty': {
        'professional': ['Nothing new since the last briefing.'],
        'friendly': ['Nothing new since the last briefing.'],
        'playful': ['Nothing new since the last briefing — all caught up.'],
    },
    'briefing.writing': {
        'professional': ['Writing a cited briefing from {new}.'],
        'friendly': ['Writing a cited briefing from {new}.'],
        'playful': ['Drafting the briefing now from {new}.'],
    },
    'briefing.saving': {
        'professional': ['Saving the briefing and its source references.'],
        'friendly': ['Saving the briefing and its source references.'],
        'playful': ['Filing the briefing with its sources attached.'],
    },
    'briefing.saved': {
        'professional': ['Briefing saved with {refs}.'],
        'friendly': ['Briefing saved with {refs}.'],
        'playful': ['Briefing saved, with {refs} to back it up.'],
    },
    'briefing.fallback': {
        'professional': ['AI writing was unavailable; saved a plain digest instead.'],
        'friendly': ['AI writing was unavailable; saved a plain listing instead.'],
        'playful': ['No AI writer today; a plain, honest list it is.'],
    },
    'briefing.failed': {
        'professional': ['Briefing processing stopped. Check its issue history.'],
        'friendly': ['Briefing processing stopped unexpectedly. Check its issue history.'],
        'playful': ['Something knocked this briefing over mid-write; the history has the details.'],
    },
    # ---------- Source Scout
    'scout.queued': {
        'professional': ['Waiting for a free agent slot.'],
        'friendly': ['Waiting for a free agent slot.'],
        'playful': ['Waiting for a free agent slot.'],
    },
    'scout.searching': {
        'professional': ['Searching for relevant publishers.'],
        'friendly': ['Searching for relevant publishers.'],
        'playful': ['Hunting for promising new haunts.'],
    },
    'scout.found': {
        'professional': ['Identified {sites} for further inspection.'],
        'friendly': ['Found {sites} to look at.'],
        'playful': ['Promising leads on the trail: {sites}.'],
    },
    'scout.inspected': {
        'professional': ['Inspected a public site.'],
        'friendly': ['Inspected a public site.'],
        'playful': ['Poked around one more corner of the internet.'],
    },
    'scout.validated_feed': {
        'professional': ['Validated a feed with {sample}.'],
        'friendly': ['Validated a feed with {sample}.'],
        'playful': ['Excellent: a feed with {sample} checks out.'],
    },
    'scout.rejected_feed': {
        'professional': ['A candidate feed could not be validated.'],
        'friendly': ['A candidate feed could not be validated.'],
        'playful': ['One feed didn\'t hold up; moving on.'],
    },
    'scout.validated_listing': {
        'professional': ['Tested an article listing with {sample}.'],
        'friendly': ['Tested an article listing with {sample}.'],
        'playful': ['The listing page gave up {sample} to track.'],
    },
    'scout.validated_sitemap': {
        'professional': ['Tested a sitemap with {urls}.'],
        'friendly': ['Tested a sitemap with {urls}.'],
        'playful': ['Cracked a sitemap: {urls} mapped out.'],
    },
    'scout.rejected_sitemap': {
        'professional': ['A candidate sitemap could not be validated.'],
        'friendly': ['A candidate sitemap could not be validated.'],
        'playful': ['That sitemap led somewhere odd; skipped it.'],
    },
    'scout.validated_page': {
        'professional': ['Extracted readable page text for change monitoring.'],
        'friendly': ['Extracted readable page text for change monitoring.'],
        'playful': ['Learned the page by heart to spot future changes.'],
    },
    'scout.failed': {
        'professional': ['Could not inspect a candidate site.'],
        'friendly': ['Could not inspect a candidate site.'],
        'playful': ['That one hid behind a locked door; next candidate.'],
    },
    'scout.complete': {
        'professional': ['Search complete: {found}.'],
        'friendly': ['Found {found}.'],
        'playful': ['{found} came home, checked and ready.'],
    },
    'scout.sitemaps': {
        'professional': ['Found {links}; validated sitemaps appear in the suggestions.'],
        'friendly': ['Found {links}; validated sitemaps appear in the suggestions.'],
        'playful': ['Followed {links}; the checked ones show up below.'],
    },
}


def phrase(count: int, noun: str, plural: str | None = None) -> str:
    """'2 recommendations' / '1 recommendation' — count-attached phrase fact."""
    return f'{count} {noun if count == 1 else plural or (noun + "s")}'


def profile_tone(profile) -> str:
    """Profile-level narration voice for hub agents that are not curators."""
    prefs = getattr(profile, 'prefs', None) or {}
    value = prefs.get('hub_tone', FALLBACK)
    return value in TONES and value or FALLBACK


def say(tone: str, key: str, seed: str = '', **facts) -> str:
    """Stable variant for (tone, key, seed); unknown tones fall back to friendly."""
    entry = VOICE.get(key)
    if entry is None:
        raise KeyError(key)
    variants = entry[tone] if tone in TONES and tone in entry else entry[FALLBACK]
    choice = int(hashlib.sha1(f'{seed}:{key}'.encode()).hexdigest(), 16) % len(variants)
    return variants[choice].format(**facts)
