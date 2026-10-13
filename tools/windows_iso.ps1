param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$VolumeLabel = 'DISCFORGE',
    [long]$Capacity = 25000000000,
    [string]$Overlay = '',
    [string]$ProgressFile = ''
)
$ErrorActionPreference = 'Stop'
try {
    $discSource = (Resolve-Path -LiteralPath $Source).Path
    $discDestination = [IO.Path]::GetFullPath($Destination)
    if (Test-Path -LiteralPath $discDestination) { throw 'Destination already exists' }
    if ($discDestination.StartsWith($discSource.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'ISO must be outside its source tree'
    }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class DiscForgeStream {
    [DllImport("shlwapi.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void SHCreateStreamOnFileEx(string path, uint mode, uint attributes, bool create, IStream template, out IStream stream);
    public static IStream Open(string path) {
        IStream stream;
        SHCreateStreamOnFileEx(path, 0x20, 0, false, null, out stream);
        return stream;
    }
    public static void Save(object source, string path, long bytes, string progressPath) {
        IStream stream = (IStream)source;
        IntPtr read = Marshal.AllocCoTaskMem(4);
        try {
            using (FileStream output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                output.SetLength(bytes);
                var timer = System.Diagnostics.Stopwatch.StartNew();
                byte[] buffer = new byte[1048576];
                long remaining = bytes;
                while (remaining > 0) {
                    int wanted = (int)Math.Min(buffer.Length, remaining);
                    stream.Read(buffer, wanted, read);
                    int count = Marshal.ReadInt32(read);
                    if (count <= 0 || count > wanted) throw new IOException("Truncated IMAPI image stream");
                    output.Write(buffer, 0, count);
                    remaining -= count;
                    if (progressPath.Length > 0 && (timer.ElapsedMilliseconds >= 500 || remaining == 0)) {
                        File.WriteAllText(progressPath, (bytes - remaining).ToString());
                        timer.Restart();
                    }
                }
                output.Flush(true);
            }
        } finally { Marshal.FreeCoTaskMem(read); }
    }
}
'@
    $discImage = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $discImage.FileSystemsToCreate = 4
    $discImage.UDFRevision = 0x250
    $discImage.CreateRedundantUdfMetadataFiles = $true
    $discImage.FreeMediaBlocks = [int][Math]::Floor($Capacity / 2048)
    $discImage.VolumeName = $VolumeLabel
    $discImage.Root.AddTree($discSource, $false)
    if ($Overlay) {
        function Add-Overlay($directory, [string]$path) {
            foreach ($entry in Get-ChildItem -LiteralPath $path) {
                $existing = $null
                try { $existing = $directory.Item($entry.Name) } catch {}
                if ($entry.PSIsContainer) {
                    if ($null -eq $existing) { $directory.AddDirectory($entry.Name); $existing = $directory.Item($entry.Name) }
                    Add-Overlay $existing $entry.FullName
                } else {
                    if ($null -ne $existing) { $directory.Remove($entry.Name) }
                    $directory.AddFile($entry.Name, [DiscForgeStream]::Open($entry.FullName))
                }
            }
        }
        Add-Overlay $discImage.Root (Resolve-Path -LiteralPath $Overlay).Path
    }
    $discResult = $discImage.CreateResultImage()
    $discSize = [long]$discResult.TotalBlocks * [long]$discResult.BlockSize
    if ($discSize -gt $Capacity) { throw 'ISO exceeds selected disc capacity' }
    [DiscForgeStream]::Save($discResult.ImageStream, $discDestination, $discSize, $ProgressFile)
    Write-Output "UDF 2.50 image created: $discSize bytes"
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
