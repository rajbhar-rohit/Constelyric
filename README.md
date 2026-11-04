<p align="center">
  <img src="assets/logo.png" width="112" alt="Constelyric logo: a glowing four-point star">
</p>

<h1 align="center">Constelyric</h1>
<p align="center">Word-by-word lyrics that bloom into constellations on your Windows screen.</p>

---

Constelyric shows the lyrics of whatever is playing (Spotify, Apple Music, YouTube in a browser, VLC...) with each word lighting up as it is sung.

**Three styles**
- **Stack** - three lines that glide up as the song moves on, with a karaoke wipe across each word.
- **Focus** - one line at a time; upcoming words float up into place.
- **Constellation** - fullscreen: every word becomes a star, linked into a growing constellation, inside a soft ambient glow that drifts around the screen edge.

## Website

The download page lives in `docs/index.html`. Edit the `REPO` line near the bottom to your `username/repo`, then enable
**Settings -> Pages -> Deploy from a branch -> `main` / `/docs`** on GitHub. The Download button then always links to your latest release.

## Install

1. Open the [**Releases**](../../releases/latest) page.
2. Download **Constelyric-Setup-x.y.z.exe** and run it. No Python and no admin rights needed.
3. If Windows shows *"Windows protected your PC"*: click **More info -> Run anyway**.
   (New, unsigned apps trigger this. Code signing is on the to-do list.)
4. Play a song, then click the star icon in the system tray (near the clock, maybe under the `^`).

Prefer no installer? Download the **portable zip**, unzip it, and run `Constelyric.exe`.
To uninstall: Windows Settings -> Apps -> Constelyric (or delete the portable folder).

## Using it

- **Tray icon:** click to open the panel, right-click to quit.
- **Display:** Stack, Focus or Constellation (fullscreen - press **Esc** to leave).
- **Options:** show/hide lyrics, colours from the album art, keep the display awake, border glow, check for updates.
- Windows doesn't let apps draw over the lock screen, so lyrics appear on the desktop.

## Updates

Constelyric checks GitHub Releases shortly after launch and every 6 hours. When a newer version exists a pop-up offers
**Download**, **Later** or **Skip version**. You can also click **Check for updates** in the panel or tray menu.
Release builds stamp their own version and repo automatically (`tools/set_version.py`); if you build by hand, set
`GITHUB_REPO` near the top of `constelyric.py`.

## Lyrics sources

Constelyric tries your enabled sources **in order** and uses the first one that has *word-by-word* timing
(otherwise the first line-by-line result). Open **Lyrics sources -> Choose...** in the panel to switch sources
on or off and reorder them. The panel also shows which service the current lyrics came from.

| Source | Timing | Notes |
|---|---|---|
| [lrc.red](https://lrc.red) | word | community database |
| BiniLyrics (`lyrics-api.binimum.org`) | word | Apple-style TTML |
| LyricsPlus (`lyricsplus.binimum.org` + mirrors) | word | several mirrors, tried in turn |
| KuGou | word (KRC) / line | strong for Chinese and other Asian songs |
| [BetterLyrics](https://github.com/better-lyrics/api) | word | a small community server - please be gentle |
| Musixmatch | word / line | **official API, bring your own key**; synced lyrics usually need a paid plan |
| [LRCLIB](https://lrclib.net) | line | open database |

**Privacy:** the song title, artist and length are sent to each *enabled* service to look the lyrics up. Nothing else
is sent and nothing is written to disk (lyrics are only cached in memory). Turn sources off if you prefer.

**Not included, on purpose:** the *Paxsenix Spotify-Lyrics-API* is a self-hosted project that needs your personal
Spotify login cookie and a Spotify track ID, which a shared desktop app can't provide; *Genius* has no lyrics in its
official API and its lyrics are not time-synced.

These are unofficial community services, so they can change or disappear. The app skips a source for 10 minutes after
three failures in a row. Lyrics belong to their rights holders.

## Troubleshooting

| Problem | Fix |
|---|---|
| No lyrics for a song | Try enabling more sources (panel -> Lyrics sources). Coverage differs by language. You can also contribute lyrics to [LRCLIB](https://lrclib.net). |
| Nothing happens after launch | Look for the star icon under the `^` next to the clock. |
| Song not detected | Check that your player appears in the Windows media controls (volume flyout). |

## Build from source

```
git clone https://github.com/<you>/constelyric
cd constelyric
build.bat          # Python 3.10-3.12; add Inno Setup to also build the installer
```

Or run directly: `pip install -r requirements.txt` then `python constelyric.py`.

**Releasing:** `git tag v1.0.0 && git push --tags`. GitHub Actions builds the installer and the portable zip
and attaches both to a new Release (`.github/workflows/release.yml`).

## Contributing

Issues and pull requests are welcome - especially new display styles, translations, and better word-timing sources.

## Credits & legal

- Lyrics come from the community services listed above. They belong to their rights holders;
  Constelyric doesn't store or redistribute them.
- Built with Qt (PyQt6) and the Windows media-session API.
- Not affiliated with any other lyrics app.
- Licensed under the **GNU GPL v3** (see [LICENSE](LICENSE)); PyQt6 is GPL-licensed.
