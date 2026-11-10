# Сборка и выпуск Studio

`main` содержит общий интерфейс и платформенные реализации Windows и macOS. Текущий выпуск: [DiskForge Studio 1.5.1](https://github.com/FORQO/DiscForge-RU/releases/tag/v1.5.1-studio), [описание изменений](docs/RELEASE_STUDIO_1.5.1.md). Физическая проверка диска на GIEC G2805 подтверждена автором проекта; [запись результата](docs/PLAYER_TEST_MATRIX.md).

## Windows

Нужны Python 3.12 x64, зависимости из `requirements.txt` и PyInstaller 6.22.3. Выполните `build_exe_windows.bat` либо:

```sh
python tools/build_windows.py
python tools/package_windows.py --app dist/DiscForge_RU --output release
```

Сборщик ограничивает PATH Python и системными каталогами Windows. Это исключает попадание посторонних DLL ICU/Qt из Poppler, ImageMagick и других инструментов в пакет. Параметры `--distpath` и `--workpath` позволяют выбрать каталоги сборки.

Результат: `DiskForge-Studio-1.5.1-Windows-x64.zip`, инструкция, `SHA256SUMS.txt` и `verification.json`. FFmpeg, tsMuxer и Java закреплены контрольными суммами; VLC устанавливается отдельно.

## macOS Apple Silicon

На Mac с Xcode Command Line Tools, Python 3.11+ и FFmpeg выполните `bash build_app_macos.sh`. Результат: `dist/DiskForge.app`, `DiskForge-Studio-1.5.1-macOS-arm64.zip` и `build/macos-self-test.json`. Подпись локальная ad-hoc, без нотарификации Apple. [Инструкция](docs/MACOS.md).

## Проверки и публикация

PR запускает проверки без публикации. Workflow `Studio 1.5.1 qualification` проверяет Linux/Python 3.11–3.13, Windows и macOS, собирает оба пакета и выполняет `tools/studio_journey.py`: проект, дорожки, главы, меню, одиночный ISO, коллекция ISO и BDMV. Windows дополнительно проходит проверку BD-J через libVLC. Отчёты и снимки сохраняются в `QA-Windows` и `QA-macOS`.

Обновление `docs/RELEASE_STUDIO_1.5.1.md` в `main` или ручной запуск workflow из `main` запускает выпуск. Публикация выполняется только после успешных тестов, самопроверок и сквозных сценариев обеих платформ. `tools/prepare_studio_release.py` проверяет единый исходный коммит и формирует общий манифест и SHA-256.

Существующий **неопубликованный** черновик 1.5.1 можно обновить проверенными пакетами этого коммита; затем он публикуется как стабильный Latest. Если выпуск уже опубликован, workflow останавливается до изменения его файлов. Уже опубликованные теги и вложения не заменяются. Для следующего выпуска обновите версии, тег, пути и описание. Прежний 1.5.0 остаётся доступен в истории релизов.

GITHUB_TOKEN получает `contents: write` только в задаче публикации. Результаты физических испытаний записываются отдельно от автоматических отчётов; сведения о непроверенных моделях не выводятся из успеха GIEC G2805.
