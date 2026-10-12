"""Synthetic regression tests for Full HD logical menu navigation."""
import json
import unittest
from bdj_menu import MenuAction, MenuController, MenuSpec

class MenuSpecTests(unittest.TestCase):
    def test_serializable_manifest(self):
        spec = MenuSpec(chapters=('Начало', 'Финал'), audio_tracks=('Русский',), profile='BD50')
        manifest = spec.manifest()
        self.assertEqual(json.loads(json.dumps(manifest, ensure_ascii=False)), manifest)
        self.assertEqual(manifest['chapters'][1]['index'], 1)

    def test_reject_out_of_scope_profiles(self):
        for profile in ('UHD', 'BDXL100', 'DVD'):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                MenuSpec(chapters=('Scene',), profile=profile)

    def test_reject_invalid_label_page_size(self):
        for label in ('', 'a' * 81, 'bad\nlabel'):
            with self.assertRaises(ValueError):
                MenuSpec(chapters=(label,))
        for n in (0, 13, '6', True):
            with self.assertRaises(ValueError):
                MenuSpec(chapters=('Scene',), scenes_per_page=n)

class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.menu = MenuController(MenuSpec(
            chapters=tuple('Chapter %s' % x for x in range(1, 14)),
            audio_tracks=('Russian', 'English'), subtitle_tracks=('Russian', 'English'),
            bonus_titles=('Making of',), scenes_per_page=5))

    def open(self, section):
        while self.menu.entries[self.menu.focus] != section:
            self.menu.press('DOWN')
        self.menu.press('ENTER')
        self.assertEqual(self.menu.screen, section)

    def test_play_default(self):
        self.assertEqual(self.menu.press('ENTER'), MenuAction('play', 0))

    def test_last_scene_page(self):
        self.open('scenes')
        self.assertEqual(self.menu.pages, 3)
        self.menu.press('LEFT')
        self.assertEqual(self.menu.page, 2)
        self.menu.press('DOWN')
        self.menu.press('DOWN')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('play', 12))
        self.menu.press('RIGHT')
        self.assertEqual(self.menu.page, 0)

    def test_chapter_page_boundary(self):
        self.open('scenes')
        for _ in range(4):
            self.menu.press('DOWN')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('play', 4))
        self.menu.press('RIGHT')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('play', 5))

    def test_audio_track_state(self):
        self.open('audio')
        self.menu.press('DOWN')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('audio', 1))
        self.menu.press('BACK')
        self.open('audio')
        self.assertEqual(self.menu.focus, 1)

    def test_subtitles_off_and_on(self):
        self.open('subtitles')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('subtitle', -1))
        self.menu.press('DOWN')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('subtitle', 0))

    def test_bonus_and_top_menu(self):
        self.open('bonus')
        self.assertEqual(self.menu.press('ENTER'), MenuAction('bonus', 0))
        self.menu.press('TOP_MENU')
        self.assertEqual(self.menu.screen, 'home')

    def test_invalid_key_preserves_state(self):
        before = self.menu.view()
        with self.assertRaises(ValueError):
            self.menu.press('INVALID')
        self.assertEqual(self.menu.view(), before)

    def test_single_chapter_has_no_extra_options(self):
        menu = MenuController(MenuSpec(chapters=('Only',)))
        self.assertEqual(menu.entries, ('play',))
        self.assertEqual(menu.press('ENTER'), MenuAction('play', 0))

if __name__ == '__main__':
    unittest.main()
