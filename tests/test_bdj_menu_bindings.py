"""Synthetic checks: never proof of playable BD-J or on-disc stream mapping."""
import json
import unittest

from bdj_menu import MenuAction, MenuController, MenuSpec
from bdj_menu_bindings import MenuBindings, PlaylistMark


class MenuBindingTests(unittest.TestCase):
    def setUp(self):
        self.spec = MenuSpec(
            chapters=('Opening', 'Finale'),
            audio_tracks=('Russian AC-3', 'English AC-3'),
            subtitle_tracks=('Russian PGS',),
            bonus_titles=('Extra',), profile='BD25')
        self.bindings = MenuBindings(
            self.spec,
            chapters=(PlaylistMark('00001', 0), PlaylistMark('00001', 3)),
            audio_pids=(0x1100, 0x1101),
            subtitle_pids=(0x1200,),
            bonus_playlists=('00002',))

    def test_chapter_and_bonus_targets(self):
        self.assertEqual(self.bindings.resolve(MenuAction('play', 1)),
                         {'operation': 'jump_chapter', 'playlist_id': '00001', 'mark_index': 3})
        self.assertEqual(self.bindings.resolve(MenuAction('bonus', 0)),
                         {'operation': 'jump_bonus', 'playlist_id': '00002'})

    def test_tracks_and_off(self):
        self.assertEqual(self.bindings.resolve(MenuAction('audio', 1)),
                         {'operation': 'select_audio', 'pid': 0x1101})
        self.assertEqual(self.bindings.resolve(MenuAction('subtitle', 0)),
                         {'operation': 'select_subtitles', 'pid': 0x1200})
        self.assertEqual(self.bindings.resolve(MenuAction('subtitle', -1)),
                         {'operation': 'disable_subtitles'})

    def test_manifest_deterministic_and_json_compatible(self):
        first = self.bindings.manifest()
        self.assertEqual(first, self.bindings.manifest())
        self.assertEqual(json.loads(json.dumps(first, ensure_ascii=False)), first)
        self.assertEqual(first['target_verification'], 'unverified')
        self.assertEqual(first['chapters'][1]['mark_index'], 3)
        self.assertEqual(first['subtitle_tracks'][0]['pid'], 0x1200)

    def test_controller_actions_can_be_resolved(self):
        menu = MenuController(self.spec)
        self.assertEqual(self.bindings.resolve(menu.press('ENTER'))['playlist_id'], '00001')
        menu.press('DOWN')
        menu.press('ENTER')      # scenes
        menu.press('DOWN')
        self.assertEqual(self.bindings.resolve(menu.press('ENTER'))['mark_index'], 3)

    def test_invalid_playlist_names(self):
        for bad in ('0001', '００００１', '123456', '../00', '00000.mpls', '', None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                PlaylistMark(bad, 0)
        with self.assertRaises(ValueError):
            MenuBindings(self.spec, self.bindings.chapters,
                         self.bindings.audio_pids, self.bindings.subtitle_pids, ('xyz',))

    def test_invalid_mark_index(self):
        for bad in (-1, 65536, 0.0, True, '0', None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                PlaylistMark('00000', bad)

    def test_invalid_pids(self):
        for bad in (-1, 0x1F, 0x1FFF, 0x2000, True, 0.0, '0x1100'):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                MenuBindings(self.spec, self.bindings.chapters,
                             (bad, 0x1101), self.bindings.subtitle_pids, self.bindings.bonus_playlists)

    def test_reject_duplicate_targets(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            MenuBindings(self.spec, (PlaylistMark('00000', 0),) * 2,
                         self.bindings.audio_pids, self.bindings.subtitle_pids, self.bindings.bonus_playlists)
        with self.assertRaisesRegex(ValueError, 'unique'):
            MenuBindings(self.spec, self.bindings.chapters,
                         (0x1100, 0x1100), self.bindings.subtitle_pids, self.bindings.bonus_playlists)
        with self.assertRaisesRegex(ValueError, 'unique'):
            MenuBindings(self.spec, self.bindings.chapters,
                         (0x1200, 0x1101), self.bindings.subtitle_pids, self.bindings.bonus_playlists)

    def test_reject_mismatched_cardinality_and_types(self):
        for field, value in [('chapters', ()), ('audio_pids', (0x1100,)),
                             ('subtitle_pids', []), ('bonus_playlists', ())]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                fields = dict(chapters=self.bindings.chapters,
                              audio_pids=self.bindings.audio_pids,
                              subtitle_pids=self.bindings.subtitle_pids,
                              bonus_playlists=self.bindings.bonus_playlists)
                fields[field] = value
                MenuBindings(self.spec, **fields)
        with self.assertRaises(ValueError):
            MenuBindings(self.spec, ('00001', PlaylistMark('00001', 3)),
                         self.bindings.audio_pids, self.bindings.subtitle_pids, self.bindings.bonus_playlists)

    def test_unavailable_actions_and_invalid_indices(self):
        for action in (MenuAction('play', -1), MenuAction('play', 2),
                       MenuAction('audio', 2), MenuAction('subtitle', -2),
                       MenuAction('bonus', 1), MenuAction('play', True),
                       MenuAction('play', '1'), MenuAction('unknown', 0)):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.bindings.resolve(action)
        with self.assertRaises(ValueError):
            self.bindings.resolve(None)

    def test_unavailable_optional_sections_do_not_resolve(self):
        spec = MenuSpec(chapters=('Only',))
        target = MenuBindings(spec, (PlaylistMark('00000', 0),))
        self.assertEqual(target.resolve(MenuAction('play', 0))['mark_index'], 0)
        for kind, index in (('audio', 0), ('subtitle', -1), ('subtitle', 0), ('bonus', 0)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                target.resolve(MenuAction(kind, index))

    def test_immutable(self):
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            self.bindings.audio_pids = (1,)
        with self.assertRaises(FrozenInstanceError):
            self.bindings.chapters[0].mark_index = 1


if __name__ == '__main__':
    unittest.main()
