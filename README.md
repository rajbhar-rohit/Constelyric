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

## Emoji in the constellation

Words with a known meaning get an animated emoji above their star: *aankhein* / *ojos* / *yeux* / आँखें blink as 👀,
*dil* / *corazón* / *Herz* beat as ❤️, *aag* / *fuego* / *feu* flicker as 🔥, *chand* / *luna* / *Mond* float as 🌙, rain drips, tears fall,
stars twinkle... Around 30 ideas and 880 words across English, Hindi/Urdu/Punjabi (romanised and Devanagari),
Spanish, French, Portuguese, German, Italian, Turkish, Russian, Arabic, Korean, Japanese and Chinese.

**Add your own words** - either edit `emoji_map.py` (and send a pull request!) or create
`%APPDATA%\Constelyric\emoji.json`:

```json
{ "tumhare": "🫶", "samundar": "🌊", "yaadein": "💭" }
```

## Updates

Constelyric checks GitHub Releases shortly after launch and every 6 hours. When a newer version exists a pop-up offers
**Download**, **Later** or **Skip version**. You can also click **Check for updates** in the panel or tray menu.
Release builds stamp their own version and repo automatically (`tools/set_version.py`); if you build by hand, set
`GITHUB_REPO` near the top of `constelyric.py`.

## Troubleshooting

| Problem | Fix |
|---|---|
| No lyrics for a song | It may not be in [LRCLIB](https://lrclib.net) yet - coverage is thinner for some languages. You can contribute lyrics there. |
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

- Lyrics come from [LRCLIB](https://lrclib.net), a community database. They belong to their rights holders;
  Constelyric doesn't store or redistribute them.
- Built with Qt (PyQt6) and the Windows media-session API.
- Not affiliated with any other lyrics app.
- Licensed under the **GNU GPL v3** (see [LICENSE](LICENSE)); PyQt6 is GPL-licensed.
