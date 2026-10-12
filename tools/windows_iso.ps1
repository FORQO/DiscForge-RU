param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$VolumeLabel = 'DISCFORGE',
    [long]$Capacity = 25000000000
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
    public static void Save(object source, string path, long bytes) {
        IStream stream = (IStream)source;
        IntPtr read = Marshal.AllocCoTaskMem(4);
        try {
            using (FileStream output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                byte[] buffer = new byte[1048576];
                long remaining = bytes;
                while (remaining > 0) {
                    int wanted = (int)Math.Min(buffer.Length, remaining);
                    stream.Read(buffer, wanted, read);
                    int count = Marshal.ReadInt32(read);
                    if (count <= 0 || count > wanted) throw new IOException("Truncated IMAPI image stream");
                    output.Write(buffer, 0, count);
                    remaining -= count;
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
    $discResult = $discImage.CreateResultImage()
    $discSize = [long]$discResult.TotalBlocks * [long]$discResult.BlockSize
    if ($discSize -gt $Capacity) { throw 'ISO exceeds selected disc capacity' }
    [DiscForgeStream]::Save($discResult.ImageStream, $discDestination, $discSize)
    Write-Output "UDF 2.50 image created: $discSize bytes"
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
