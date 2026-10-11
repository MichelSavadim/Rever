"""Interface do leitor de PDF e reprodução TTS."""

from __future__ import annotations

import os
import json
import time
from pathlib import Path
from threading import Event, Thread, current_thread

import pymupdf
from PySide6.QtCore import QEasingCurve, QObject, QSettings, QSize, QTimer, Qt, QUrl, QVariantAnimation, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)

from .core import (
    VOICES, Book, GenerationCancelled, Library, Paragraph, audio_path,
    clear_all_temporary, clear_temporary,
    export_audio, load_paragraphs, playable_audio_path, run_generation,
    write_json,
)
from . import diagnostics
from .paths import data_root, resource_root
from .timing import DurationTimeline
from .timing import is_speakable
from .viewer import PdfPageView


PROJECT_ROOT = data_root()
RESOURCE_ROOT = resource_root()
ICON_ROOT = Path(__file__).resolve().parent / "assets" / "icons"


def clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class PreferencesDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language = QComboBox()
        self.language.addItems(VOICES)
        self.voice = QComboBox()
        form.addRow("Idioma da voz", self.language)
        form.addRow("Voz", self.voice)
        layout.addLayout(form)
        layout.addWidget(QLabel("O Edge TTS requer internet para gerar novos áudios."))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.language.currentTextChanged.connect(self.populate_voices)
        language = settings.value("language", "Português (Brasil)")
        self.language.setCurrentText(language if language in VOICES else "Português (Brasil)")
        self.populate_voices(self.language.currentText())
        index = self.voice.findData(settings.value("voice", "pt-BR-FranciscaNeural"))
        self.voice.setCurrentIndex(max(index, 0))

    def populate_voices(self, language: str) -> None:
        self.voice.clear()
        for name, code in VOICES[language].items():
            self.voice.addItem(name, code)


class GenerationWorker(QObject):
    progress = Signal(int, int)
    ready = Signal(int)
    completed = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self, book: Book, entries: list[tuple[int, Paragraph]], voice: str,
        temporary: bool, export_label: str | None = None,
    ):
        super().__init__()
        self.book = book
        self.entries = entries
        self.voice = voice
        self.temporary = temporary
        self.export_label = export_label
        self.cancelled = Event()
        self.created_audio_paths: set[Path] = set()

    @Slot()
    def run(self) -> None:
        try:
            total = len(self.entries)
            for count, (index, paragraph) in enumerate(self.entries, 1):
                if self.cancelled.is_set():
                    break
                target = audio_path(self.book, self.voice, paragraph)
                existed_before = target.is_file() and target.stat().st_size > 0
                run_generation(
                    self.book, [paragraph], self.voice, lambda _n, _t: None,
                    temporary=self.temporary, cancelled=self.cancelled.is_set,
                )
                if not self.temporary and not existed_before and target.is_file():
                    self.created_audio_paths.add(target)
                if self.cancelled.is_set():
                    break
                self.progress.emit(count, total)
                self.ready.emit(index)
            target = None
            if not self.cancelled.is_set() and self.export_label:
                target = export_audio(
                    self.book, [paragraph for _, paragraph in self.entries],
                    self.voice, self.export_label, cancelled=self.cancelled.is_set,
                )
            self.completed.emit(str(target) if target else "")
        except GenerationCancelled:
            self.completed.emit("")
        except Exception as exc:
            diagnostics.log_exception(type(exc), exc, exc.__traceback__, "Erro na geração de áudio")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rever")
        self.resize(1370, 850)
        self.settings = QSettings("PDFReaderTTS", "PDFReaderTTS")
        library_root = Path(os.environ.get("PDF_READER_LIBRARY", PROJECT_ROOT / "data" / "library"))
        self.library = Library(library_root)
        if not os.environ.get("PDF_READER_LIBRARY"):
            old = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PDFReaderTTS" / "library"
            self.library.migrate_from(old)
        clear_all_temporary(self.library)
        self.book: Book | None = None
        self.paragraphs: list[Paragraph] = []
        self.timeline: DurationTimeline | None = None
        self.current_page = 1
        self.selected_index: int | None = None
        self.resume_index: int | None = None
        self.search_results: list[tuple[int, tuple[float, float, float, float]]] = []
        self.search_query = ""
        self.search_index = -1
        self.track_index = -1
        self.current_audio: Path | None = None
        self.pending_position: int | None = None
        self.pending_play: tuple[str, int, int, bool] | None = None
        self.dragging = False
        self.session_ms = 0
        self.session_since: float | None = None
        self.thread: Thread | None = None
        self.worker: GenerationWorker | None = None
        self.generation_mode: str | None = None
        self.generation_book: Book | None = None
        self.queued_export: str | None = None
        self.pending_removal: tuple[Book, bool] | None = None
        self.delete_created_on_cancel = False
        self.generation_failed = False
        self.export_started_at: float | None = None
        self.export_weights: list[float] = []
        self.export_completed_count = 0
        self.export_result_path: Path | None = None
        self.closing = False
        self.last_export: Path | None = None
        self.left_width = 230
        self.right_width = 300
        self.panel_animations: dict[str, QVariantAnimation] = {}
        self.panel_targets: dict[str, bool] = {}
        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.errorOccurred.connect(self.on_player_error)
        self.player.playbackStateChanged.connect(self.on_playback_state)
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(200)
        self.session_timer.timeout.connect(self.update_session_clock)
        self.session_timer.start()
        self.export_timer = QTimer(self)
        self.export_timer.setInterval(1000)
        self.export_timer.timeout.connect(self.update_export_times)
        self.build_ui()
        self.load_books(self.library.last_book_id())
        QTimer.singleShot(0, lambda: self.horizontal_splitter.setSizes([230, 820, 300]))
        QTimer.singleShot(0, lambda: self.vertical_splitter.setSizes([640, 150]))

    def build_ui(self) -> None:
        self.build_menus()
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        heading = QHBoxLayout()
        self.title_label = QLabel("Selecione um PDF")
        self.title_label.setObjectName("title")
        heading.addWidget(self.title_label, 1)
        import_button = QPushButton("+ Importar PDF")
        import_button.clicked.connect(self.import_pdf)
        heading.addWidget(import_button)
        outer.addLayout(heading)
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setHandleWidth(7)
        outer.addWidget(self.vertical_splitter, 1)
        self.horizontal_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.horizontal_splitter.setHandleWidth(7)
        self.vertical_splitter.addWidget(self.horizontal_splitter)

        self.left_wrapper = QWidget()
        self.left_wrapper.setMinimumWidth(30)
        left_box = QVBoxLayout(self.left_wrapper)
        left_box.setContentsMargins(0, 0, 0, 0)
        self.left_panel = QFrame()
        self.left_panel.setObjectName("panel")
        left_panel_box = QVBoxLayout(self.left_panel)
        left_header = QHBoxLayout()
        left_header.addWidget(QLabel("Biblioteca"), 1)
        self.collapse_left_button = QPushButton("▼")
        self.collapse_left_button.setToolTip("Ocultar biblioteca")
        self.collapse_left_button.setFixedWidth(30)
        self.collapse_left_button.clicked.connect(lambda: self.set_panel_visible("left", False))
        left_header.addWidget(self.collapse_left_button)
        left_panel_box.addLayout(left_header)
        self.book_list = QListWidget()
        self.book_list.setIconSize(QPixmap(90, 112).size())
        self.book_list.currentItemChanged.connect(self.select_book)
        left_panel_box.addWidget(self.book_list)
        self.left_clip = QScrollArea()
        self.left_clip.setFrameShape(QFrame.Shape.NoFrame)
        self.left_clip.setWidgetResizable(True)
        self.left_clip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_clip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_clip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.left_clip.setMinimumWidth(0)
        self.left_clip.setWidget(self.left_panel)
        left_box.addWidget(self.left_clip)
        self.left_collapsed = QPushButton("◀")
        self.left_collapsed.setToolTip("Mostrar biblioteca")
        self.left_collapsed.setFixedSize(30, 30)
        self.left_collapsed.clicked.connect(lambda: self.set_panel_visible("left", True))
        self.left_collapsed_holder = QWidget()
        left_collapsed_row = QHBoxLayout(self.left_collapsed_holder)
        left_collapsed_row.setContentsMargins(3, 9, 0, 0)
        left_collapsed_row.addWidget(self.left_collapsed)
        left_collapsed_row.addStretch()
        self.left_collapsed_holder.hide()
        left_box.addWidget(self.left_collapsed_holder, 0, Qt.AlignmentFlag.AlignTop)
        self.horizontal_splitter.addWidget(self.left_wrapper)

        viewer_panel = QWidget()
        viewer_box = QVBoxLayout(viewer_panel)
        viewer_box.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        for label, callback in (
            ("−", lambda: self.viewer.zoom(0.8)),
            ("+", lambda: self.viewer.zoom(1.25)),
            ("Ajustar largura", self.viewer_fit),
        ):
            button = QPushButton(label)
            if label == "Ajustar largura":
                button.setToolTip("Ajusta o zoom para a página caber na largura do leitor")
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addSpacing(12)
        previous_page_button = QPushButton("◀")
        previous_page_button.clicked.connect(lambda: self.show_page(self.current_page - 1))
        toolbar.addWidget(previous_page_button)
        toolbar.addWidget(QLabel("Página"))
        self.viewer_page_input = QLineEdit()
        self.viewer_page_input.setFixedWidth(48)
        self.viewer_page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewer_page_input.setToolTip("Digite uma página e pressione Enter para visualizá-la sem iniciar o áudio")
        self.viewer_page_input.returnPressed.connect(self.goto_viewer_page)
        toolbar.addWidget(self.viewer_page_input)
        self.viewer_page_total = QLabel("/ —")
        toolbar.addWidget(self.viewer_page_total)
        viewer_go_button = QPushButton("Ir")
        viewer_go_button.clicked.connect(self.goto_viewer_page)
        toolbar.addWidget(viewer_go_button)
        next_page_button = QPushButton("▶")
        next_page_button.clicked.connect(lambda: self.show_page(self.current_page + 1))
        toolbar.addWidget(next_page_button)
        toolbar.addStretch()
        self.select_button = QPushButton("Selecionar parágrafo")
        self.select_button.setCheckable(True)
        self.select_button.setToolTip("Clique em um parágrafo da página para iniciar o áudio nele")
        self.select_button.toggled.connect(self.on_selection_mode)
        toolbar.addWidget(self.select_button)
        viewer_box.addLayout(toolbar)

        search_row = QHBoxLayout()
        search_row.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Pesquisar no PDF")
        self.search_input.setToolTip("Pesquise texto neste PDF (Ctrl+F)")
        self.search_input.setMinimumWidth(130)
        self.search_input.setMaximumWidth(230)
        self.search_input.textChanged.connect(self.clear_search_results)
        self.search_input.returnPressed.connect(self.search_next)
        search_row.addWidget(self.search_input)
        search_previous_button = QPushButton("◀")
        search_previous_button.setToolTip("Resultado anterior")
        search_previous_button.clicked.connect(self.search_previous)
        search_row.addWidget(search_previous_button)
        search_next_button = QPushButton("▶")
        search_next_button.setToolTip("Próximo resultado")
        search_next_button.clicked.connect(self.search_next)
        search_row.addWidget(search_next_button)
        self.search_count = QLabel("")
        search_row.addWidget(self.search_count)
        viewer_box.addLayout(search_row)
        self.viewer = PdfPageView()
        self.viewer.page_clicked.connect(self.on_page_clicked)
        self.viewer.visible_page_changed.connect(self.on_visible_page_changed)
        viewer_box.addWidget(self.viewer, 1)
        self.horizontal_splitter.addWidget(viewer_panel)

        self.right_wrapper = QWidget()
        self.right_wrapper.setMinimumWidth(30)
        right_box = QVBoxLayout(self.right_wrapper)
        right_box.setContentsMargins(0, 0, 0, 0)
        self.right_panel = QFrame()
        self.right_panel.setObjectName("panel")
        right_panel_box = QVBoxLayout(self.right_panel)
        right_header = QHBoxLayout()
        right_header.addWidget(QLabel("Opções do PDF"), 1)
        self.collapse_right_button = QPushButton("▼")
        self.collapse_right_button.setToolTip("Ocultar opções")
        self.collapse_right_button.setFixedWidth(30)
        self.collapse_right_button.clicked.connect(lambda: self.set_panel_visible("right", False))
        right_header.addWidget(self.collapse_right_button)
        right_panel_box.addLayout(right_header)
        self.info_label = QLabel("Nenhum PDF selecionado")
        self.info_label.setWordWrap(True)
        right_panel_box.addWidget(self.info_label)
        self.full_button = QPushButton("Gerar MP3 completo")
        self.full_button.clicked.connect(lambda: self.start_export("full"))
        right_panel_box.addWidget(self.full_button)
        right_panel_box.addWidget(QLabel("Gerar MP3 por intervalo de páginas"))
        range_row = QHBoxLayout()
        self.range_start = QSpinBox()
        self.range_end = QSpinBox()
        for spin in (self.range_start, self.range_end):
            spin.setMinimum(1)
            spin.valueChanged.connect(self.update_range_count)
        range_row.addWidget(QLabel("De"))
        range_row.addWidget(self.range_start)
        range_row.addWidget(QLabel("até"))
        range_row.addWidget(self.range_end)
        right_panel_box.addLayout(range_row)
        self.range_count = QLabel("0 páginas selecionadas")
        right_panel_box.addWidget(self.range_count)
        self.range_button = QPushButton("Gerar MP3 do intervalo")
        self.range_button.clicked.connect(lambda: self.start_export("range"))
        right_panel_box.addWidget(self.range_button)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        right_panel_box.addWidget(self.progress)
        self.cancel_export_button = QPushButton("Cancelar geração")
        self.cancel_export_button.hide()
        self.cancel_export_button.clicked.connect(self.cancel_export)
        right_panel_box.addWidget(self.cancel_export_button)
        self.export_elapsed_label = QLabel("Tempo decorrido: 00:00:00")
        self.export_remaining_label = QLabel("Tempo estimado de conclusão: —")
        for label in (self.export_elapsed_label, self.export_remaining_label):
            label.hide()
            right_panel_box.addWidget(label)
        self.status_label = QLabel("Pronto")
        self.status_label.setWordWrap(True)
        right_panel_box.addWidget(self.status_label)
        right_panel_box.addWidget(QLabel("Último MP3 gerado"))
        self.export_path = QLineEdit()
        self.export_path.setReadOnly(True)
        self.export_path.setPlaceholderText("Nenhum arquivo gerado")
        right_panel_box.addWidget(self.export_path)
        self.open_export_button = QPushButton("Abrir arquivo gerado")
        self.open_export_button.clicked.connect(self.open_export)
        right_panel_box.addWidget(self.open_export_button)
        folder_button = QPushButton("Abrir pasta do PDF")
        folder_button.clicked.connect(self.open_book_folder)
        right_panel_box.addWidget(folder_button)
        right_panel_box.addStretch()
        self.right_clip = QScrollArea()
        self.right_clip.setFrameShape(QFrame.Shape.NoFrame)
        self.right_clip.setWidgetResizable(True)
        self.right_clip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_clip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_clip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.right_clip.setMinimumWidth(0)
        self.right_clip.setWidget(self.right_panel)
        right_box.addWidget(self.right_clip)
        self.right_collapsed = QPushButton("▶")
        self.right_collapsed.setToolTip("Mostrar opções")
        self.right_collapsed.setFixedSize(30, 30)
        self.right_collapsed.clicked.connect(lambda: self.set_panel_visible("right", True))
        self.right_collapsed_holder = QWidget()
        right_collapsed_row = QHBoxLayout(self.right_collapsed_holder)
        right_collapsed_row.setContentsMargins(0, 9, 3, 0)
        right_collapsed_row.addStretch()
        right_collapsed_row.addWidget(self.right_collapsed)
        self.right_collapsed_holder.hide()
        right_box.addWidget(self.right_collapsed_holder, 0, Qt.AlignmentFlag.AlignTop)
        self.horizontal_splitter.addWidget(self.right_wrapper)
        self.horizontal_splitter.setStretchFactor(1, 1)

        self.player_panel = QFrame()
        self.player_panel.setObjectName("panel")
        player_box = QVBoxLayout(self.player_panel)
        self.now_playing = QLabel("Selecione um PDF para ouvir")
        playing_row = QHBoxLayout()
        playing_row.addWidget(self.now_playing, 1)
        self.session_label = QLabel("Ouvido: 00:00:00")
        playing_row.addWidget(self.session_label)
        player_box.addLayout(playing_row)
        timeline = QHBoxLayout()
        self.elapsed = QLabel("00:00:00")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setToolTip("Posição estimada na narração do PDF inteiro")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "dragging", True))
        self.seek_slider.sliderReleased.connect(self.seek_from_slider)
        self.total = QLabel("00:00:00")
        self.total.setToolTip("A duração estimada é corrigida conforme os áudios são gerados")
        timeline.addWidget(self.elapsed)
        timeline.addWidget(self.seek_slider, 1)
        timeline.addWidget(self.total)
        player_box.addLayout(timeline)
        controls = QHBoxLayout()
        self.transport_icons = {name: QIcon(str(ICON_ROOT / f"{name}.svg")) for name in (
            "play", "pause", "back_10", "forward_10", "previous_paragraph",
            "next_paragraph", "previous_page", "next_page",
        )}
        for icon_name, description, callback in (
            ("previous_page", "Voltar uma página", self.previous_page_audio),
            ("previous_paragraph", "Voltar um parágrafo", self.previous_paragraph),
            ("back_10", "Voltar 10 segundos", lambda: self.seek_delta(-10000)),
            ("play", "Reproduzir", self.toggle_play),
            ("forward_10", "Avançar 10 segundos", lambda: self.seek_delta(10000)),
            ("next_paragraph", "Avançar um parágrafo", self.next_paragraph),
            ("next_page", "Avançar uma página", self.next_page_audio),
        ):
            button = QPushButton()
            button.setObjectName("transportButton")
            button.setIcon(self.transport_icons[icon_name])
            button.setIconSize(QSize(22, 22))
            button.setFixedSize(42, 36)
            button.setToolTip(description)
            button.setAccessibleName(description)
            button.clicked.connect(callback)
            controls.addWidget(button)
            if icon_name == "play":
                self.play_pause_button = button
        controls.addStretch()
        controls.addWidget(QLabel("Ir à página"))
        self.goto_input = QLineEdit()
        self.goto_input.setFixedWidth(55)
        self.goto_input.returnPressed.connect(self.goto_page)
        controls.addWidget(self.goto_input)
        goto_button = QPushButton("Ir")
        goto_button.clicked.connect(self.goto_page)
        controls.addWidget(goto_button)
        controls.addWidget(QLabel("Velocidade"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0,75×", "1×", "1,25×", "1,5×", "2×"])
        self.speed_combo.setCurrentText("1×")
        self.speed_combo.currentTextChanged.connect(self.set_speed)
        controls.addWidget(self.speed_combo)
        player_box.addLayout(controls)
        self.vertical_splitter.addWidget(self.player_panel)
        self.vertical_splitter.setStretchFactor(0, 1)
        self.apply_theme(self.theme_action.isChecked())
        self.set_export_enabled(False)
        self.remove_book_action.setEnabled(False)

    def build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("Arquivo")
        import_action = file_menu.addAction("Adicionar PDF à biblioteca")
        import_action.setShortcut("Ctrl+O")
        import_action.triggered.connect(self.import_pdf)
        self.remove_book_action = file_menu.addAction("Excluir PDF selecionado da biblioteca…")
        self.remove_book_action.triggered.connect(self.remove_selected_book)
        folder_action = file_menu.addAction("Abrir pasta da biblioteca")
        folder_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root)))
        )
        file_menu.addSeparator()
        file_menu.addAction("Sair", self.close)
        settings_menu = self.menuBar().addMenu("Configurações")
        settings_action = settings_menu.addAction("Preferências de voz…")
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.open_preferences)
        settings_menu.addAction("Atalhos de teclado…", self.show_shortcuts)
        play_shortcut = QAction(self)
        play_shortcut.setShortcut("Ctrl+Space")
        play_shortcut.triggered.connect(self.toggle_play)
        self.addAction(play_shortcut)
        select_shortcut = QAction(self)
        select_shortcut.setShortcut("Ctrl+L")
        select_shortcut.triggered.connect(
            lambda: self.select_button.setChecked(not self.select_button.isChecked())
        )
        self.addAction(select_shortcut)
        search_shortcut = QAction(self)
        search_shortcut.setShortcut("Ctrl+F")
        search_shortcut.triggered.connect(self.focus_search)
        self.addAction(search_shortcut)
        view_menu = self.menuBar().addMenu("Exibir")
        self.theme_action = view_menu.addAction("Tema escuro")
        self.theme_action.setCheckable(True)
        self.theme_action.setChecked(self.settings.value("dark_theme", False, type=bool))
        self.theme_action.toggled.connect(self.set_dark_theme)
        view_menu.addSeparator()
        self.left_action = view_menu.addAction("Biblioteca")
        self.right_action = view_menu.addAction("Opções do PDF")
        self.player_action = view_menu.addAction("Player de áudio")
        for action in (self.left_action, self.right_action, self.player_action):
            action.setCheckable(True)
            action.setChecked(True)
        self.left_action.toggled.connect(lambda visible: self.set_panel_visible("left", visible))
        self.right_action.toggled.connect(lambda visible: self.set_panel_visible("right", visible))
        self.player_action.toggled.connect(self.set_player_visible)
        help_menu = self.menuBar().addMenu("Ajuda")
        help_menu.addAction("Sobre / README", self.open_readme)

    def set_panel_visible(self, side: str, visible: bool) -> None:
        panel, collapsed_holder, action, index = (
            (self.left_clip, self.left_collapsed_holder, self.left_action, 0)
            if side == "left" else
            (self.right_clip, self.right_collapsed_holder, self.right_action, 2)
        )
        current_animation = self.panel_animations.pop(side, None)
        if current_animation is not None:
            if self.panel_targets.get(side) == visible:
                self.panel_animations[side] = current_animation
                return
            current_animation.stop()
            current_animation.deleteLater()
        elif panel.isHidden() != visible:
            return
        sizes = self.horizontal_splitter.sizes()
        start_width = sizes[index]
        if not visible:
            if side == "left":
                self.left_width = max(180, start_width)
            else:
                self.right_width = max(220, start_width)
        target_width = (self.left_width if side == "left" else self.right_width) if visible else 36
        collapsed_holder.hide()
        panel.show()
        panel.setMaximumWidth(max(start_width, 36))
        if action.isChecked() != visible:
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)
        self.panel_targets[side] = visible
        total_width = sum(sizes)
        other_index = 2 if index == 0 else 0
        other_width = sizes[other_index]
        animation = QVariantAnimation(self)
        animation.setDuration(220)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.setStartValue(start_width)
        animation.setEndValue(target_width)

        def resize_panel(width: int) -> None:
            width = int(width)
            panel.setMaximumWidth(max(width, 36))
            updated = self.horizontal_splitter.sizes()
            updated[index] = width
            updated[other_index] = other_width
            updated[1] = max(1, total_width - width - other_width)
            self.horizontal_splitter.setSizes(updated)

        def finish_panel() -> None:
            if self.panel_animations.get(side) is not animation:
                return
            self.panel_animations.pop(side, None)
            resize_panel(target_width)
            if visible:
                panel.setMaximumWidth(16777215)
            else:
                panel.hide()
                panel.setMaximumWidth(16777215)
                collapsed_holder.show()
                updated = self.horizontal_splitter.sizes()
                updated[index] = target_width
                updated[other_index] = other_width
                updated[1] = max(1, total_width - target_width - other_width)
                self.horizontal_splitter.setSizes(updated)
            animation.deleteLater()

        animation.valueChanged.connect(resize_panel)
        animation.finished.connect(finish_panel)
        self.panel_animations[side] = animation
        animation.start()

    def set_player_visible(self, visible: bool) -> None:
        self.player_panel.setVisible(visible)

    def set_export_enabled(self, enabled: bool) -> None:
        self.full_button.setEnabled(enabled)
        self.range_button.setEnabled(enabled)

    def viewer_fit(self) -> None:
        self.viewer.fit_width()

    def goto_viewer_page(self) -> None:
        if not self.book:
            return
        try:
            page = int(self.viewer_page_input.text())
        except ValueError:
            QMessageBox.warning(self, "Página inválida", "Digite um número de página.")
            return
        if not 1 <= page <= self.book.page_count:
            QMessageBox.warning(self, "Página inválida", f"Escolha uma página entre 1 e {self.book.page_count}.")
            return
        self.show_page(page)

    def focus_search(self) -> None:
        if self.book:
            self.search_input.setFocus()
            self.search_input.selectAll()

    def clear_search_results(self) -> None:
        self.search_query = ""
        self.search_results = []
        self.search_index = -1
        self.search_count.setText("")
        self.viewer.set_search_highlight(None, None)

    def search_next(self) -> None:
        self.navigate_search(1)

    def search_previous(self) -> None:
        self.navigate_search(-1)

    def navigate_search(self, direction: int) -> None:
        if not self.book:
            return
        query = self.search_input.text().strip()
        if not query:
            self.focus_search()
            return
        if query != self.search_query:
            self.search_query = query
            self.search_results = []
            self.search_index = -1
            try:
                with pymupdf.open(self.book.pdf_path) as pdf:
                    for page_index, page in enumerate(pdf):
                        for rect in page.search_for(query):
                            self.search_results.append((page_index, tuple(rect)))
            except Exception as exc:
                diagnostics.log_exception(type(exc), exc, exc.__traceback__, "Erro na pesquisa do PDF")
                self.search_count.setText("Erro na pesquisa")
                return
        if not self.search_results:
            self.search_count.setText("0 resultados")
            self.viewer.set_search_highlight(None, None)
            return
        self.search_index = (self.search_index + direction) % len(self.search_results)
        page_index, bbox = self.search_results[self.search_index]
        self.show_page(page_index + 1)
        self.viewer.set_search_highlight(page_index, bbox)
        self.search_count.setText(f"{self.search_index + 1}/{len(self.search_results)}")

    def apply_theme(self, dark: bool) -> None:
        if dark:
            background, foreground, panel, border = "#191d24", "#eef2f7", "#242a34", "#414b59"
            button, hover, checked, selected = "#303946", "#3b4d65", "#355579", "#31557c"
            muted = "#9ba8b7"
        else:
            background, foreground, panel, border = "#f6f7f9", "#192334", "#ffffff", "#dfe4ea"
            button, hover, checked, selected = "#e9edf2", "#dce8f8", "#c8dcfa", "#dbeafe"
            muted = "#777f89"
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {background}; color: {foreground}; font-size: 13px; }}
            QFrame#panel {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
            QFrame#panel QWidget {{ background: {panel}; }}
            QLabel#title {{ font-size: 17px; font-weight: 600; }}
            QPushButton {{ background: {button}; border: 1px solid {border};
                          border-radius: 5px; padding: 6px 9px; }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:checked {{ background: {checked}; border-color: #6a9cdb; }}
            QPushButton:disabled {{ color: {muted}; }}
            QPushButton#transportButton {{ background: #355579; border-color: #274766; }}
            QPushButton#transportButton:hover {{ background: #4673a1; }}
            QLineEdit, QSpinBox, QComboBox {{ background: {panel}; color: {foreground};
                                            border: 1px solid {border}; padding: 3px; }}
            QListWidget {{ border: none; background: {panel}; }}
            QListWidget::item {{ padding: 7px; }}
            QListWidget::item:selected {{ background: {selected}; color: {foreground}; }}
            QMenuBar, QMenu {{ background: {panel}; color: {foreground}; }}
            QMenu::item:selected {{ background: {selected}; }}
            QSplitter::handle {{ background: {border}; }}
            QProgressBar {{ background: {panel}; color: {foreground}; border: 1px solid {border}; }}
            QProgressBar::chunk {{ background: #3b82f6; }}
        """)
        self.viewer.set_dark_theme(dark)

    def set_dark_theme(self, enabled: bool) -> None:
        self.settings.setValue("dark_theme", enabled)
        self.apply_theme(enabled)

    def load_books(self, selected_id: str | None = None) -> None:
        self.book_list.blockSignals(True)
        self.book_list.clear()
        chosen = None
        for book in self.library.books():
            item = QListWidgetItem(book.title)
            item.setData(Qt.ItemDataRole.UserRole, book)
            item.setToolTip(f"{book.title}\n{book.page_count} páginas")
            item.setIcon(QIcon(str(book.thumbnail_path)))
            self.book_list.addItem(item)
            if book.id == selected_id:
                chosen = item
        self.book_list.blockSignals(False)
        if chosen:
            self.book_list.setCurrentItem(chosen)
        elif self.book_list.count():
            self.book_list.setCurrentRow(0)

    def import_pdf(self) -> None:
        if self.generation_mode == "export":
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Importar PDF", "", "Arquivos PDF (*.pdf)")
        if not filename:
            return
        try:
            book = self.library.import_pdf(Path(filename))
        except Exception as exc:
            diagnostics.log_exception(type(exc), exc, exc.__traceback__, "Erro ao importar PDF")
            QMessageBox.warning(self, "Não foi possível importar", str(exc))
            return
        self.load_books(book.id)

    def confirm_remove_book(self, book: Book) -> str:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Excluir PDF da biblioteca")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f"Como deseja excluir “{book.title}”?")
        dialog.setInformativeText(
            "Excluir apenas da biblioteca mantém o PDF importado, os áudios e o ponto de leitura "
            "na pasta do projeto. Importar o mesmo PDF novamente recupera esses arquivos. "
            "Excluir todos os arquivos remove a pasta inteira deste PDF."
        )
        keep_button = dialog.addButton("Excluir apenas da biblioteca", QMessageBox.ButtonRole.AcceptRole)
        delete_button = dialog.addButton(
            "Excluir da biblioteca e todos os arquivos", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() == keep_button:
            return "keep"
        if dialog.clickedButton() == delete_button:
            return "delete"
        return "cancel"

    def remove_selected_book(self) -> None:
        if not self.book or self.pending_removal:
            return
        book = self.book
        choice = self.confirm_remove_book(book)
        if choice == "cancel" or self.book != book:
            return
        delete_files = choice == "delete"
        if self.thread:
            self.pending_removal = (book, delete_files)
            self.pending_play = None
            self.queued_export = None
            if self.worker:
                self.worker.cancelled.set()
            self.book_list.setEnabled(False)
            self.remove_book_action.setEnabled(False)
            self.status_label.setText("Aguardando a geração parar para excluir o PDF…")
            return
        self.finish_remove_book(book, delete_files)

    def finish_remove_book(self, book: Book, delete_files: bool) -> None:
        if self.book != book:
            return
        self.persist_reader_state()
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_audio = None
        self.pending_play = None
        self.viewer.clear_pdf()
        self.search_input.clear()
        self.clear_search_results()
        self.book = None
        self.paragraphs = []
        self.timeline = None
        self.selected_index = None
        self.resume_index = None
        self.track_index = -1
        self.current_page = 1
        self.set_export_path(None)
        self.set_export_enabled(False)
        self.remove_book_action.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        try:
            self.library.remove(book, delete_files=delete_files)
        except Exception as exc:
            diagnostics.log_exception(type(exc), exc, exc.__traceback__, "Erro ao excluir PDF")
            QMessageBox.warning(self, "Não foi possível excluir", str(exc))
            self.load_books(book.id)
            return
        if not delete_files:
            self.cleanup_book(book)
        diagnostics.record(f"PDF excluído da biblioteca: {book.id}, arquivos apagados={delete_files}")
        self.title_label.setText("Selecione um PDF")
        self.info_label.setText("")
        self.viewer_page_input.clear()
        self.viewer_page_total.setText("/ —")
        self.now_playing.setText("Nenhum PDF selecionado")
        self.status_label.setText("PDF excluído da biblioteca.")
        self.load_books()

    def select_book(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        old_book = self.book
        self.persist_reader_state()
        if self.worker and self.generation_mode == "temp":
            self.worker.cancelled.set()
        self.pending_play = None
        self.queued_export = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_audio = None
        if old_book:
            self.cleanup_book(old_book)
        self.book = current.data(Qt.ItemDataRole.UserRole)
        self.search_input.clear()
        self.clear_search_results()
        self.library.set_last_book(self.book.id)
        diagnostics.record(f"PDF selecionado: {self.book.pdf_path}")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("Pronto")
        self.paragraphs = load_paragraphs(self.book)
        self.timeline = DurationTimeline(self.book.folder, self.paragraphs, self.current_voice())
        self.track_index = -1
        saved_page, self.resume_index = self.read_reader_state(self.book)
        self.selected_index = self.resume_index
        self.reset_session_clock()
        self.title_label.setText(self.book.title)
        self.info_label.setText(
            f"{self.book.page_count} páginas · {self.book.paragraph_count} blocos de texto"
        )
        self.range_start.setMaximum(self.book.page_count)
        self.range_end.setMaximum(self.book.page_count)
        self.range_start.setValue(1)
        self.range_end.setValue(self.book.page_count)
        self.goto_input.setPlaceholderText(f"1–{self.book.page_count}")
        self.viewer_page_total.setText(f"/ {self.book.page_count}")
        self.viewer_page_input.setPlaceholderText(f"1–{self.book.page_count}")
        self.show_page(saved_page, keep_selection=True)
        self.now_playing.setText("Pressione Play para gerar o áudio do parágrafo")
        self.update_timeline_ui()
        self.set_export_enabled(self.generation_mode != "export")
        self.remove_book_action.setEnabled(True)
        exports = list((self.book.folder / "exports").glob("*.mp3"))
        self.set_export_path(max(exports, key=lambda path: path.stat().st_mtime) if exports else None)

    def read_reader_state(self, book: Book) -> tuple[int, int | None]:
        try:
            state = json.loads(book.reader_state_path.read_text(encoding="utf-8"))
            page = state.get("page", 1)
            page = page if isinstance(page, int) and 1 <= page <= book.page_count else 1
            saved = state.get("paragraph")
            index = next((i for i, paragraph in enumerate(self.paragraphs)
                          if isinstance(saved, dict) and paragraph.page == saved.get("page")
                          and paragraph.index == saved.get("index")), None)
            return page, index
        except (OSError, ValueError, TypeError, AttributeError):
            return 1, None

    def persist_reader_state(self) -> None:
        if not self.book:
            return
        paragraph = (self.paragraphs[self.resume_index]
                     if self.resume_index is not None and 0 <= self.resume_index < len(self.paragraphs)
                     else None)
        write_json(self.book.reader_state_path, {
            "page": self.current_page,
            "paragraph": {"page": paragraph.page, "index": paragraph.index} if paragraph else None,
        })

    def show_page(self, page: int, *, keep_selection: bool = False) -> None:
        if not self.book or not 1 <= page <= self.book.page_count:
            return
        if not keep_selection:
            self.selected_index = None
        if self.viewer._pdf_path != self.book.pdf_path:
            self.viewer.load_pdf(self.book.pdf_path)
        self.viewer.show_page(page - 1)
        self.current_page = page
        self.viewer_page_input.setText(str(page))
        self.update_highlight()
        self.persist_reader_state()

    def on_visible_page_changed(self, page: int) -> None:
        if self.book:
            self.current_page = page
            self.viewer_page_input.setText(str(page))
            self.persist_reader_state()

    def update_highlight(self) -> None:
        if self.select_button.isChecked() and self.selected_index is not None:
            index = self.selected_index
        elif self.track_index >= 0 and self.current_audio is not None:
            index = self.track_index
        elif self.pending_play and self.book and self.pending_play[0] == self.book.id:
            index = self.pending_play[1]
        elif self.selected_index is not None:
            index = self.selected_index
        else:
            index = None
        bbox = None
        page_index = None
        reveal = False
        if index is not None and 0 <= index < len(self.paragraphs):
            paragraph = self.paragraphs[index]
            page_index = paragraph.page - 1
            bbox = paragraph.bbox
            reveal = paragraph.page == self.current_page
        self.viewer.set_highlight(page_index, bbox, reveal=reveal)

    def on_selection_mode(self, enabled: bool) -> None:
        if enabled and self.track_index >= 0:
            self.selected_index = self.track_index
        if not enabled:
            self.selected_index = None
            if self.track_index < 0:
                self.resume_index = None
            self.persist_reader_state()
        self.update_highlight()

    def on_page_clicked(self, page: int, point) -> None:
        if not self.select_button.isChecked() or not self.book:
            return
        for index, paragraph in enumerate(self.paragraphs):
            if paragraph.page != page or paragraph.bbox is None:
                continue
            x0, y0, x1, y1 = paragraph.bbox
            if x0 <= point.x() <= x1 and y0 <= point.y() <= y1:
                self.player.stop()
                self.pending_play = None
                if self.worker and self.generation_mode == "temp":
                    self.worker.cancelled.set()
                self.track_index = -1
                self.current_audio = None
                self.reset_session_clock()
                self.selected_index = index
                self.resume_index = index
                self.update_timeline_ui()
                self.update_highlight()
                self.persist_reader_state()
                self.now_playing.setText(
                    f"Selecionado: página {paragraph.page} · parágrafo {paragraph.index}"
                )
                return

    def update_range_count(self) -> None:
        count = max(0, self.range_end.value() - self.range_start.value() + 1)
        self.range_count.setText(
            f"{count} página{'s' if count != 1 else ''} selecionada{'s' if count != 1 else ''}"
        )

    def current_voice(self) -> str:
        return self.settings.value("voice", "pt-BR-FranciscaNeural")

    def open_preferences(self) -> None:
        if self.generation_mode == "export":
            return
        dialog = PreferencesDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            old_voice = self.current_voice()
            self.settings.setValue("language", dialog.language.currentText())
            self.settings.setValue("voice", dialog.voice.currentData())
            if old_voice != self.current_voice():
                if self.worker and self.generation_mode == "temp":
                    self.worker.cancelled.set()
                self.pending_play = None
                self.player.stop()
                self.player.setSource(QUrl())
                self.current_audio = None
                self.track_index = -1
                self.reset_session_clock()
                if self.book:
                    self.cleanup_book(self.book)
                    self.timeline = DurationTimeline(self.book.folder, self.paragraphs, self.current_voice())
                    self.update_timeline_ui()
                self.now_playing.setText("Voz alterada. Pressione Play para gerar novo áudio.")

    def start_worker(
        self, entries: list[tuple[int, Paragraph]], *, temporary: bool,
        export_label: str | None = None,
    ) -> None:
        if not self.book or self.thread or not entries:
            return
        self.generation_mode = "temp" if temporary else "export"
        self.generation_book = self.book
        self.generation_failed = False
        self.export_result_path = None
        self.delete_created_on_cancel = False
        diagnostics.record(
            f"Geração iniciada: modo={self.generation_mode}, pdf={self.book.id}, "
            f"voz={self.current_voice()}, trechos={len(entries)}, exportação={export_label}"
        )
        self.worker = GenerationWorker(self.book, entries, self.current_voice(), temporary, export_label)
        self.worker.progress.connect(self.on_generation_progress)
        self.worker.ready.connect(self.on_audio_ready)
        self.worker.completed.connect(self.on_generation_completed)
        self.worker.failed.connect(self.on_generation_failed)
        self.worker.finished.connect(self.on_thread_finished)
        if not temporary:
            self.book_list.setEnabled(False)
            self.set_export_enabled(False)
            self.progress.setRange(0, len(entries))
            self.progress.setValue(0)
            self.cancel_export_button.setEnabled(True)
            self.set_export_activity_visible(True)
            self.export_started_at = time.monotonic()
            self.export_completed_count = 0
            self.export_weights = [self.export_work_weight(index, paragraph)
                                   for index, paragraph in entries]
            self.update_export_times()
            self.export_timer.start()
            self.status_label.setText("Gerando MP3 permanente…")
        self.thread = Thread(target=self.worker.run, name="edge-tts-worker")
        self.thread.start()

    def request_play(self, index: int, position: int = 0, autoplay: bool = True) -> None:
        if not self.book or not 0 <= index < len(self.paragraphs):
            return
        if not is_speakable(self.paragraphs[index].text):
            next_index = self.speakable_index(index + 1)
            if next_index is None:
                self.status_label.setText("Não há mais texto narrável neste PDF.")
                return
            index, position = next_index, 0
        self.resume_index = index
        self.persist_reader_state()
        path = playable_audio_path(self.book, self.current_voice(), self.paragraphs[index])
        if path:
            self.pending_play = None
            self.play_ready(index, path, position, autoplay)
            self.start_buffer(index + 1)
            return
        self.pending_play = (self.book.id, index, position, autoplay)
        self.update_highlight()
        self.status_label.setText(
            f"Gerando áudio da página {self.paragraphs[index].page}, parágrafo {self.paragraphs[index].index}…"
        )
        if self.worker and self.generation_mode == "temp":
            queued_indices = {queued_index for queued_index, _ in self.worker.entries}
            if index not in queued_indices:
                self.worker.cancelled.set()
        if self.thread is None:
            entries = [(index, self.paragraphs[index])]
            next_index = self.speakable_index(index + 1)
            if next_index is not None:
                entries.append((next_index, self.paragraphs[next_index]))
            self.start_worker(entries, temporary=True)

    def speakable_index(self, start: int, direction: int = 1) -> int | None:
        for index in range(start, len(self.paragraphs) if direction > 0 else -1, direction):
            if is_speakable(self.paragraphs[index].text):
                return index
        return None

    def start_buffer(self, index: int) -> None:
        index = self.speakable_index(index)
        if (
            self.book and self.thread is None and index is not None
            and playable_audio_path(self.book, self.current_voice(), self.paragraphs[index]) is None
        ):
            self.start_worker([(index, self.paragraphs[index])], temporary=True)

    def play_ready(self, index: int, path: Path, position: int, autoplay: bool) -> None:
        if not self.book:
            return
        if self.track_index == index and self.current_audio == path:
            self.player.setPosition(position)
            if autoplay:
                self.player.play()
            return
        previous = self.current_audio
        self.track_index = index
        self.resume_index = index
        self.current_audio = path
        if self.timeline:
            self.timeline.record_audio(index, path)
        diagnostics.record(f"Reprodução: pdf={self.book.id}, parágrafo={index + 1}, arquivo={path}")
        self.pending_position = max(0, position)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.setPlaybackRate(self.speed_rate())
        if autoplay:
            self.player.play()
        paragraph = self.paragraphs[index]
        self.now_playing.setText(f"Página {paragraph.page} · Parágrafo {paragraph.index}")
        if self.current_page != paragraph.page:
            self.show_page(paragraph.page, keep_selection=True)
        if self.select_button.isChecked():
            self.selected_index = index
        self.update_highlight()
        self.update_timeline_ui()
        self.persist_reader_state()
        if previous and previous != path and "temp_audio" in previous.parts:
            QTimer.singleShot(250, lambda old=previous: self.remove_temp_file(old))

    def remove_temp_file(self, path: Path, retry: int = 0) -> None:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            if retry < 4:
                QTimer.singleShot(500, lambda: self.remove_temp_file(path, retry + 1))

    def cleanup_book(self, book: Book, retry: int = 0) -> None:
        if not clear_temporary(book) and retry < 20:
            QTimer.singleShot(100, lambda: self.cleanup_book(book, retry + 1))

    def on_audio_ready(self, index: int) -> None:
        if not self.book or self.generation_book != self.book:
            return
        path = playable_audio_path(self.book, self.current_voice(), self.paragraphs[index])
        if path and self.timeline and self.timeline.record_audio(index, path):
            self.update_timeline_ui()
        if self.generation_mode == "temp" and self.pending_play and self.pending_play[:2] == (self.book.id, index):
            _, _, position, autoplay = self.pending_play
            if path:
                self.pending_play = None
                self.play_ready(index, path, position, autoplay)

    def export_work_weight(self, index: int, paragraph: Paragraph) -> float:
        assert self.book is not None
        voice = self.current_voice()
        if audio_path(self.book, voice, paragraph).is_file():
            return 0.1
        if audio_path(self.book, voice, paragraph, temporary=True).is_file():
            return 0.3
        duration = self.timeline.duration_ms(index) if self.timeline else 5000
        return max(1.0, duration / 1000)

    def update_export_times(self) -> None:
        if self.export_started_at is None:
            return
        elapsed = max(0, time.monotonic() - self.export_started_at)
        self.export_elapsed_label.setText(f"Tempo decorrido: {clock(round(elapsed * 1000))}")
        if self.worker and self.worker.cancelled.is_set():
            self.export_remaining_label.setText("Tempo estimado de conclusão: cancelando…")
            return
        done = sum(self.export_weights[:self.export_completed_count])
        remaining = sum(self.export_weights[self.export_completed_count:])
        if done <= 0:
            self.export_remaining_label.setText("Tempo estimado de conclusão: calculando…")
        elif remaining <= 0:
            self.export_remaining_label.setText("Tempo estimado de conclusão: finalizando MP3…")
        else:
            seconds = max(1, round(elapsed * remaining / done))
            self.export_remaining_label.setText(f"Tempo estimado de conclusão: {clock(seconds * 1000)}")

    def set_export_activity_visible(self, visible: bool) -> None:
        for widget in (self.cancel_export_button, self.export_elapsed_label,
                       self.export_remaining_label):
            widget.setVisible(visible)

    def confirm_cancel_export(self) -> str:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Cancelar geração")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText("Deseja mesmo cancelar a geração do MP3?")
        dialog.setInformativeText(
            "Se apagar, apenas os trechos criados nesta geração serão removidos. "
            "Áudios anteriores serão preservados."
        )
        continue_button = dialog.addButton("Continuar geração", QMessageBox.ButtonRole.RejectRole)
        delete_button = dialog.addButton(
            "Cancelar e apagar arquivos gerados", QMessageBox.ButtonRole.DestructiveRole
        )
        preserve_button = dialog.addButton(
            "Cancelar e preservar arquivos gerados", QMessageBox.ButtonRole.AcceptRole
        )
        dialog.setDefaultButton(continue_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == delete_button:
            return "delete"
        if clicked == preserve_button:
            return "keep"
        return "continue"

    def cancel_export(self) -> None:
        if not self.worker or self.generation_mode != "export":
            return
        choice = self.confirm_cancel_export()
        if choice == "continue" or not self.worker or self.generation_mode != "export":
            return
        self.delete_created_on_cancel = choice == "delete"
        self.worker.cancelled.set()
        self.cancel_export_button.setEnabled(False)
        self.status_label.setText("Cancelando após o trecho atual…")
        diagnostics.record(f"Cancelamento da exportação solicitado: {choice}")
        self.update_export_times()

    def remove_created_export_files(self, paths: set[Path], retry: int = 0) -> None:
        pending = set()
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pending.add(path)
        if pending and retry < 20:
            QTimer.singleShot(100, lambda: self.remove_created_export_files(pending, retry + 1))
        elif pending:
            diagnostics.record(f"Não foi possível apagar {len(pending)} trecho(s) da exportação cancelada")

    def on_generation_progress(self, current: int, total: int) -> None:
        if self.generation_mode == "export":
            self.progress.setValue(current)
            self.export_completed_count = current
            self.update_export_times()
            if not (self.worker and self.worker.cancelled.is_set()):
                self.status_label.setText(f"Gerando MP3 permanente: {current}/{total} parágrafos")

    def on_generation_completed(self, export_file: str) -> None:
        if self.closing:
            return
        if self.generation_mode == "export" and export_file:
            self.export_result_path = Path(export_file)
            diagnostics.record(f"Exportação concluída: {export_file}")
            self.set_export_path(self.export_result_path)
            self.status_label.setText("MP3 permanente pronto.")
        elif self.worker and self.worker.cancelled.is_set():
            return
        elif self.generation_mode == "temp" and self.generation_book == self.book:
            self.status_label.setText("Áudio pronto.")

    def on_generation_failed(self, message: str) -> None:
        self.generation_failed = True
        self.pending_play = None
        if self.closing or (self.worker and self.worker.cancelled.is_set()):
            return
        self.status_label.setText("Falha na geração de áudio.")
        QMessageBox.warning(self, "Erro de geração", message)

    @Slot()
    def on_thread_finished(self) -> None:
        # O sinal chega à thread da interface. Esperar o retorno real de run()
        # evita liberar o worker enquanto o callback ainda está em andamento.
        if self.thread is not None and self.thread is not current_thread():
            self.thread.join()
        old_book = self.generation_book
        cancelled = bool(self.worker and self.worker.cancelled.is_set())
        old_mode = self.generation_mode
        failed = self.generation_failed
        export_result = self.export_result_path
        created_paths = set(self.worker.created_audio_paths) if self.worker else set()
        delete_created = self.delete_created_on_cancel
        if old_mode == "export":
            self.export_timer.stop()
            self.set_export_activity_visible(False)
            if cancelled and not export_result and delete_created:
                if self.current_audio in created_paths:
                    self.player.stop()
                    self.player.setSource(QUrl())
                    self.current_audio = None
                    self.track_index = -1
                    self.update_timeline_ui()
                    self.update_highlight()
                self.remove_created_export_files(created_paths)
        self.thread = None
        self.worker = None
        self.generation_mode = None
        self.generation_book = None
        if cancelled and old_book and (
            old_book != self.book or self.closing or self.current_audio is None
        ):
            self.cleanup_book(old_book)
        if self.pending_removal:
            book, delete_files = self.pending_removal
            self.pending_removal = None
            self.finish_remove_book(book, delete_files)
            if self.closing:
                self.close()
            return
        if self.closing:
            clear_all_temporary(self.library)
            self.close()
            return
        self.book_list.setEnabled(True)
        self.set_export_enabled(self.book is not None)
        if old_mode == "export":
            if export_result:
                self.status_label.setText("MP3 permanente pronto.")
            elif cancelled:
                if delete_created:
                    self.status_label.setText("Geração cancelada. Trechos criados nesta tentativa foram apagados.")
                else:
                    self.status_label.setText("Geração cancelada. Trechos concluídos foram preservados.")
        if self.queued_export:
            mode = self.queued_export
            self.queued_export = None
            self.start_export(mode)
            return
        if not failed and self.pending_play and self.book and self.pending_play[0] == self.book.id:
            _, index, position, autoplay = self.pending_play
            self.request_play(index, position, autoplay)
        elif not failed and old_mode == "temp" and self.track_index >= 0:
            self.start_buffer(self.track_index + 1)

    def start_export(self, mode: str) -> None:
        if not self.book:
            return
        if self.thread:
            self.queued_export = mode
            self.status_label.setText("A exportação começará após a geração do buffer.")
            return
        if mode == "range":
            start, end = self.range_start.value(), self.range_end.value()
            if end < start:
                QMessageBox.warning(self, "Intervalo inválido", "A página final deve ser igual ou posterior à inicial.")
                return
            entries = [(i, p) for i, p in enumerate(self.paragraphs)
                       if start <= p.page <= end and is_speakable(p.text)]
            label = f"paginas-{start:04d}-{end:04d}"
        else:
            entries = [(i, p) for i, p in enumerate(self.paragraphs) if is_speakable(p.text)]
            label = "completo"
        if not entries:
            QMessageBox.information(self, "Sem texto", "O intervalo escolhido não contém texto extraível.")
            return
        self.start_worker(entries, temporary=False, export_label=label)

    def set_export_path(self, path: Path | None) -> None:
        self.last_export = path
        self.export_path.setText(str(path) if path else "")
        self.export_path.setToolTip(str(path) if path else "")
        self.open_export_button.setEnabled(bool(path and path.exists()))

    def open_export(self) -> None:
        if self.last_export and self.last_export.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_export)))

    def open_book_folder(self) -> None:
        if self.book:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.book.folder)))

    def open_readme(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(RESOURCE_ROOT / "README.md")))

    def show_shortcuts(self) -> None:
        QMessageBox.information(
            self, "Atalhos de teclado",
            "Ctrl+O — importar PDF\nCtrl+, — preferências de voz\n"
            "Ctrl+Espaço — reproduzir ou pausar\nCtrl+L — selecionar parágrafo\n"
            "Ctrl+F — pesquisar no PDF",
        )

    def on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self.pending_position is not None:
            self.player.setPosition(self.pending_position)
            self.pending_position = None
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.track_index + 1 < len(self.paragraphs):
                self.request_play(self.track_index + 1)

    def on_player_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        if message:
            diagnostics.record(f"Erro de reprodução: {message}")
            self.status_label.setText(f"Erro de reprodução: {message}")

    def on_duration(self, duration: int) -> None:
        if duration > 0 and self.timeline and self.track_index >= 0 and self.current_audio:
            if self.timeline.record_audio(self.track_index, self.current_audio):
                self.update_timeline_ui()

    def on_position(self, position: int) -> None:
        self.update_timeline_ui(position)

    def on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_pause_button.setIcon(self.transport_icons["pause" if playing else "play"])
        self.play_pause_button.setToolTip("Pausar" if playing else "Reproduzir")
        self.play_pause_button.setAccessibleName("Pausar" if playing else "Reproduzir")
        if playing:
            if self.session_since is None:
                self.session_since = time.monotonic()
        elif self.session_since is not None:
            self.session_ms += round((time.monotonic() - self.session_since) * 1000)
            self.session_since = None
        self.update_session_clock()

    def update_session_clock(self) -> None:
        elapsed = self.session_ms
        if self.session_since is not None:
            elapsed += round((time.monotonic() - self.session_since) * 1000)
        self.session_label.setText(f"Ouvido: {clock(elapsed)}")

    def reset_session_clock(self) -> None:
        self.session_ms = 0
        self.session_since = None
        self.update_session_clock()

    def update_timeline_ui(self, position: int | None = None) -> None:
        if self.timeline is None:
            self.seek_slider.setRange(0, 0)
            self.elapsed.setText(clock(0))
            self.total.setText(clock(0))
            return
        speed = self.speed_rate()
        total = self.timeline.total_ms(speed)
        self.seek_slider.setRange(0, total)
        if self.pending_play and self.book and self.pending_play[0] == self.book.id:
            index, offset = self.pending_play[1], self.pending_play[2]
        else:
            index = self.track_index if self.track_index >= 0 else self.selected_index
            offset = self.player.position() if position is None else position
        current = self.timeline.position_ms(index if index is not None else -1, offset, speed)
        self.elapsed.setText(clock(current))
        self.total.setText(clock(total) if self.timeline.is_exact else f"≈ {clock(total)}")
        if not self.dragging:
            self.seek_slider.setValue(current)

    def seek_from_slider(self) -> None:
        self.dragging = False
        if self.timeline is None:
            return
        index, offset = self.timeline.locate(self.seek_slider.value(), self.speed_rate())
        if index == self.track_index and self.current_audio:
            self.player.setPosition(offset)
        else:
            self.request_play(index, offset)
        self.update_timeline_ui()

    def seek_delta(self, delta: int) -> None:
        if self.track_index < 0:
            return
        position = self.player.position() + delta
        duration = self.player.duration() or self.duration_of(self.track_index)
        previous = self.speakable_index(self.track_index - 1, -1)
        following = self.speakable_index(self.track_index + 1)
        if position < 0 and previous is not None:
            self.request_play(previous, max(0, self.duration_of(previous) + position))
        elif position >= duration and following is not None:
            self.request_play(following, max(0, position - duration))
        else:
            self.player.setPosition(max(0, min(position, duration)))

    def duration_of(self, index: int) -> int:
        if not self.timeline or not 0 <= index < len(self.paragraphs):
            return 0
        return self.timeline.duration_ms(index)

    def toggle_play(self) -> None:
        if not self.book:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.track_index >= 0 and self.current_audio:
            self.player.play()
        else:
            index = self.selected_index
            if index is None:
                index = next((i for i, p in enumerate(self.paragraphs) if p.page >= self.current_page), 0)
            self.request_play(index)

    def previous_paragraph(self) -> None:
        index = self.speakable_index(self.track_index - 1, -1)
        if index is not None:
            self.request_play(index)

    def next_paragraph(self) -> None:
        index = self.speakable_index(self.track_index + 1)
        if index is not None:
            self.request_play(index)

    def previous_page_audio(self) -> None:
        page = self.paragraphs[self.track_index].page if self.track_index >= 0 else self.current_page
        candidates = [i for i, p in enumerate(self.paragraphs)
                      if p.page < page and is_speakable(p.text)]
        if candidates:
            target_page = self.paragraphs[candidates[-1]].page
            self.request_play(next(i for i in candidates if self.paragraphs[i].page == target_page))

    def next_page_audio(self) -> None:
        page = self.paragraphs[self.track_index].page if self.track_index >= 0 else self.current_page
        for index, paragraph in enumerate(self.paragraphs):
            if paragraph.page > page and is_speakable(paragraph.text):
                self.request_play(index)
                return

    def goto_page(self) -> None:
        if not self.book:
            return
        try:
            page = int(self.goto_input.text())
        except ValueError:
            QMessageBox.warning(self, "Página inválida", "Digite um número de página.")
            return
        if not 1 <= page <= self.book.page_count:
            QMessageBox.warning(self, "Página inválida", f"Escolha uma página entre 1 e {self.book.page_count}.")
            return
        self.show_page(page)
        for index, paragraph in enumerate(self.paragraphs):
            if paragraph.page == page and is_speakable(paragraph.text):
                self.request_play(index)
                return
        self.status_label.setText(f"A página {page} não contém texto para áudio.")

    def speed_rate(self) -> float:
        return float(self.speed_combo.currentText().replace("×", "").replace(",", "."))

    def set_speed(self) -> None:
        self.player.setPlaybackRate(self.speed_rate())
        self.update_timeline_ui()

    def closeEvent(self, event) -> None:
        self.persist_reader_state()
        if self.thread is not None:
            self.closing = True
            if self.worker:
                self.worker.cancelled.set()
            event.ignore()
            self.status_label.setText("Encerrando geração e limpando áudios temporários…")
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self.player.setAudioOutput(None)
        for _ in range(25):
            if clear_all_temporary(self.library):
                break
            QApplication.processEvents()
            time.sleep(0.04)
        else:
            diagnostics.record("Alguns MP3s temporários ainda estavam bloqueados ao fechar")
        diagnostics.record("Janela fechada pelo usuário")
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Rever")
    app.setOrganizationName("PDFReaderTTS")
    window = MainWindow()
    window.show()
    return app.exec()
