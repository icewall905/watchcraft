# Play on another screen

On the media center, open BetterTube in its browser/PWA, choose **Play on** in
the top bar, enter **Media center**, and press **Register this screen**. This
registration is saved in that browser, so it reconnects after reloads. Keep the
app open. The projector's `bt-mode` Chromium launcher already permits autoplay;
other browsers may need sound/autoplay permission for this site.

On your phone, open the same BetterTube server, choose **Play on → Media center**,
and tap any video card or Play link. Playback starts on the selected screen;
the phone stays on its browsing page. Play on also has Pause, Resume and Stop.
Choose **This device** to return to local playback. Target selection lasts for
the current phone app session. Sending uses the phone's active family profile
and playlist, including watch history, playback preferences and autoplay-next.

The receiver can use `http://localhost:6645` while the phone uses
`https://bt.tdgs.org`; both connect to the same server. No Chromecast hardware,
YouTube application, or browser-to-browser network access is needed. This cannot
wake a closed browser or a sleeping computer. A backgrounded mobile receiver can
be suspended by its operating system; the media center should keep its PWA open.

## Operation

Receivers poll a lightweight authenticated mailbox every two seconds and report
playback status. Targets become offline after 20 seconds without a heartbeat.
Only the latest command is kept; commands expire after 30 seconds. Command IDs
are acknowledged and saved locally to avoid replay after a receiver reload.
Receiver credentials are random and stored locally; only their hashes are stored
in the database. Public discovery never exposes them. The existing BetterTube
login protects these endpoints. Anyone who can access this BetterTube instance
can discover registered targets, across family profiles, but only a paired
profile can send commands. Only the registered browser can heartbeat or
unregister its own target.

The additive `PlaybackTarget` table is created on API startup. No existing data
migration is required. Registration is separate from profiles so switching family
members does not lose the receiver.

## Managing screens

**Settings → Screens** (administrators) lists every registered screen with its
online state and the profiles allowed to send to it:

- **New pairing code** replaces the code the screen shows. The screen picks the
  new code up on its next heartbeat, so anyone who saw the old one can no longer
  pair. Existing pairings are kept; the wrong-code lockout is cleared.
- **Remove screen** unregisters it from the sender side. The screen's next
  heartbeat is refused and it must register again. The receiver can still
  unregister itself from Play on.
- Each paired profile has an **unpair** button that revokes only that profile.

A sender can also drop its own pairing with **Forget this screen** in the Play on
picker. Deleting a profile removes its pairings and any command it left in a
screen's mailbox, so a reused profile id cannot inherit casting rights.

Administrators see `paired_profiles` in `GET /api/playback/targets`; other
profiles do not. The pairing code itself is never returned to a sender — only the
registered receiver reads it from its heartbeat.

## Validation

Backend coverage checks registration, receiver credentials, command validation,
profile/playlist ownership, acknowledgement, expiry, offline rejection and status,
plus administrator removal, per-profile revocation, code rotation and the pairing
cleanup when a profile is deleted. The two-browser Playwright test runs a phone
viewport and a separate receiver, reloads the registered receiver, sends a video
by tapping a card, verifies actual MP4 playback and repeat casting, exercises
pause/resume/stop without navigating the phone, and forgets the screen from the
phone. A second test drives Settings → Screens: revoking a profile, rotating the
code and removing the screen. Both use generated local media and make no YouTube
requests.
