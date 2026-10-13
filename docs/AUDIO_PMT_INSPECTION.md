# Read-only M2TS audio metadata inspection (experimental)

This development utility inspects the MPEG-TS PAT and PMT in an **unencrypted**
Blu-ray-style 192-byte M2TS source. It does not burn a disc, alter video, inspect
protected discs, assert ISO/UDF validity, or establish standalone-player compatibility.

From the repository root, with a BDMV folder already authored:

```sh
python -m tools.bdmv_audio_check "Output/BDMV/STREAM/00000.m2ts" --expected-ac3 2
```

Exit code `0` means a complete CRC-valid PAT/PMT pair was found within the first
32,768 M2TS packets (6 MiB) **and** that its PMT advertised two elementary streams
with `stream_type=0x81` (Blu-ray AC-3), matching the PAT program number. It does **not** prove that audio payloads are
valid/decodable, selectable through BD-J, or in sync. Exit code `2` indicates
malformed/inconclusive metadata or a different AC-3 stream count; it does not by
itself prove the M2TS is bad. PSI sections spanning consecutive 188-byte TS packets are now reassembled with
continuity-counter checks, maximum section size 1024 bytes, and CRC checking.
Reserved/null PAT PMT PIDs, duplicate program numbers, reserved/null elementary PIDs,
PMT/elementary PID collisions, and invalid PCR PIDs fail closed.
Missing or out-of-order packets, invalid pointers and incomplete sections fail
closed. The check still inspects only the first 32,768 M2TS packets. Actual hardware playback and audio switching remain
mandatory before compatibility claims.

Tests: `python -m unittest tests/test_bdmv_audio_check.py -v` or normal test
discovery from the repository root.

The checker rejects malformed PSI syntax, mismatched PAT/PMT program numbers,
missing TS sync bytes and discontinuous transport sections. Use this result as a diagnostic **in addition to**,
not instead of, the existing BDMV preflight and the later physical disc tests.

## Optional BDMV folder preflight gate

The BDMV preflight CLI also accepts an explicit `--expected-ac3 N` option.
For each `BDMV/STREAM/*.m2ts` file it rejects absent/inconclusive PAT/PMT
metadata and an unexpected advertised AC-3 stream count:

```sh
python bdmv_preflight.py "Output/Movie" --profile BD25 --expected-ac3 1
```

Default `bdmv_preflight.py` behavior remains unchanged unless the count is
given; applications using the existing two-argument `inspect(root, profile)`
API retain the structural-only check.

For **BD25/BD50 folder authoring**, `core.create_bluray` now enables the gate
before moving the temporary BDMV folder to its final output location:
`expected_ac3=1` for an audio source and `expected_ac3=0` for a silent
source. A mismatch, malformed PAT/PMT, or inconclusive scan aborts publication;
the temporary folder is cleaned. This tests the single-track authoring path,
not decoded audio, multi-audio selection, BD-J switching or ISO validity.
**ISO authoring is not covered by this gate.**

The authoring pipeline regressions use synthetic CRC-valid transport packets
with mocked FFmpeg/tsMuxer; they are **not real tsMuxer output fixtures**.
Because this remains a bounded metadata-only inspection, a clip can still be
inconclusive (for example, when PAT/PMT are not found inside the scan window). Real tsMuxer-generated
fixtures, Windows/macOS packaging and ASUS BW-16D1HT/player testing are still
required before release-readiness claims.

These checks only inspect PAT/PMT advertisements in a bounded part of M2TS,
not audio decoding, stream selection in BD-J, audio synchronization, ISO UDF
validity, burning or compatibility with a physical Blu-ray player.
