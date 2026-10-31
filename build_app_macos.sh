#!/bin/bash
# Run on the target Mac architecture. All authoring tools are bundled.
set -euo pipefail
cd "$(dirname "$0")"
[[ "$(uname -s)" == Darwin ]] || { echo 'Сборка требует macOS.'; exit 1; }
bash start_macos.command --prepare-only
.venv/bin/python -m pip install pyinstaller==6.22.3
.venv/bin/python tools/build_macos_tools.py
ffmpeg_bin="${DISKFORGE_FFMPEG:-$(command -v ffmpeg || true)}"
ffprobe_bin="${DISKFORGE_FFPROBE:-$(command -v ffprobe || true)}"
[[ -f "$ffmpeg_bin" && -f "$ffprobe_bin" ]] || { echo 'Установите FFmpeg: brew install ffmpeg'; exit 1; }
mkdir -p build/DiskForge.iconset
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" assets/brand/diskforge-symbol.png --out "build/DiskForge.iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" assets/brand/diskforge-symbol.png --out "build/DiskForge.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/DiskForge.iconset -o build/DiskForge.icns
.venv/bin/python -m PyInstaller --clean --noconfirm --windowed \
    --name DiskForge --osx-bundle-identifier ru.forqo.diskforge \
    --icon build/DiskForge.icns \
    --add-data 'assets:assets' --add-data 'LICENSE:.' \
    --add-data 'THIRD_PARTY_NOTICES.md:.' --add-data 'licenses:licenses' \
    --add-data 'runtime/java:runtime/java' \
    --add-binary 'bin/discforge-udf:bin' --add-binary 'bin/tsMuxeR:bin' \
    --add-binary "$ffmpeg_bin:bin" --add-binary "$ffprobe_bin:bin" main.py
# PyInstaller signs locally; no Developer ID or notarization is implied.
dist/DiskForge.app/Contents/MacOS/DiskForge --self-test build/macos-self-test.json
ditto -c -k --sequesterRsrc --keepParent dist/DiskForge.app "dist/DiskForge-Studio-1.5.0-macOS-$(uname -m).zip"
echo "Готово: dist/DiskForge.app и ZIP для $(uname -m). Локальная подпись, без нотарификации Apple."
