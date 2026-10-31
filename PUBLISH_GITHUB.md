# Сборка и выпуск Studio

`main` содержит общий интерфейс и платформенные реализации Windows и macOS. Опубликованы [Windows Studio 1.5.0](https://github.com/FORQO/DiscForge-RU/releases/tag/v1.5.0-studio) и [macOS Studio 1.5.0](https://github.com/FORQO/DiscForge-RU/releases/tag/v1.5.0-studio).

## Проверки основной ветки

PR и push в `main` запускают `Windows Studio build` (Windows и Linux/Python 3.11–3.13) и `macOS Apple Silicon build`. Сборки проходят самопроверку, ZIP и отчёты доступны в Actions artifacts. Изменение main само по себе не публикует релиз.

## Windows

Python 3.12 x64, зависимости из requirements.txt и PyInstaller. Выполните `build_exe_windows.bat`, затем:

```sh
python tools/package_windows.py --app dist/DiscForge_RU --output release
```

Результат: DiskForge-Studio-1.5.1-Windows-x64.zip, инструкция, SHA256SUMS.txt и verification.json. Инструменты и Java закреплены контрольными суммами, VLC устанавливается отдельно.

## macOS Apple Silicon

На Mac с Xcode Command Line Tools, Python 3.11+ и FFmpeg:

```sh
bash build_app_macos.sh
```

Результат: dist/DiskForge.app, DiskForge-Studio-1.5.1-macOS-arm64.zip и build/macos-self-test.json. Подпись локальная ad-hoc, без нотарификации Apple. Подробнее: [macOS](docs/MACOS.md).

## Публикация следующего выпуска

Обновите версии, описание выпуска и контрольные суммы зависимостей при их изменении. Дождитесь тестов и самопроверок обеих сборок; публикуйте новый тег и проверенные ZIP с SHA-256. Уже опубликованные теги и файлы не заменяйте.

Единый кандидат запускается вручную через Actions → Studio 1.5.1 qualification → Run workflow, ветка main. Этот же workflow проверяет PR без публикации. Помимо модульных тестов и самопроверок пакетов, обе ОС проходят tools/studio_journey.py: проект, дорожки, главы, меню, одиночный ISO, коллекция ISO и BDMV. В Windows дополнительно выполняется tools/studio_vlc_journey.py. Отчёты и снимки находятся в QA-Windows и QA-macOS.

Ручной запуск закрепляет один commit SHA для обеих сборок и создаёт **черновик** v1.5.1-studio с двумя ZIP, инструкциями, отчётами и общим SHA256SUMS.txt. release-manifest.json фиксирует исходный коммит, размеры и SHA-256 архивов. Автоматической публикации и замены Latest нет: сначала требуется завершить ручные проверки, перечисленные в описании кандидата. Стабильный 1.5.0 остаётся доступен.

Для следующего выпуска обновите версию и тег в workflow, сборщиках и описании. Повторный запуск не заменяет существующий релиз. GITHUB_TOKEN имеет contents:write только в задаче подготовки черновика. Старые платформенные релизы сохранены как история.
