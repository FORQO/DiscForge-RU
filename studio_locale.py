"""Application UI language. Authored menu labels and media metadata stay in their original language."""
from studio_strings_en import MESSAGES

_active = "ru"
_preference = "auto"


def active_language():
    return _active


def preference():
    return _preference


def normalize_preference(value):
    return value if value in ("ru", "en") else "auto"


def resolve_language(value, system_language):
    value = normalize_preference(value)
    if value != "auto":
        return value
    return "ru" if str(system_language).replace("_", "-").lower().split("-")[0] == "ru" else "en"


def configure_console_language():
    import locale
    from settings import load

    global _active, _preference
    _preference = normalize_preference(load().get("ui_language", "auto"))
    system = locale.getlocale()[0] or ""
    # The Windows C runtime may report an English language name, e.g. Russian_Russia.
    _active = resolve_language(_preference, "ru" if system.lower().startswith("russian") else system)
    return _active


def configure_language(app, value):
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    global _active, _preference
    _preference = normalize_preference(value)
    system = QLocale.system()
    languages = system.uiLanguages()
    _active = resolve_language(_preference, languages[0] if languages else system.name())
    old = getattr(app, "_studio_translator", None)
    if old:
        app.removeTranslator(old)
    app._studio_translator = None
    if _active == "ru":
        translator = QTranslator(app)
        folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load("qtbase_ru", folder):
            app.installTranslator(translator)
            app._studio_translator = translator
    return _active


def tr(source, *values):
    text = MESSAGES.get(source, source) if _active == "en" else source
    return text.format(*values) if values else text
