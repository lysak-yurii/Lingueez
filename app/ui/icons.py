"""Embedded vector icon set (Feather-style, stroke-based SVG).

icon(name, color) renders an SVG at HiDPI into a QIcon, recolorable per
theme. No binary assets, no emoji.
"""
import os

from PySide6.QtCore import QByteArray, QRectF, Qt, QSize
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

ICONS = {
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "sync": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>'
            '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 '
            '5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "star-filled": '<polygon fill="{color}" points="12 2 15.09 8.26 22 9.27 17 14.14 '
                   '18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "tag": '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59'
           'a2 2 0 0 1 0 2.83z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
    "edit": '<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/>'
             '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 '
             '2-2h4a2 2 0 0 1 2 2v2"/>'
             '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "volume": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
              '<path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2" fill="{color}" stroke="none"/>',
    "book-open": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
                 '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
            '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
                 '<polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/>'
                 '<line x1="16" y1="17" x2="8" y2="17"/>',
    "archive": '<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/>'
               '<line x1="10" y1="12" x2="14" y2="12"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
               '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
               '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
               '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>'
               '<line x1="17" y1="16" x2="23" y2="16"/>',
    "list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>'
            '<line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/>'
            '<line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "sparkles": '<path d="M12 2l1.7 6.3L20 10l-6.3 1.7L12 18l-1.7-6.3L4 10l6.3-1.7z"/>'
                '<path d="M19 15l.85 3.15L23 19l-3.15.85L19 23l-.85-3.15L15 19l3.15-.85z"/>',
    "swap": '<path d="M7 16V4"/><polyline points="3 8 7 4 11 8"/>'
            '<path d="M17 8v12"/><polyline points="13 16 17 20 21 16"/>',
    "filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron-left": '<polyline points="15 18 9 12 15 6"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "menu": '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/>'
            '<line x1="3" y1="18" x2="21" y2="18"/>',
    "play": '<polygon points="5 3 19 12 5 21 5 3" fill="{color}" stroke="none"/>',
    "pause": '<rect x="6" y="5" width="4" height="14" rx="1" fill="{color}" stroke="none"/>'
             '<rect x="14" y="5" width="4" height="14" rx="1" fill="{color}" stroke="none"/>',
    "skip-back": '<polygon points="19 20 9 12 19 4 19 20" fill="{color}" stroke="none"/>'
                 '<line x1="5" y1="19" x2="5" y2="5"/>',
    "skip-forward": '<polygon points="5 4 15 12 5 20 5 4" fill="{color}" stroke="none"/>'
                    '<line x1="19" y1="5" x2="19" y2="19"/>',
    "cloud": '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 '
             '1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
             '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
              '<polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "more": '<circle cx="12" cy="12" r="1.6" fill="{color}" stroke="none"/>'
            '<circle cx="19" cy="12" r="1.6" fill="{color}" stroke="none"/>'
            '<circle cx="5" cy="12" r="1.6" fill="{color}" stroke="none"/>',
    "rows": '<rect x="3" y="4" width="18" height="6" rx="1"/>'
            '<rect x="3" y="14" width="18" height="6" rx="1"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>'
             '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 '
             '15.3 15.3 0 0 1 4-10z"/>',
    "help-circle": '<circle cx="12" cy="12" r="10"/>'
                   '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
                   '<line x1="12" y1="17" x2="12.01" y2="17"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
           '<circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 '
               '18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 '
               '18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>'
               '<line x1="1" y1="1" x2="23" y2="23"/>',
    "win-min": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "win-max": '<rect x="5" y="5" width="14" height="14" rx="1.5"/>',
    "win-restore": '<rect x="5" y="9" width="10" height="10" rx="1.5"/>'
                   '<path d="M9 9V6.5A1.5 1.5 0 0 1 10.5 5H17.5A1.5 1.5 0 0 1 19 6.5V13.5'
                   'A1.5 1.5 0 0 1 17.5 15H15"/>',
}

_cache = {}


def svg_bytes(name, color):
    body = ICONS[name].replace("{color}", color)
    return _SVG_TEMPLATE.format(color=color, body=body).encode()


def pixmap(name, color, size=20, dpr=2.0):
    key = (name, color, size, dpr)
    if key in _cache:
        return _cache[key]
    renderer = QSvgRenderer(QByteArray(svg_bytes(name, color)))
    image = QImage(int(size * dpr), int(size * dpr), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * dpr, size * dpr))
    painter.end()
    pm = QPixmap.fromImage(image)
    pm.setDevicePixelRatio(dpr)
    _cache[key] = pm
    return pm


def icon(name, color, size=20):
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size))
    return ic


def write_qss_icons(colors, directory="assets/generated"):
    """Write SVG files needed by the stylesheet (e.g. combo arrows)."""
    os.makedirs(directory, exist_ok=True)
    paths = {}
    for name, color_key in [("chevron-down", "text_dim"), ("check", "accent")]:
        path = os.path.join(directory, f"{name}.svg")
        with open(path, "wb") as fh:
            fh.write(svg_bytes(name, colors[color_key]))
        paths[name] = path.replace(os.sep, "/")
    return paths
