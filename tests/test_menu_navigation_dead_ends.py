"""Navigation regressions for the exported Full HD BD-J button graph.

Pure-Python fixtures; this does not emulate a physical Blu-ray player.
"""
import unittest

from disc_menu import MenuDesign, pages, validate_menu_navigation


class NavigationDeadEndTests(unittest.TestCase):
    def test_default_multipage_chapter_menu_remains_valid(self):
        validate_menu_navigation([float(i * 10) for i in range(13)], MenuDesign('Film'))

    def test_reachable_chapter_page_with_all_buttons_hidden_is_rejected(self):
        design = MenuDesign('Film', hidden_buttons=['chapter:0', 'chapter:1', 'home:1'])
        graph = pages([0, 10], design)
        self.assertEqual([action for _, action in graph[0]], ['play:0', 'page:1'])
        self.assertEqual(graph[1], [])
        with self.assertRaisesRegex(ValueError, 'без кнопок'):
            validate_menu_navigation([0, 10], design)

    def test_reachable_audio_page_with_all_buttons_hidden_is_rejected(self):
        design = MenuDesign('Film', audio_tracks=[{'language': 'rus', 'title': ''}],
                            hidden_buttons=['audio:1', 'home:1'])
        self.assertEqual(pages([0], design)[1], [])
        with self.assertRaisesRegex(ValueError, 'без кнопок'):
            validate_menu_navigation([0], design)

    def test_intentionally_hidden_submenu_is_not_a_reachable_dead_end(self):
        design = MenuDesign('Film',
                            hidden_buttons=['chapters', 'chapter:0', 'chapter:1', 'home:1'])
        self.assertEqual(pages([0, 10], design)[1], [])
        validate_menu_navigation([0, 10], design)

    def test_play_button_can_be_hidden_if_chapters_still_play(self):
        validate_menu_navigation([0, 10], MenuDesign('Film', hidden_buttons=['play']))

    def test_no_reachable_play_action_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'нет доступной кнопки запуска'):
            validate_menu_navigation([0], MenuDesign('Film', hidden_buttons=['play']))


if __name__ == '__main__':
    unittest.main()
