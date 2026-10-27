"""Colors, fonts, icons and the application style sheet.

Three skins share one set of color tokens and one style sheet template:

- fresh:  bright and friendly, green accent, filled hero card, pill buttons
- tech:   dark, cyan accent, dense cards (always dark)
- fluent: Windows 11 look, blue accent, flat with a left indicator in the sidebar

`fresh` and `fluent` follow the Windows light/dark setting.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

THEMES = ("fresh", "tech", "fluent")

_SEMANTIC_LIGHT = {"safe": "#0E9F5B", "safe_soft": "#E3F6EC", "review": "#D97706", "review_soft": "#FDF1E1",
                   "danger": "#E5484D", "danger_soft": "#FDECEC"}
_SEMANTIC_DARK = {"safe": "#3DD68C", "safe_soft": "#13301F", "review": "#F5A524", "review_soft": "#352713",
                  "danger": "#FF6369", "danger_soft": "#3A1A1C"}

PALETTES: dict[str, dict[str, dict[str, str]]] = {
    "fresh": {
        "light": {
            "bg": "#F2F8F5", "sidebar": "#FFFFFF", "surface": "#FFFFFF", "surface2": "#F7FBF9", "hover": "#EAF5F0",
            "border": "#DCEBE4", "text": "#10302A", "muted": "#4F6A62", "faint": "#6C847C",
            "accent": "#0E8F6A", "accent_hover": "#0B7A5A", "accent_soft": "#DFF3EB", "on_accent": "#FFFFFF",
            "bar": "#E1EDE8", "hero_bg": "#0E8F6A", "hero_text": "#FFFFFF", "hero_muted": "#C9EEDF",
            "ring_track": "#2FA982", "ring_arc": "#B9F3DE", **_SEMANTIC_LIGHT,
        },
        "dark": {
            "bg": "#0E1916", "sidebar": "#12201C", "surface": "#15241F", "surface2": "#1A2C26", "hover": "#1F342D",
            "border": "#263D35", "text": "#E3F2EC", "muted": "#A9C3BA", "faint": "#839E94",
            "accent": "#2CC490", "accent_hover": "#4BD6A4", "accent_soft": "#173B30", "on_accent": "#052019",
            "bar": "#243A33", "hero_bg": "#13453A", "hero_text": "#E3F2EC", "hero_muted": "#A6D8C6",
            "ring_track": "#1F5E4E", "ring_arc": "#4BE0AC", **_SEMANTIC_DARK,
        },
    },
    "tech": {
        "dark": {
            "bg": "#0E131D", "sidebar": "#0A0E16", "surface": "#151C29", "surface2": "#19212F", "hover": "#1C2536",
            "border": "#232D40", "text": "#E3E9F2", "muted": "#9AA5B8", "faint": "#7A869A",
            "accent": "#4FD1FF", "accent_hover": "#7ADDFF", "accent_soft": "#16314A", "on_accent": "#06202D",
            "bar": "#232D40", "hero_bg": "#151C29", "hero_text": "#E3E9F2", "hero_muted": "#8C97AA",
            "ring_track": "#232D40", "ring_arc": "#4FD1FF",
            "safe": "#3DDC97", "safe_soft": "#12322A", "review": "#FFB547", "review_soft": "#3A2C14",
            "danger": "#FF6B6B", "danger_soft": "#3D1C22",
        },
    },
    "fluent": {
        "light": {
            "bg": "#F9F9F9", "sidebar": "#F3F3F3", "surface": "#FFFFFF", "surface2": "#FBFBFB", "hover": "#F0F0F0",
            "border": "#E5E5E5", "text": "#1B1B1B", "muted": "#5C5C5C", "faint": "#767676",
            "accent": "#005FB8", "accent_hover": "#0A6CC6", "accent_soft": "#EAEAEA", "on_accent": "#FFFFFF",
            "bar": "#E6E6E6", "hero_bg": "#FFFFFF", "hero_text": "#1B1B1B", "hero_muted": "#616161",
            "ring_track": "#E6E6E6", "ring_arc": "#005FB8", **_SEMANTIC_LIGHT,
        },
        "dark": {
            "bg": "#272727", "sidebar": "#202020", "surface": "#2D2D2D", "surface2": "#323232", "hover": "#383838",
            "border": "#3A3A3A", "text": "#FFFFFF", "muted": "#C5C5C5", "faint": "#9A9A9A",
            "accent": "#60CDFF", "accent_hover": "#7AD6FF", "accent_soft": "#3A3A3A", "on_accent": "#000000",
            "bar": "#3F3F3F", "hero_bg": "#2D2D2D", "hero_text": "#FFFFFF", "hero_muted": "#C5C5C5",
            "ring_track": "#3F3F3F", "ring_arc": "#60CDFF", **_SEMANTIC_DARK,
        },
    },
}

# Shape and layout choices per skin.
STYLES: dict[str, dict] = {
    "fresh": {"card_radius": 16, "ctrl_radius": 14, "nav": "pill", "hero": "filled", "finding": "circle",
              "sidebar_border": False},
    "tech": {"card_radius": 12, "ctrl_radius": 8, "nav": "soft", "hero": "card", "finding": "plain",
             "sidebar_border": True},
    "fluent": {"card_radius": 8, "ctrl_radius": 4, "nav": "bar", "hero": "card", "finding": "plain",
               "sidebar_border": False},
}

# Skin previews for the theme picker: (sidebar, page, accent, card)
PREVIEWS = {
    "fresh": ("#FFFFFF", "#F2F8F5", "#0E8F6A", "#FFFFFF"),
    "tech": ("#0A0E16", "#0E131D", "#4FD1FF", "#151C29"),
    "fluent": ("#F3F3F3", "#F9F9F9", "#005FB8", "#FFFFFF"),
}

CATEGORY_COLORS = {"system": "#2F6BFF", "browser": "#0BA5C7", "ai": "#7A5AF8", "dev": "#E04F9A", "apps": "#F08C00"}

# Segoe MDL2 Assets / Segoe Fluent Icons code points
GLYPHS = {
    "home": "", "clean": "", "ai": "", "software": "", "startup": "",
    "memory": "", "settings": "", "folder": "", "refresh": "", "search": "",
    "copy": "", "info": "", "warning": "", "check": "", "close": "",
    "system": "", "browser": "", "dev": "", "apps": "", "shield": "",
    "scan": "", "history": "", "disk": "", "palette": "", "chevron": "",
}

C: dict[str, str] = dict(PALETTES["fresh"]["light"])
S: dict = dict(STYLES["fresh"])
NAME = "fresh"
_icon_font: str | None = None


def is_dark() -> bool:
    """Whether Windows is in dark mode (not the skin; see `dark_skin`)."""
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def luminance(color) -> float:
    """WCAG relative luminance, 0 (black) .. 1 (white)."""
    c = QColor(color)

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def dark_skin() -> bool:
    """Whether the skin in use has a dark background."""
    return luminance(C["bg"]) < 0.3


def text_on(background) -> QColor:
    """Near-black or white, whichever reads better on `background`."""
    dark, light = QColor("#14171C"), QColor("#FFFFFF")
    return dark if contrast(dark, background) >= contrast(light, background) else light


def _find_icon_font() -> str | None:
    families = set(QFontDatabase.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in families:
            return name
    return None


def ui_font() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC",
                      "Noto Sans CJK SC", "Noto Sans"])
    font.setPointSizeF(9.5)
    return font


def glyph_pixmap(name: str, color: str, size: int = 18) -> QPixmap:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    if _icon_font and name in GLYPHS:
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        font = QFont(_icon_font)
        font.setPixelSize(int(size * 0.85))
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, GLYPHS[name])
        p.end()
    return pm


def nav_selected_text() -> str:
    return {"pill": C["on_accent"], "soft": C["accent"], "bar": C["text"]}[S["nav"]]


def icon(name: str, color: str | None = None, selected_color: str | None = None, size: int = 18) -> QIcon:
    if not _icon_font:
        return QIcon()
    ic = QIcon()
    ic.addPixmap(glyph_pixmap(name, color or C["muted"], size), QIcon.Normal)
    ic.addPixmap(glyph_pixmap(name, selected_color or color or C["accent"], size), QIcon.Selected)
    ic.addPixmap(glyph_pixmap(name, C["faint"], size), QIcon.Disabled)
    return ic


def apply(app: QApplication, name: str = "fresh") -> None:
    """Switch every token to the `name` skin and restyle the whole app."""
    global _icon_font, NAME
    NAME = name if name in THEMES else "fresh"
    variants = PALETTES[NAME]
    mode = "dark" if (is_dark() or "light" not in variants) and "dark" in variants else "light"
    C.clear()
    C.update(variants[mode])
    S.clear()
    S.update(STYLES[NAME])
    _icon_font = _find_icon_font()
    app.setStyle("Fusion")
    app.setFont(ui_font())
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(C["bg"]))
    pal.setColor(QPalette.WindowText, QColor(C["text"]))
    pal.setColor(QPalette.Base, QColor(C["surface"]))
    pal.setColor(QPalette.AlternateBase, QColor(C["surface2"]))
    pal.setColor(QPalette.Text, QColor(C["text"]))
    pal.setColor(QPalette.Button, QColor(C["surface"]))
    pal.setColor(QPalette.ButtonText, QColor(C["text"]))
    pal.setColor(QPalette.Highlight, QColor(C["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(C["on_accent"]))
    pal.setColor(QPalette.ToolTipBase, QColor(C["surface"]))
    pal.setColor(QPalette.ToolTipText, QColor(C["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(C["faint"]))
    pal.setColor(QPalette.Mid, QColor(C["border"]))
    # Fusion draws checkbox frames with these; keep them visible in dark skins.
    pal.setColor(QPalette.Dark, QColor(C["faint"]))
    pal.setColor(QPalette.Shadow, QColor(C["faint"]))
    pal.setColor(QPalette.Light, QColor(C["surface2"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())


def _nav_css(c: dict, s: dict) -> str:
    if s["nav"] == "pill":
        sel = f"background: {c['accent']}; color: {c['on_accent']};"
        item = "border-radius: 12px;"
    elif s["nav"] == "soft":
        sel = f"background: {c['accent_soft']}; color: {c['accent']};"
        item = "border-radius: 8px;"
    else:  # fluent: grey highlight with an accent bar on the left
        sel = f"background: {c['accent_soft']}; color: {c['text']}; border-left: 3px solid {c['accent']};"
        item = "border-radius: 4px; border-left: 3px solid transparent;"
    return f"""
QListWidget#nav::item {{ padding: 10px 12px; margin: 2px 10px; {item} color: {c['muted']}; }}
QListWidget#nav::item:hover:!selected {{ background: {c['hover']}; color: {c['text']}; }}
QListWidget#nav::item:selected {{ {sel} font-weight: 600; }}"""


def _hero_css(c: dict, s: dict) -> str:
    r = s["card_radius"] + 2
    if s["hero"] != "filled":
        return f"QFrame#hero {{ background: {c['hero_bg']}; border: 1px solid {c['border']}; border-radius: {r}px; }}"
    return f"""
QFrame#hero {{ background: {c['hero_bg']}; border: none; border-radius: {r}px; }}
QFrame#hero QLabel {{ color: {c['hero_text']}; background: transparent; }}
QFrame#hero QLabel#muted, QFrame#hero QLabel#subtitle, QFrame#hero QLabel#faint {{ color: {c['hero_muted']}; }}
QFrame#hero QLabel#section {{ color: {c['hero_text']}; }}
QFrame#hero QPushButton {{ background: transparent; color: {c['hero_text']}; border: 1px solid {c['hero_muted']}; }}
QFrame#hero QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}
QFrame#hero QPushButton#primary {{ background: #FFFFFF; color: {c['accent']}; border: none; }}
QFrame#hero QPushButton#primary:hover {{ background: {c['hero_muted']}; }}
QFrame#hero QProgressBar {{ background: {c['ring_track']}; }}
QFrame#hero QProgressBar::chunk {{ background: {c['ring_arc']}; }}"""


def stylesheet() -> str:
    c, s = C, S
    card, ctrl = s["card_radius"], s["ctrl_radius"]
    side_border = f"border-right: 1px solid {c['border']};" if s["sidebar_border"] else "border: none;"
    return f"""
QMainWindow, QWidget#page, QDialog {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}

QWidget#sidebar {{ background: {c['sidebar']}; {side_border} }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; }}
{_nav_css(c, s)}
QLabel#brand {{ font-size: 13pt; font-weight: 700; }}
QLabel#brandSub {{ color: {c['faint']}; font-size: 8.5pt; }}

QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {card}px; }}
{_hero_css(c, s)}
QFrame#banner {{ background: {c['review_soft']}; border-radius: {min(card, 10)}px; }}
QFrame#divider {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#themeCard {{ background: {c['surface']}; border: 2px solid {c['border']}; border-radius: {card}px; }}
QFrame#themeCard[selected="true"] {{ border: 2px solid {c['accent']}; }}

QLabel#title {{ font-size: 17pt; font-weight: 700; }}
QLabel#subtitle {{ color: {c['muted']}; }}
QLabel#section {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#faint {{ color: {c['faint']}; font-size: 8.5pt; }}
QLabel#huge {{ font-size: 30pt; font-weight: 700; }}
QLabel#big {{ font-size: 18pt; font-weight: 700; }}
QLabel#detailTitle {{ font-size: 13pt; font-weight: 700; }}
QLabel#badge_safe {{ background: {c['safe_soft']}; color: {c['safe']}; border-radius: 9px; padding: 2px 10px;
                     font-weight: 600; font-size: 8.5pt; }}
QLabel#badge_review {{ background: {c['review_soft']}; color: {c['review']}; border-radius: 9px; padding: 2px 10px;
                       font-weight: 600; font-size: 8.5pt; }}
QLabel#badge_info {{ background: {c['hover']}; color: {c['muted']}; border-radius: 9px; padding: 2px 10px;
                     font-weight: 600; font-size: 8.5pt; }}
QLabel#badge_danger {{ background: {c['danger_soft']}; color: {c['danger']}; border-radius: 9px; padding: 2px 10px;
                       font-weight: 600; font-size: 8.5pt; }}
QLabel#path {{ color: {c['muted']}; font-family: "Cascadia Mono", Consolas, monospace; font-size: 8.5pt; }}

QPushButton {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {ctrl}px;
               padding: 7px 14px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['surface2']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['on_accent']}; border: none; font-weight: 600;
                       padding: 8px 18px; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {c['bar']}; color: {c['faint']}; }}
QPushButton#hero {{ background: {c['accent']}; color: {c['on_accent']}; border: none; font-weight: 700;
                    font-size: 11pt; padding: 11px 26px; border-radius: {max(ctrl, 6)}px; }}
QPushButton#hero:hover {{ background: {c['accent_hover']}; }}
QPushButton#hero:disabled {{ background: {c['bar']}; color: {c['faint']}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {c['accent']}; padding: 6px 8px; }}
QPushButton#ghost:hover {{ background: {c['accent_soft']}; }}
QPushButton#danger {{ background: {c['danger']}; color: white; border: none; font-weight: 600; padding: 8px 18px; }}

QLineEdit, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {min(ctrl, 10)}px;
                        padding: 6px 10px; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']};
                               selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}

QTreeWidget, QTableWidget, QListWidget#plain {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: {min(card, 12)}px; outline: 0; alternate-background-color: {c['surface2']}; }}
QTreeWidget::item {{ padding: 7px 2px; border: none; }}
QTableWidget::item {{ padding: 4px 2px; border: none; }}
QTreeWidget::item:hover, QTableWidget::item:hover {{ background: {c['hover']}; }}
QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 8px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{ background: {c['bar']}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 9px 16px; margin-right: 6px;
                border: none; border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
QSplitter::handle {{ background: transparent; width: 12px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QCheckBox {{ spacing: 8px; }}
"""
