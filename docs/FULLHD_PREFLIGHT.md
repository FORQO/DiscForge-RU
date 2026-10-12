# Full HD Blu-ray BDMV preflight

Run: python -m bdmv_preflight PATH_TO_DISC_FOLDER --profile BD25

BD25/BD50 folder authoring now automatically runs the same structural preflight before publishing the folder. ISO output is NOT inspected this way; independently validate UDF 2.50 and the ISO before burning.

Checks: supported INDX/MOBJ/MPLS/CLPI version headers, five-digit BDMV filenames, matching CLIPINF/STREAM identifiers, bounded M2TS packet-header sampling (sync, transport-error flag, adaptation control and length, rejection of nonzero TS payload-scrambling control for clear authoring) and BD25/BD50 size. Exit 0: limited structural checks passed. Exit 2: checks failed. Output is JSON. This is NOT UDF 2.50 validation, a menu test, or evidence of hardware player compatibility. Only BD25 and BD50 are in scope. UHD/4K/BDXL deferred.

Sampling examines short windows around the start, 25%, 50%, 75%, and end of each M2TS file without reading a complete BD25/BD50 stream. It does not validate every transport packet, PID continuity, PAT/PMT, audio/video codecs, navigation or player compatibility. Invalid sampled transport headers block folder publication.

Transport packets with `adaptation_field_control = 2` (adaptation field only)
require an adaptation-field length of exactly 183 bytes; shorter lengths
leave unaccounted-for bytes, even though they fit within the 188-byte packet.
Transport scrambling-control bits must be zero for unencrypted DiscForge RU
output; the preflight does not decrypt or recover protected video.

Sampled transport packets also check that PCR, OPCR, splice countdown, private
adaptation data, extension length and extension-specific LTW/piecewise rate/
seamless splice bytes fit their advertised adaptation-field lengths. This
remains a bounded sampled syntax check rather than full TS/PES validation.

The four-byte M2TS TP_extra_header is also sampled. Its top two
copy_permission_indicator bits must be zero for DiscForge RU's clear output;
the remaining 30 bits are the arrival timestamp and are not interpreted as
copy controls. This is a conservative authoring check, not a DRM detector,
decryptor, or proof that unsampled source packets are unprotected.
