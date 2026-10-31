# DiskForge Studio 1.5.0 — Windows и macOS

Один выпуск Studio для двух платформ, собранный из одного коммита основной ветки `main`. Исходный коммит и SHA-256 обоих архивов записаны в `release-manifest.json`.

## Скачать

| Платформа | Файл |
| --- | --- |
| Windows 10/11 x64 | [DiskForge-Studio-1.5.0-Windows-x64.zip](https://github.com/FORQO/DiscForge-RU/releases/download/v1.5.0-studio/DiskForge-Studio-1.5.0-Windows-x64.zip) |
| macOS Apple Silicon (M1 и новее) | [DiskForge-Studio-1.5.0-macOS-arm64.zip](https://github.com/FORQO/DiscForge-RU/releases/download/v1.5.0-studio/DiskForge-Studio-1.5.0-macOS-arm64.zip) |

**Windows:** распакуйте весь архив и запустите `DiscForge_RU/DiscForge_RU.exe`.

**Mac:** распакуйте архив и перенесите `DiskForge.app` в «Программы». При блокировке запуска используйте «Системные настройки → Конфиденциальность и безопасность → Всё равно открыть» для DiskForge.

Python, Qt, FFmpeg, ffprobe, tsMuxer и Java входят в обе сборки. VLC устанавливается отдельно: x64 для Windows, arm64 для Mac.

## Интерфейс Studio

Библиотека фильмов слева, большой холст в центре, свойства справа: «Оформление», «Объект» и «Расстановка». Светлая и тёмная темы, отдельный предпросмотр с пультом под изображением, сворачиваемый журнал и диалог экспорта ISO/BDMV.

![Редактор Studio](https://raw.githubusercontent.com/FORQO/DiscForge-RU/v1.5.0-studio/docs/images/studio-menu.jpg)

Снимок настоящего приложения на macOS с демонстрационными данными. В Windows используется общий интерфейс; системные шрифты и рамка окна отличаются.

Сохранены коллекции фильмов, BD25/BD50, меню BD-J, главы, звук и субтитры, пробы качества, улучшение видео и возобновление сборки. Проекты прежних версий поддерживаются. На Windows доступна NVIDIA при совместимом оборудовании; на Mac перекодирование выполняет CPU x264.

## Проверки и файлы

Публикация допускается после тестов Windows, macOS и Linux/Python 3.11–3.13 и самопроверки обоих упакованных приложений. В приложениях релиза: `verification-Windows.json`, `verification-macOS.json`, инструкции `README-Windows.md` и `README-macOS.md`, общий `SHA256SUMS.txt` и `release-manifest.json`.

Windows-пакет не подписан. Mac-пакет имеет локальную ad-hoc подпись без нотарификации Apple; Intel не проверен. Авторинг ориентирован на Full HD Blu-ray. Запись физических носителей из интерфейса и совместимость со всеми бытовыми плеерами не подтверждены.

[Обзор Studio](https://github.com/FORQO/DiscForge-RU/blob/v1.5.0-studio/docs/STUDIO.md) · [Руководство](https://github.com/FORQO/DiscForge-RU/blob/v1.5.0-studio/docs/USER_GUIDE.md) · [Исходники выпуска](https://github.com/FORQO/DiscForge-RU/tree/v1.5.0-studio)
