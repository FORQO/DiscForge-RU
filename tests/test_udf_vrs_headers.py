import io
import unittest
from tools import iso_udf_preflight as u

class VRSHeaders(unittest.TestCase):
    def test_headers(self):
        for lba in (16,17,18):
            for offset in (0,6):
                data=bytearray(64*u.SECTOR_SIZE)
                for i,ident in ((16,b'BEA01'),(17,b'NSR03'),(18,b'TEA01')):
                    data[i*u.SECTOR_SIZE+1:i*u.SECTOR_SIZE+6]=ident
                    data[i*u.SECTOR_SIZE+6]=1
                data[lba*u.SECTOR_SIZE+offset]=2
                self.assertFalse(u._vrs(io.BytesIO(data),64))
