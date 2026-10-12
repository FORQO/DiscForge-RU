"""Rebuild our Xlet with an external Java 8 runtime, ECJ and BD-J API archive.

No third-party runtime classes are copied into menu.jar. Example:
python tools/build_bdj.py --java .../bin/java --ecj ecj.jar --api classes.zip
"""
import argparse
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--java', required=True)
    p.add_argument('--ecj', required=True)
    p.add_argument('--api', required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run([args.java, '-jar', args.ecj, '-source', '1.3', '-target', '1.3',
                        '-encoding', 'UTF-8', '-classpath', args.api, '-d', temp,
                        str(root / 'bdj/DiscForgeXlet.java')], check=True)
        files = sorted(Path(temp).glob('*.class'))
        if not files:
            raise RuntimeError('Compiler produced no classes')
        destination = root / 'assets/bdj/menu.jar'
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as jar:
            for file in files:
                payload = file.read_bytes()
                if payload[:4] != b'\xca\xfe\xba\xbe' or struct.unpack('>H', payload[6:8])[0] != 47:
                    raise RuntimeError('Expected Java 1.3 / class version 47')
                info = zipfile.ZipInfo(file.name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                jar.writestr(info, payload)
            source_hash = hashlib.sha256((root / 'bdj/DiscForgeXlet.java').read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            jar.writestr(zipfile.ZipInfo('META-INF/MANIFEST.MF', (2026, 1, 1, 0, 0, 0)),
                         ('Manifest-Version: 1.0\r\nCreated-By: DiscForge RU\r\n'
                          'DiscForge-Source-SHA256: ' + source_hash[:40] + '\r\n ' + source_hash[40:] + '\r\n\r\n').encode('ascii'))
            jar.writestr(zipfile.ZipInfo('LICENSE', (2026, 1, 1, 0, 0, 0)), (root / 'LICENSE').read_bytes())
        print(destination)
        print('SHA256:', hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
