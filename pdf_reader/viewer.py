"""Visualização contínua do PDF com renderização das páginas visíveis."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


RENDER_SCALE = 2.0
PAGE_GAP = 24.0


class PdfPageView(QGraphicsView):
    page_clicked = Signal(int, QPointF)
    visible_page_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#e8ebef"))
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._fit_width = True
        self._highlight = None
        self._search_highlight = None
        self._dark_theme = False
        self._pdf_path: Path | None = None
        self._page_rects: list[QRectF] = []
        self._page_items = {}
        self._rendered: set[int] = set()
        self._current_page = -1
        self._render_scheduled = False
        self.verticalScrollBar().valueChanged.connect(self._schedule_visible_pages)
        self.horizontalScrollBar().valueChanged.connect(self._schedule_visible_pages)

    def load_pdf(self, pdf_path: Path) -> None:
        pdf_path = Path(pdf_path)
        if self._pdf_path == pdf_path:
            return
        self.clear_pdf()
        self._pdf_path = pdf_path
        with pymupdf.open(pdf_path) as pdf:
            sizes = [(page.rect.width * RENDER_SCALE, page.rect.height * RENDER_SCALE) for page in pdf]
        canvas_width = max((width for width, _ in sizes), default=0)
        y = PAGE_GAP
        for width, height in sizes:
            rect = QRectF((canvas_width - width) / 2, y, width, height)
            self._page_rects.append(rect)
            background = self.scene().addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(Qt.GlobalColor.white))
            background.setZValue(-2)
            y += height + PAGE_GAP
        self.scene().setSceneRect(QRectF(0, 0, canvas_width, y))
        if self._fit_width:
            self.fit_width()
        self.show_page(0)

    def clear_pdf(self) -> None:
        self._pdf_path = None
        self.scene().clear()
        self._page_rects.clear()
        self._page_items.clear()
        self._rendered.clear()
        self._highlight = None
        self._search_highlight = None
        self._current_page = -1

    def set_dark_theme(self, enabled: bool) -> None:
        self._dark_theme = enabled
        self.setBackgroundBrush(QColor("#20242d" if enabled else "#e8ebef"))

    def show_page(self, page_index: int) -> None:
        if not 0 <= page_index < len(self._page_rects):
            return
        rect = self._page_rects[page_index]
        scale = max(self.transform().m22(), 0.01)
        self.centerOn(rect.center().x(), rect.top() + self.viewport().height() / (2 * scale))
        self._schedule_visible_pages()

    def fit_width(self) -> None:
        if not self._page_rects:
            return
        self._fit_width = True
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        width = max(rect.width() for rect in self._page_rects)
        factor = max(0.1, (self.viewport().width() - 36) / width)
        self.scale(factor, factor)
        if self._current_page >= 0:
            self.centerOn(center)
        self._schedule_visible_pages()

    def zoom(self, factor: float) -> None:
        if not self._page_rects:
            return
        current = self.transform().m11()
        if 0.12 <= current * factor <= 3.0:
            self._fit_width = False
            self.scale(factor, factor)
            self._schedule_visible_pages()

    def set_highlight(self, page_index: int | None, bbox: tuple[float, float, float, float] | None,
                      *, reveal: bool = True) -> None:
        if self._highlight is not None:
            self.scene().removeItem(self._highlight)
            self._highlight = None
        if page_index is None or bbox is None or not 0 <= page_index < len(self._page_rects):
            return
        page = self._page_rects[page_index]
        x0, y0, x1, y1 = bbox
        rect = QRectF(page.left() + x0 * RENDER_SCALE, page.top() + y0 * RENDER_SCALE,
                      (x1 - x0) * RENDER_SCALE, (y1 - y0) * RENDER_SCALE)
        self._highlight = self.scene().addRect(
            rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor(255, 215, 0, 105))
        )
        self._highlight.setZValue(-1)
        if reveal:
            self.ensureVisible(rect, 30, 45)

    def set_search_highlight(self, page_index: int | None,
                             bbox: tuple[float, float, float, float] | None) -> None:
        if self._search_highlight is not None:
            self.scene().removeItem(self._search_highlight)
            self._search_highlight = None
        if page_index is None or bbox is None or not 0 <= page_index < len(self._page_rects):
            return
        page = self._page_rects[page_index]
        x0, y0, x1, y1 = bbox
        rect = QRectF(page.left() + x0 * RENDER_SCALE, page.top() + y0 * RENDER_SCALE,
                      (x1 - x0) * RENDER_SCALE, (y1 - y0) * RENDER_SCALE)
        self._search_highlight = self.scene().addRect(
            rect, QPen(QColor("#d97706"), 2), QBrush(QColor(255, 170, 0, 115))
        )
        self._search_highlight.setZValue(-0.5)
        self.ensureVisible(rect, 40, 60)

    def _schedule_visible_pages(self) -> None:
        if not self._render_scheduled:
            self._render_scheduled = True
            QTimer.singleShot(0, self._update_visible_pages)

    def _update_visible_pages(self) -> None:
        self._render_scheduled = False
        if self._pdf_path is None or not self._pdf_path.is_file() or not self._page_rects:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        current = min(range(len(self._page_rects)),
                      key=lambda index: abs(self._page_rects[index].center().y() - visible.center().y()))
        if current != self._current_page:
            self._current_page = current
            self.visible_page_changed.emit(current + 1)
        active = {i for i, rect in enumerate(self._page_rects)
                  if rect.bottom() >= visible.top() - visible.height()
                  and rect.top() <= visible.bottom() + visible.height()}
        for index in self._rendered - active:
            self.scene().removeItem(self._page_items.pop(index))
        self._rendered.intersection_update(active)
        missing = sorted(active - self._rendered)
        if not missing:
            return
        with pymupdf.open(self._pdf_path) as pdf:
            for index in missing:
                image = pdf[index].get_pixmap(
                    matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=True
                )
                pixmap = QPixmap()
                pixmap.loadFromData(image.tobytes("png"))
                item = self.scene().addPixmap(pixmap)
                item.setPos(self._page_rects[index].topLeft())
                item.setZValue(0)
                self._page_items[index] = item
                self._rendered.add(index)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            for index, rect in enumerate(self._page_rects):
                if rect.contains(point):
                    self.page_clicked.emit(index + 1, QPointF(
                        (point.x() - rect.left()) / RENDER_SCALE,
                        (point.y() - rect.top()) / RENDER_SCALE,
                    ))
                    break
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_width:
            self.fit_width()
        else:
            self._schedule_visible_pages()
