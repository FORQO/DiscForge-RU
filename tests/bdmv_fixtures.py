"""Synthetic BDMV fixtures used by unit tests, not playable discs."""


def minimal_clpi(*, packets: int = 1, version: bytes = b"0200", optional: bool = False) -> bytes:
    """Build bounded synthetic CLPI sections (not a playable clip)."""
    clip_info = bytearray(20)
    clip_info[:4] = (16).to_bytes(4, "big")
    clip_info[16:20] = packets.to_bytes(4, "big")
    sequence = (2).to_bytes(4, "big") + b"\0\0"
    program = (2).to_bytes(4, "big") + b"\0\0"
    cpi = (0).to_bytes(4, "big") if optional else b""
    header = bytearray(40)
    header[:8] = b"HDMV" + version
    header[8:12] = (60).to_bytes(4, "big")
    header[12:16] = (66).to_bytes(4, "big")
    if optional:
        header[16:20] = (72).to_bytes(4, "big")
    return bytes(header) + bytes(clip_info) + sequence + program + cpi


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


def audio_mpls(languages=()):
    """One PlayItem with a real primary audio STN envelope and a chapter."""
    data = bytearray(minimal_mpls())
    stn = bytearray(16)
    stn[4] = 1
    stn[5] = len(languages)
    # stream entry (type 1 + PID), AVC stream attributes
    stn += bytes([3, 1, 0x10, 0x11, 2, 0x1b, 0x61])
    for i, lang in enumerate(languages):
        stn += bytes([3, 1, 0x11, i, 5, 0x81, 0x31]) + lang.encode('ascii')
    if len(stn) % 2:
        stn += b'\0'
    stn[:2] = (len(stn)-2).to_bytes(2, 'big')
    marks = int.from_bytes(data[12:16], 'big')
    data[marks:marks] = stn
    data[12:16] = (marks+len(stn)).to_bytes(4, 'big')
    data[40:44] = (int.from_bytes(data[40:44], 'big')+len(stn)).to_bytes(4, 'big')
    data[50:52] = (32+len(stn)).to_bytes(2, 'big')
    return bytes(data)
