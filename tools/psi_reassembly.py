"""CRC-checked bounded MPEG-TS PSI reassembly (metadata-only)."""
from tools.psi_transport import extract_payload


class PSIReader:
    """Reassemble one PID's PSI sections across transport packets."""

    def __init__(self, crc32, error_type):
        self.crc32 = crc32
        self.error_type = error_type
        self.pending = bytearray()
        self.last_cc = None

    def _append(self, data):
        output = []
        pos = 0
        while pos < len(data):
            if not self.pending and data[pos] == 0xFF:
                break
            if len(self.pending) < 3:
                n = min(3 - len(self.pending), len(data) - pos)
                self.pending.extend(data[pos:pos + n])
                pos += n
                if len(self.pending) < 3:
                    break
            if self.pending[1] & 0xF0 != 0xB0:
                raise self.error_type('Invalid PSI section syntax/reserved bits')
            length = ((self.pending[1] & 15) << 8) | self.pending[2]
            if not 9 <= length <= 1021:
                raise self.error_type('Invalid PSI section length')
            n = min(3 + length - len(self.pending), len(data) - pos)
            self.pending.extend(data[pos:pos + n])
            pos += n
            if len(self.pending) == 3 + length:
                section = bytes(self.pending)
                if self.crc32(section) != 0:
                    raise self.error_type('PSI CRC mismatch')
                output.append(section)
                self.pending.clear()
        return output

    def push(self, ts):
        parts = extract_payload(ts, self)
        if parts is None:
            return []
        start, prefix, payload = parts
        if start:
            previous = []
            if self.pending:
                previous = self._append(prefix)
                if self.pending:
                    raise self.error_type('PSI section interrupted by new start')
            return previous + self._append(payload)
        if self.pending:
            return self._append(payload)
        return []
