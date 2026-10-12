# Full HD interactive-menu model (pre-BD-J)

**Status: internal logical model only. It does NOT author BDJO, JAR, HDMV Interactive Graphics, UDF 2.50 images or playable Blu-ray menus.**

The stdlib-only `bdj_menu.py` defines an immutable menu specification and a deterministic remote-control model. It supports BD25 and BD50 only. The `MenuSpec.manifest()` returns a JSON-compatible logical manifest with chapters, audio, subtitles, bonus titles, and scene pagination. Logical indices are zero-based and are **not** BD-J or MPEG-TS track IDs.

Example:

```python
from bdj_menu import MenuController, MenuSpec

menu = MenuController(MenuSpec(chapters=('Opening', 'Credits'),
                             audio_tracks=('Russian AC-3', 'English AC-3'),
                             subtitle_tracks=('Russian PGS',), profile='BD25'))
menu.press('DOWN')     # Scene Selection
menu.press('ENTER')    # Chapters
menu.press('DOWN')     # Chapter 2
assert menu.press('ENTER').index == 1
```

UP/DOWN move focus; LEFT/RIGHT change pages in Scene Selection; ENTER selects Play, Chapter, Audio, Subtitle (including Off), or Bonus; BACK and TOP_MENU return to Home. Scene pages contain 1–12 entries. Empty optional categories are hidden.

**Required before playable authoring:** compile a real BD-J Xlet or HDMV menu, map chapter and audio/subtitle selections to valid muxed MPLS/PlayMark and stream IDs, package signed/appropriate BDJO/JAR assets, link startup navigation and perform real BD-RE, ASUS BW-16D1HT and standalone-player tests. This model does not alter disc creation, recording, DRM, DiscForge TV or DiscForge Player.

Tests: `python -m unittest discover -s tests -p 'test_bdj_menu.py' -v`.
