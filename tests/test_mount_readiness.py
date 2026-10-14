"""Exercise the actual mount PowerShell with delayed Storage responses."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from iso_menu_update import mount_script, powershell


@unittest.skipUnless(os.name == 'nt', 'Windows Storage/PowerShell')
class MountReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'volume').mkdir()
        (self.root / 'volume/index.bdmv').write_bytes(b'INDX')
        self.iso = self.root / 'test.iso'
        self.prefix = r'''
$script:queries = 0; $script:mounts = 0; $script:ejects = 0
$script:attached = $false
function Get-DiskImage { param($ImagePath) [pscustomobject]@{Attached=$script:attached} }
function Mount-DiskImage {
    param($ImagePath, $StorageType, $Access, [switch]$PassThru)
    if ($Access -ne 'ReadOnly') { throw 'not read-only' }
    $script:mounts++; $script:attached=$true
    [pscustomobject]@{Attached=$true}
}
function Dismount-DiskImage { param($ImagePath, $ErrorAction) $script:ejects++ }
function Start-Sleep { param($Milliseconds) } # No wall-clock delay in success tests.
'''

    def run_script(self, get_volume, *, attached=False, timeout=15):
        code = self.prefix + ('$script:attached=$true\n' if attached else '') + get_volume
        code += '\ntry { $result = & {\n' + mount_script(timeout) + '\n}; $errorText=$null } catch { $errorText=$_.Exception.Message }\n'
        code += '@{result=$result; error=$errorText; queries=$script:queries; mounts=$script:mounts; ejects=$script:ejects} | ConvertTo-Json -Compress'
        return json.loads(powershell(code, self.iso))

    def test_delayed_volume_is_retried(self):
        result = self.run_script(r'''
function Get-Volume {
    param($ErrorAction)
    $script:queries++
    if ($script:queries -gt 2) {
        [pscustomobject]@{Path=(Join-Path (Split-Path $env:DISCFORGE_ISO) 'volume'); DriveLetter=$null}
    }
}
''')
        self.assertIsNone(result['error'])
        self.assertEqual(result['queries'], 3)
        self.assertEqual(result['mounts'], 1)
        self.assertEqual(result['ejects'], 0)
        mounted = json.loads(result['result'])
        self.assertTrue(mounted['owned'])
        # Windows CI can expose TEMP through an 8.3 alias (RUNNER~1), while
        # PowerShell returns its long path. Both must identify the same folder.
        self.assertTrue(Path(mounted['root']).samefile(self.root / 'volume'))

    def test_volume_exists_before_filesystem_ready(self):
        result = self.run_script(r'''
function Get-Volume {
    param($ErrorAction)
    $script:queries++
    $name = if ($script:queries -eq 1) {'not-ready'} else {'volume'}
    [pscustomobject]@{Path=(Join-Path (Split-Path $env:DISCFORGE_ISO) $name); DriveLetter=$null}
}
''')
        self.assertIsNone(result['error'])
        self.assertEqual(result['queries'], 2)

    def test_timeout_cleans_only_mount_owned_by_us(self):
        volume = 'function Get-Volume { param($ErrorAction) $script:queries++ }'
        for attached in (False, True):
            with self.subTest(attached=attached):
                result = self.run_script(volume, attached=attached, timeout=0)
                self.assertEqual(result['error'], 'ISO_MOUNT_VOLUME_TIMEOUT')
                self.assertEqual(result['ejects'], 0 if attached else 1)
                self.assertEqual(result['mounts'], 0 if attached else 1)
