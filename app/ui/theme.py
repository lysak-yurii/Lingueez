"""Modern Qt theme: dark / light palettes + application-wide QSS."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


DARK = {
    "bg": "#101418",
    "bg_alt": "#161b22",
    "surface": "#1c232b",
    "surface_alt": "#222b35",
    "border": "#2c3640",
    "text": "#e6edf3",
    "text_dim": "#8b98a5",
    "accent": "#4f8cff",
    "accent_hover": "#6ba1ff",
    "accent_pressed": "#3a72e0",
    "danger": "#e5534b",
    "success": "#3fb950",
    "warning": "#d29922",
    "favorite": "#2b3a55",
    "row_alt": "#151b22",
    "selection": "#2d4f8a",
    "accent_soft": "#1f3050",
    "accent_soft_hover": "#26395f",
    "accent_soft_pressed": "#1a2940",
    "accent_text": "#7fb0ff",
}

LIGHT = {
    "bg": "#f4f6f9",
    "bg_alt": "#ffffff",
    "surface": "#ffffff",
    "surface_alt": "#eef1f5",
    "border": "#d4dae1",
    "text": "#1c2733",
    "text_dim": "#5f6f7f",
    "accent": "#2f6fed",
    "accent_hover": "#4a84f2",
    "accent_pressed": "#2459c4",
    "danger": "#d6453d",
    "success": "#2da44e",
    "warning": "#bf8700",
    "favorite": "#dbe7ff",
    "row_alt": "#f2f5f9",
    "selection": "#cfe0ff",
    "accent_soft": "#e2ebfc",
    "accent_soft_hover": "#d2e1fa",
    "accent_soft_pressed": "#c1d4f7",
    "accent_text": "#2257c5",
}


TABLE_DENSITY = {
    "Compact":     {"scale": 0.9, "row_ratio": 2.9},
    "Normal":      {"scale": 1.0, "row_ratio": 3.2},
    "Comfortable": {"scale": 1.2, "row_ratio": 3.3},
    "Spacious":    {"scale": 1.4, "row_ratio": 3.6},
}
TABLE_DENSITY_DEFAULT = "Normal"

_current_colors = None


def current_colors():
    """Colors of the theme most recently applied via apply_theme()."""
    return _current_colors or DARK


def resolve_mode(mode):
    """Map a settings appearance_mode value to 'dark' or 'light'."""
    mode = (mode or "System").strip().lower()
    if mode == "dark":
        return "dark"
    if mode == "light":
        return "light"
    # System: follow the desktop color scheme when Qt can tell us
    try:
        from PySide6.QtGui import QGuiApplication
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    except Exception:
        return "dark"


def palette_colors(mode):
    return DARK if resolve_mode(mode) == "dark" else LIGHT


def build_qss(c, base_font_size=10, icon_paths=None):
    icon_paths = icon_paths or {}
    chevron = icon_paths.get("chevron-down", "")
    return f"""
* {{
    font-family: "Inter", "Segoe UI", "Noto Sans", "Ubuntu", sans-serif;
    font-size: {base_font_size}pt;
}}
QMainWindow, QDialog {{
    background: {c['bg']};
}}
QWidget {{
    color: {c['text']};
}}
#HeaderBar {{
    background: {c['bg_alt']};
    border-bottom: 1px solid {c['border']};
}}
#AppTitle {{
    font-size: {base_font_size + 4}pt;
    font-weight: 700;
    color: {c['text']};
}}
#SubTitle {{
    color: {c['text_dim']};
    font-size: {base_font_size - 1}pt;
}}
QLabel {{
    background: transparent;
}}
QLabel#dimLabel {{
    color: {c['text_dim']};
}}
QToolTip {{
    background: {c['surface_alt']};
    color: {c['text']};
    border: 1px solid {c['border']};
    padding: 5px 8px;
    border-radius: 4px;
}}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {c['selection']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {c['accent']};
}}
QLineEdit#SearchBox {{
    border-radius: 16px;
    padding: 7px 14px;
    font-size: {base_font_size + 1}pt;
}}
QComboBox {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 5px 10px;
    min-height: 20px;
}}
QComboBox:hover {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: url("{chevron}");
    width: 14px;
    height: 14px;
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    selection-background-color: {c['selection']};
    selection-color: {c['text']};
    outline: 0;
}}
QComboBox[filterActive="true"] {{
    border: 1px solid {c['accent']};
    background: {c['selection']};
}}
QComboBox#headerFilter {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 2px 4px 2px 6px;
    font-weight: 600;
    color: {c['text_dim']};
}}
QComboBox#headerFilter:hover {{ background: {c['surface_alt']}; color: {c['text']}; }}
QComboBox#headerFilter[filterActive="true"] {{
    color: {c['accent']};
    background: {c['selection']};
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {c['surface_alt']}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['bg_alt']}; }}
QPushButton:disabled {{ color: {c['text_dim']}; border-color: {c['border']}; }}
QPushButton#primaryButton {{
    background: {c['accent']};
    border: none;
    color: white;
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background: {c['accent_hover']}; }}
QPushButton#primaryButton:pressed {{ background: {c['accent_pressed']}; }}
QPushButton#tonalButton {{
    background: {c['accent_soft']};
    border: none;
    color: {c['accent_text']};
    font-weight: 600;
}}
QPushButton#tonalButton:hover {{ background: {c['accent_soft_hover']}; }}
QPushButton#tonalButton:pressed {{ background: {c['accent_soft_pressed']}; }}
QPushButton#dangerButton {{
    background: transparent;
    border: 1px solid {c['danger']};
    color: {c['danger']};
}}
QPushButton#dangerButton:hover {{ background: {c['danger']}; color: white; }}
QPushButton#iconButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px;
    font-size: {base_font_size + 3}pt;
    min-width: 30px;
}}
QPushButton#iconButton:hover {{ background: {c['surface_alt']}; }}
QPushButton#iconButton:checked {{ background: {c['selection']}; }}
QPushButton#chipButton {{
    border-radius: 14px;
    padding: 5px 14px;
}}
QPushButton#chipButton:checked {{
    background: {c['accent']};
    color: white;
    border-color: {c['accent']};
}}

/* ---------- table ---------- */
QTableView {{
    background: {c['bg_alt']};
    alternate-background-color: {c['row_alt']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    gridline-color: transparent;
    selection-background-color: {c['selection']};
    selection-color: {c['text']};
    outline: 0;
}}
QTableView::item {{
    padding: 4px 8px;
    border: none;
}}
QTableView::item:hover {{
    background: {c['surface_alt']};
}}
QTableView::item:selected {{
    background: {c['selection']};
}}
QHeaderView::section {{
    background: {c['bg_alt']};
    color: {c['text_dim']};
    font-weight: 600;
    border: none;
    border-bottom: 2px solid {c['border']};
    padding: 8px 8px;
}}
QHeaderView::section:hover {{ color: {c['text']}; }}
QTableCornerButton::section {{ background: {c['bg_alt']}; border: none; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 10px;
    top: -1px;
    background: {c['bg_alt']};
}}
QTabBar::tab {{
    background: transparent;
    color: {c['text_dim']};
    border: none;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    color: {c['text']};
    background: {c['bg_alt']};
    border: 1px solid {c['border']};
    border-bottom: none;
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{ color: {c['text']}; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {c['border']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['text_dim']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {c['border']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- menus ---------- */
QMenuBar {{
    background: {c['bg_alt']};
    border-bottom: 1px solid {c['border']};
    padding: 2px 6px;
}}
QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 6px;
    background: transparent;
    color: {c['text']};
}}
QMenuBar::item:selected {{ background: {c['surface_alt']}; color: {c['text']}; }}
QMenu {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 26px 7px 14px;
    border-radius: 6px;
    color: {c['text']};
}}
QMenu::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['text_dim']}; }}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 5px 10px;
}}

/* ---------- misc ---------- */
QStatusBar {{
    background: {c['bg_alt']};
    border-top: 1px solid {c['border']};
    color: {c['text_dim']};
}}
QStatusBar::item {{ border: none; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {c['border']};
    border-radius: 4px;
    background: {c['surface']};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {c['accent']};
    border-color: {c['accent']};
}}
QProgressBar {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    text-align: center;
    height: 16px;
}}
QProgressBar::chunk {{
    background: {c['accent']};
    border-radius: 7px;
}}
QSplitter::handle {{ background: {c['border']}; }}
QGroupBox {{
    border: 1px solid {c['border']};
    border-radius: 10px;
    margin-top: 12px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {c['text_dim']};
}}
#ToastFrame {{
    border-radius: 10px;
    border: 1px solid {c['border']};
    background: {c['surface_alt']};
}}
#WordPopup {{
    background: transparent;  /* the pill paints its own rounded body */
}}
#WordPopupText {{
    font-weight: 600;
    background: transparent;
}}
#SyncPopover {{
    background: transparent;  /* the bubble paints its own rounded body */
}}
#SyncPopover QLabel {{
    background: transparent;
}}

/* ---------- app shell ---------- */
#Sidebar {{
    background: {c['bg_alt']};
    border-right: 1px solid {c['border']};
}}
#Sidebar QPushButton {{
    background: transparent;
    border: none;
    border-radius: 10px;
    padding: 9px;
    margin: 2px 8px;
}}
#Sidebar QPushButton:hover {{ background: {c['surface_alt']}; }}
#Sidebar QPushButton:checked {{ background: {c['selection']}; }}
#TopBar {{
    background: {c['bg_alt']};
    border-bottom: 1px solid {c['border']};
}}
#ActionBar {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
#ActionBar QPushButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    color: {c['text']};
}}
#ActionBar QPushButton:hover {{ background: {c['surface_alt']}; }}
#ActionBar QLabel {{ color: {c['text_dim']}; }}
#PlayerBar {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
#PlayerBar QPushButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 8px;
}}
#PlayerBar QPushButton:hover {{ background: {c['surface_alt']}; }}
#PlayerBar QLabel#PlayerWord {{
    font-weight: 600;
    color: {c['text']};
    padding-left: 4px;
}}
#Footer {{
    background: {c['bg_alt']};
    border-top: 1px solid {c['border']};
}}
QPushButton#navButton {{
    text-align: left;
}}

/* ---------- texts page ---------- */
#TextsList {{
    background: {c['bg_alt']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 4px 2px;
    outline: 0;
}}
#TextsList::item {{ background: transparent; border: none; }}
#ReaderCard {{
    background: {c['bg_alt']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
QLineEdit#ReaderTitle {{
    font-size: {base_font_size + 3}pt;
    font-weight: 700;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 4px 8px;
}}
QLineEdit#ReaderTitle:hover {{ background: {c['surface']}; }}
QLineEdit#ReaderTitle:focus {{ background: {c['surface']}; border: 1px solid {c['accent']}; }}
QTextEdit#ReaderBody {{
    background: transparent;
    border: none;
    font-size: {base_font_size + 1}pt;
    padding: 0px;
}}
#EmptyTitle {{
    font-size: {base_font_size + 3}pt;
    font-weight: 600;
    color: {c['text']};
}}

/* ---------- window controls (client-side decorations) ---------- */
QPushButton#winBtn, QPushButton#winBtnClose {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 7px 13px;
}}
QPushButton#winBtn:hover {{ background: {c['surface_alt']}; }}
QPushButton#winBtnClose:hover {{ background: {c['danger']}; }}
QMainWindow {{
    border: 1px solid {c['border']};
}}
QDialog {{
    border: 1px solid {c['border']};
}}
#DialogTitleBar {{
    background: {c['bg_alt']};
    border-bottom: 1px solid {c['border']};
}}
#DialogTitle {{
    font-weight: 600;
    color: {c['text']};
}}
QLineEdit#BigInput {{
    font-size: {base_font_size + 3}pt;
    padding: 8px 14px;
    min-height: {base_font_size * 3}px;
    border: 1px solid {c['border']};
    border-radius: 10px;
    background: {c['bg']};
}}
QLineEdit#BigInput:focus {{
    border: 1px solid {c['accent']};
}}
#LangCard {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
#LangCard QComboBox {{
    border: none;
    background: transparent;
    font-weight: 600;
    padding-left: 2px;
}}
#CardFrame {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
#CardFrame:hover {{ border-color: {c['accent']}; }}
"""


def apply_theme(app: QApplication, mode="System", scaling=1.0):
    """Apply palette + QSS. Returns the resolved color dict."""
    global _current_colors
    c = palette_colors(mode)
    _current_colors = c
    base_font_size = max(8, round(10 * scaling))

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c['bg']))
    pal.setColor(QPalette.WindowText, QColor(c['text']))
    pal.setColor(QPalette.Base, QColor(c['surface']))
    pal.setColor(QPalette.AlternateBase, QColor(c['row_alt']))
    pal.setColor(QPalette.Text, QColor(c['text']))
    pal.setColor(QPalette.Button, QColor(c['surface']))
    pal.setColor(QPalette.ButtonText, QColor(c['text']))
    pal.setColor(QPalette.Highlight, QColor(c['selection']))
    pal.setColor(QPalette.HighlightedText, QColor(c['text']))
    pal.setColor(QPalette.ToolTipBase, QColor(c['surface_alt']))
    pal.setColor(QPalette.ToolTipText, QColor(c['text']))
    pal.setColor(QPalette.PlaceholderText, QColor(c['text_dim']))
    pal.setColor(QPalette.Link, QColor(c['accent']))
    pal.setColor(QPalette.LinkVisited, QColor(c['accent']))
    app.setPalette(pal)

    from app.ui import icons
    icon_paths = icons.write_qss_icons(c)
    app.setStyleSheet(build_qss(c, base_font_size, icon_paths))
    return c
