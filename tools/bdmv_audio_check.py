"""Read-only, bounded M2TS PMT audio check for experimental Full HD BDMV.

This is an *independent diagnostic*; it does not validate Blu-ray playback,
TS continuity, codecs, timing or UDF. It never modifies media or burns discs.
CRC-valid PAT/PMT sections can span multiple consecutive TS packets.
Incomplete sections and continuity errors never count as successful checks.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tools.psi_reassembly import PSIReader

M2TS_PACKET_BYTES = 192
MAX_PACKETS = 32768  # 6 MiB bounded scan
STREAM_TYPES = {0x80: 'LPCM', 0x81: 'AC3', 0x82: 'DTS',
                0x83: 'TRUEHD', 0x84: 'EAC3', 0x85: 'DTS_HD',
                0x86: 'DTS_HD_MA'}


class AudioInspectionError(ValueError):
    """Malformed/inconclusive unencrypted Blu-ray transport metadata."""


@dataclass(frozen=True)
class Stream:
    pid: int
    type_id: int
    codec: str


def _crc32_mpeg(section: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in section:
        crc ^= value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
    return crc


def _pat(section: bytes) -> tuple[int, int]:
    if section[0] != 0x00 or (len(section) - 12) % 4:
        raise AudioInspectionError('Invalid PAT structure')
    if not section[5] & 1 or section[6] != 0 or section[7] != 0:
        raise AudioInspectionError('Inactive or multipart PAT is inconclusive')
    programs: set[tuple[int, int]] = set()
    for pos in range(8, len(section) - 4, 4):
        program = int.from_bytes(section[pos:pos + 2], 'big')
        pid = ((section[pos + 2] & 0x1F) << 8) | section[pos + 3]
        if program:
            programs.add((program, pid))
    if len(programs) != 1:
        raise AudioInspectionError('Expected one PAT program in authored 2D Blu-ray')
    return next(iter(programs))


def _pmt(section: bytes, program_number: int) -> tuple[Stream, ...]:
    if section[0] != 0x02 or len(section) < 16:
        raise AudioInspectionError('Invalid PMT structure')
    if not section[5] & 1 or section[6] != 0 or section[7] != 0:
        raise AudioInspectionError('Inactive or multipart PMT is inconclusive')
    if int.from_bytes(section[3:5], 'big') != program_number:
        raise AudioInspectionError('PMT program number does not match PAT')
    end = len(section) - 4
    descriptor_length = ((section[10] & 0x0F) << 8) | section[11]
    position = 12 + descriptor_length
    if position > end:
        raise AudioInspectionError('PMT program descriptors exceed section')
    streams = []
    while position < end:
        if position + 5 > end:
            raise AudioInspectionError('Truncated PMT elementary-stream entry')
        kind = section[position]
        pid = ((section[position + 1] & 0x1F) << 8) | section[position + 2]
        size = ((section[position + 3] & 0x0F) << 8) | section[position + 4]
        position += 5 + size
        if position > end:
            raise AudioInspectionError('PMT stream descriptors exceed section')
        streams.append(Stream(pid, kind, STREAM_TYPES.get(kind, 'other')))
    if len({stream.pid for stream in streams}) != len(streams):
        raise AudioInspectionError('Duplicate elementary PIDs in PMT')
    return tuple(streams)


def inspect(path: Path, expected_ac3: int | None = None) -> tuple[Stream, ...]:
    """Find PAT and PMT in the initial bounded scan; optionally verify AC-3 count."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != '.m2ts':
        raise AudioInspectionError('Expected an existing regular .m2ts file (not a symlink)')
    size = path.stat().st_size
    if size == 0 or size % M2TS_PACKET_BYTES:
        raise AudioInspectionError('M2TS size must be nonzero and divisible by 192')
    if expected_ac3 is not None and (type(expected_ac3) is not int or not 0 <= expected_ac3 <= 8):
        raise AudioInspectionError('Expected AC-3 count must be 0–8')
    program: tuple[int, int] | None = None
    pat = PSIReader(_crc32_mpeg, AudioInspectionError)
    pmt: PSIReader | None = None
    with path.open('rb') as source:
        for _ in range(min(size // M2TS_PACKET_BYTES, MAX_PACKETS)):
            ts = source.read(M2TS_PACKET_BYTES)[4:]
            if len(ts) != 188 or ts[0] != 0x47:
                raise AudioInspectionError('Missing M2TS transport sync byte')
            pid = ((ts[1] & 0x1F) << 8) | ts[2]
            if pid == 0:
                for section in pat.push(ts):
                    found = _pat(section)
                    if program is not None and program != found:
                        raise AudioInspectionError('PAT program changed during inspection')
                    program = found
                    if pmt is None:
                        pmt = PSIReader(_crc32_mpeg, AudioInspectionError)
            elif program is not None and pid == program[1] and pmt is not None:
                for section in pmt.push(ts):
                    streams = _pmt(section, program[0])
                    if expected_ac3 is not None and sum(s.codec == 'AC3' for s in streams) != expected_ac3:
                        raise AudioInspectionError('AC-3 stream count does not match expected value')
                    return streams
    raise AudioInspectionError('No complete PAT/PMT pair found within bounded scan')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only AC-3 PMT preflight for BDMV .m2ts')
    parser.add_argument('file', type=Path)
    parser.add_argument('--expected-ac3', type=int, default=None)
    args = parser.parse_args(argv)
    try:
        streams = inspect(args.file, args.expected_ac3)
    except (AudioInspectionError, OSError) as exc:
        print(json.dumps({'pmt_parsed': False, 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({'pmt_parsed': True, 'streams': [asdict(s) for s in streams],
                      'ac3_count': sum(s.codec == 'AC3' for s in streams)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
