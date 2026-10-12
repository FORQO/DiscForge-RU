# Full HD BD-J navigation target map (prototype, not playable)

`bdj_menu_bindings.py` is a **read-only planning adapter** for the logical
`bdj_menu.py` navigation model. It does not modify an existing BDMV, build a
BDJO/JAR or HDMV menu, encode tracks, write ISO images, or operate a drive.

The authoring pipeline currently muxes only the first audio track and does **not**
export interactive menus. Therefore the example IDs below are **illustrative
only**, and should never be inferred from `ffprobe` track order or surfaced
as confirmed until checked against a real muxed BDMV.

```python
from bdj_menu import MenuAction, MenuSpec
from bdj_menu_bindings import MenuBindings, PlaylistMark

spec = MenuSpec(chapters=('Start', 'Scene 2'), audio_tracks=('AC-3',))
plan = MenuBindings(spec,
    chapters=(PlaylistMark('00000', 0), PlaylistMark('00000', 1)),
    audio_pids=(0x1100,))
assert plan.resolve(MenuAction('play', 1)) == {
    'operation': 'jump_chapter', 'playlist_id': '00000', 'mark_index': 1}
assert plan.manifest()['target_verification'] == 'unverified'
```

Fail-closed input checks enforce five **ASCII** digits for playlist IDs,
nonnegative 16-bit PlayMark indices, non-reserved MPEG-TS PID bounds and
one-to-one cardinality between rendered choices and targets. Invalid/absent
choices never produce a command. Subtitle **Off** is a distinct operation,
not a fake PID.

These are shape checks **only**: they do not prove that referenced MPLS files,
PlayMarks, bonus playlists or track PIDs exist, that PIDs carry the correct
codec (e.g. AC-3 or PGS), or that a Blu-ray player can execute the command.
A subsequent stage must validate all references against muxed BDMV/CLPI/STN,
compile an Xlet or HDMV interactive graphics, bind startup navigation, validate
an actual UDF 2.50 image and physically test BD-RE with ASUS BW-16D1HT and a
standalone player. Only Full HD BD25/BD50 is in scope; UHD/BDXL is deferred.

Run the synthetic tests with:

```sh
python -m unittest discover -s tests -p 'test_bdj_menu_bindings.py' -v
```
