"""Minimal synthetic MPLS section used by unit tests, not a playable disc."""


def minimal_mpls(clip: str = "00000", *, mark_item: int = 0) -> bytes:
    item = bytearray(32)
    item[:5] = clip.encode('ascii')
    item[5:9] = b'M2TS'
    item[16:20] = (45000).to_bytes(4, 'big')
    playlist_body = b'\0\0' + (1).to_bytes(2, 'big') + b'\0\0' + (len(item)).to_bytes(2, 'big') + bytes(item)
    playlist = len(playlist_body).to_bytes(4, 'big') + playlist_body
    mark = bytearray(14)
    mark[1] = 1
    mark[2:4] = mark_item.to_bytes(2, 'big')
    marks_body = (1).to_bytes(2, 'big') + bytes(mark)
    marks = len(marks_body).to_bytes(4, 'big') + marks_body
    head = bytearray(40)
    head[:8] = b'MPLS0200'
    head[8:12] = (40).to_bytes(4, 'big')
    head[12:16] = (40 + len(playlist)).to_bytes(4, 'big')
    return bytes(head) + playlist + marks
