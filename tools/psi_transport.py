"""Extract bounded PSI payloads from a 188-byte unencrypted TS packet."""


def extract_payload(ts: bytes, reader):
    error = reader.error_type
    if len(ts) != 188 or ts[0] != 0x47 or ts[1] & 0x80:
        raise error('Invalid TS sync/transport-error header')
    if ts[3] & 0xC0:
        raise error('Encrypted TS packet is unsupported')
    control = (ts[3] >> 4) & 3
    if control == 0:
        raise error('Reserved TS adaptation control')
    offset = 4
    if control in (2, 3):
        length = ts[offset]
        end = offset + 1 + length
        if end > len(ts) or (control == 3 and end == len(ts)):
            raise error('Invalid TS adaptation-field length')
        if length and ts[offset + 1] & 0x80:
            reader.last_cc = None
            reader.pending.clear()
        if control == 2:
            return None
        offset = end
    cc = ts[3] & 15
    if reader.last_cc is not None and cc != (reader.last_cc + 1) % 16:
        raise error('PSI transport continuity-counter mismatch')
    reader.last_cc = cc
    if ts[1] & 0x40:
        if offset >= len(ts):
            raise error('Truncated PSI pointer field')
        pointer = ts[offset]
        offset += 1
        if pointer > len(ts) - offset:
            raise error('PSI pointer exceeds TS payload')
        return (True, ts[offset:offset + pointer], ts[offset + pointer:])
    return (False, b'', ts[offset:])
