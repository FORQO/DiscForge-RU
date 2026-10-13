"""Synthetic BD-J chapter timeline checks, not playable Blu-ray disc tests."""
import unittest

from chapter_timeline import validate_chapter_timeline


def playlist(items, marks):
    body = bytearray(b'\0\0' + len(items).to_bytes(2, 'big') + b'\0\0')
    for start, end in items:
        item = bytearray(32)
        item[:5] = b'00000'
        item[5:9] = b'M2TS'
        item[12:16] = start.to_bytes(4, 'big')
        item[16:20] = end.to_bytes(4, 'big')
        body += len(item).to_bytes(2, 'big') + item
    play = len(body).to_bytes(4, 'big') + body
    mark_body = bytearray(len(marks).to_bytes(2, 'big'))
    for item, at in marks:
        mark = bytearray(14)
        mark[1] = 1
        mark[2:4] = item.to_bytes(2, 'big')
        mark[4:8] = at.to_bytes(4, 'big')
        mark_body += mark
    header = bytearray(40)
    header[:8] = b'MPLS0200'
    header[8:12] = (40).to_bytes(4, 'big')
    header[12:16] = (40 + len(play)).to_bytes(4, 'big')
    return bytes(header) + play + len(mark_body).to_bytes(4, 'big') + mark_body


class ChapterTimelineTests(unittest.TestCase):
    def test_nonzero_in_time(self):
        t = 45000
        validate_chapter_timeline(playlist([(2*t, 12*t)], [(0, 2*t), (0, 7*t)]), [0, 5])

    def test_multiplayitem_clock(self):
        t = 45000
        validate_chapter_timeline(playlist([(0, 4*t), (2*t, 8*t)],
                                           [(0, 0), (1, 2*t), (1, 4*t)]), [0, 4, 6])

    def test_allow_minor_keyframe_snap(self):
        t = 45000
        validate_chapter_timeline(playlist([(0, 20*t)], [(0, 0), (0, 6*t)]), [0, 5])

    def test_duplicate_marks_fail(self):
        with self.assertRaisesRegex(ValueError, 'возрастанию'):
            validate_chapter_timeline(playlist([(0, 450000)], [(0, 0), (0, 0)]), [0, 3])

    def test_reversed_marks_fail(self):
        t = 45000
        with self.assertRaisesRegex(ValueError, 'возрастанию'):
            validate_chapter_timeline(playlist([(0, 10*t)], [(0, 0), (0, 5*t), (0, 4*t)]), [0, 5, 6])

    def test_before_playitem_in_fails(self):
        t = 45000
        with self.assertRaisesRegex(ValueError, 'вне интервала'):
            validate_chapter_timeline(playlist([(2*t, 10*t)], [(0, t)]), [0])

    def test_at_playitem_out_fails(self):
        t = 45000
        with self.assertRaisesRegex(ValueError, 'вне интервала'):
            validate_chapter_timeline(playlist([(0, 10*t)], [(0, 10*t)]), [0])

    def test_bad_chapter_jump_fails(self):
        t = 45000
        with self.assertRaisesRegex(ValueError, 'не соответствует'):
            validate_chapter_timeline(playlist([(0, 20*t)], [(0, 0), (0, 12*t)]), [0, 4])

    def test_second_item_bad_jump_fails(self):
        t = 45000
        with self.assertRaisesRegex(ValueError, 'не соответствует'):
            validate_chapter_timeline(playlist([(0, 4*t), (2*t, 10*t)],
                                               [(0, 0), (1, 9*t)]), [0, 6])

    def test_zero_duration_playitem_fails(self):
        with self.assertRaisesRegex(ValueError, 'интервал PlayItem'):
            validate_chapter_timeline(playlist([(45000, 45000)], [(0, 45000)]), [0])

    def test_truncated_playitem_fails(self):
        data = bytearray(playlist([(0, 45000)], [(0, 0)]))
        data[50:52] = (1000).to_bytes(2, 'big')
        with self.assertRaisesRegex(ValueError, 'границы PlayItem'):
            validate_chapter_timeline(data, [0])

    def test_duplicate_requested_chapters_fail(self):
        with self.assertRaisesRegex(ValueError, 'возрастать'):
            validate_chapter_timeline(playlist([(0, 450000)], [(0, 0), (0, 45000)]), [0, 0])

    def test_large_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Некорректный MPLS'):
            validate_chapter_timeline(b'MPLS0200' + bytes(16*1024*1024), [0])

    def test_zero_start_required(self):
        with self.assertRaisesRegex(ValueError, 'начинаться с нуля'):
            validate_chapter_timeline(playlist([(0, 450000)], [(0, 45000)]), [1])
