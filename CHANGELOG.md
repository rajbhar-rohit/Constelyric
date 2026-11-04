# Changelog

## Unreleased

- **More lyrics sources:** lrc.red, BiniLyrics, LyricsPlus, KuGou (word-by-word KRC), BetterLyrics and Musixmatch
  (your own API key) join LRCLIB. A new *Lyrics sources* window lets you switch them on/off and reorder them; the
  panel shows which service was used. Word-by-word results are preferred over line-by-line ones.
- **Smoother constellation:** words come into focus (blur-in) as they appear, the old sky melts away instead of
  disappearing, stars drift gently, and the current word gets a soft out-of-focus halo.
- Tests for the lyrics sources (`python -m unittest tests.test_sources`).

## v1.0.0-bug-fixes

- **Updates:** Constelyric now checks GitHub Releases at launch and every 6 hours and shows a pop-up
  (Download / Later / Skip version) when a newer release exists. There is also a *Check for updates*
  link in the panel and the tray menu.
- **Fixed:** labelled releases such as `1.0.0-bug-fixes` are now recognised as newer than a plain `1.0.0`,
  so people on the first release are told about this one.
- **Fixed:** the installer now closes the running tray app before replacing it, so updating over an open
  copy works.
- **Website:** download page in `docs/` (GitHub Pages) with a glass design and a "star the repo" pop-up.

## v1.0.0

- First public release: Stack, Focus and Constellation styles, ambient border glow, album-art colours.
