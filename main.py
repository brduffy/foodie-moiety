"""
Foodie Moiety - Cross-platform Recipe Application
Main application entry point and window manager.
"""

import copy
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRect, QSettings, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFileOpenEvent, QIcon, QPalette, QPixmap
from PySide6.QtMultimedia import QMediaDevices, QMediaFormat, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models.recipe_data import BookCategoryData, BookData, RecipeData, StepData, build_clipboard_recipe
from services.agent_service import AgentService
from services.app_context import AppContext
from services.community_api import CommunityApiClient, WEBSITE_URL
from services.tts_service import TTSService
from services.voice_service import VoiceService
from utils.paths import DATA_DIR, BUNDLE_DIR, DB_PATH, CSAM_REPORT_DOC, LOG_PATH, SETTINGS_PATH, _is_frozen
from utils.clipboard_store import clear_clipboard, load_clipboard, save_clipboard
from utils.book_export import export_book_to_zip, import_book_from_zip, peek_book_zip
from utils.database import (
    copy_recipe_to_book,
    delete_book,
    delete_recipe,
    ensure_schema_migrations,
    find_book_by_title_producer,
    find_recipe_by_title_producer,
    get_standalone_ids_by_titles,
    insert_book_data,
    insert_recipe_data,
    load_book_data,
    load_recipe_data,
    mark_book_viewed,
    mark_recipe_viewed,
    hide_temp_book,
    hide_temp_recipe,
    save_book_data,
    save_recipe_data,
    seed_default_tags,
)
from utils.bluetooth import is_bluetooth_headset
from utils.update_checker import UpdateChecker
from utils.helpers import DIALOG_STYLE, PROGRESS_DIALOG_STYLE, platform_icon, sf_symbol, winui_icon
from utils.recipe_export import export_recipe_to_zip, import_recipe_from_zip, peek_recipe_zip
from views.book_view import BookView
from views.community_detail import CommunityDetailView
from views.community_home import CommunityHomeView
from views.recipe_detail import RecipeDetailView
from views.recipe_list import RecipeListView
from views.video_player import VideoPlayer
from widgets.article_tip_panel import ArticleTipPanel
from widgets.moiety_tip_panel import MoietyTipPanel
from widgets.moiety_panel import MoietyPanel
from widgets.info_panel import InfoPanel
from widgets.command_bar import CommandBar
from widgets.step_navigator import StepNavigator
from widgets.video_play_overlay import VideoPlayOverlay
from widgets.voice_indicator import VoiceIndicator

import logging
import urllib.parse

log = logging.getLogger(__name__)

APP_VERSION = "1.0.4"


class FoodieApp(QApplication):
    """Custom QApplication that handles foodiemoiety:// deep links.

    macOS: The OS delivers custom URL scheme activations via QFileOpenEvent.
    Windows: The URL arrives as a command-line argument (handled in main()).
    """

    deep_link_received = Signal(str)

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            url = event.url().toString() if event.url() else event.file()
            if url and url.startswith("foodiemoiety://"):
                self.deep_link_received.emit(url)
                return True
        return super().event(event)


class _IOWorker(QThread):
    """Background worker for file I/O operations (export/import)."""

    finished = Signal(object)  # Emits result (e.g. new recipe_id or None)
    error = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """
    Main application window that manages all views via QStackedWidget.

    The command bar and step navigator overlay the content area so that
    views (especially recipe detail) fill the entire window.
    """

    def __init__(self, video_path):
        super().__init__()
        self.setWindowTitle("Foodie Moiety")

        # Central widget — no layout, manual positioning
        central_widget = QWidget()
        central_widget.setStyleSheet("background-color: black;")
        self.setCentralWidget(central_widget)

        # Aspect ratio management
        self.aspect_ratio = 9 / 16  # 16:9 aspect ratio
        self.is_resizing = False
        self._is_maximized = False
        self._large_mode_active = False  # Tracks font/info-panel large mode
        self._fullscreen_exiting = False  # Suppresses geometric detection during FS exit
        # Full-window stacked widget for views
        self.stacked_widget = QStackedWidget(central_widget)

        # Overlaid command bar (top)
        self.command_bar = CommandBar(central_widget)
        self.command_bar.raise_()

        # Video play overlay (behind step navigator in z-order)
        self.video_play_overlay = VideoPlayOverlay(central_widget)
        self._vpo_opacity = QGraphicsOpacityEffect(self.video_play_overlay)
        self._vpo_opacity.setOpacity(0.0)
        self.video_play_overlay.setGraphicsEffect(self._vpo_opacity)
        self._fade_anim_vpo = None
        self._vpo_has_video = False  # whether current step has a video

        # Overlaid step navigator (bottom)
        self.step_navigator = StepNavigator(central_widget)
        self.step_navigator.raise_()

        # Voice activity indicator (overlay)
        self.voice_indicator = VoiceIndicator(central_widget)
        self.voice_indicator.raise_()

        # Info panel (full-width overlay for help, scaled ingredients, conversions)
        self._info_panel = InfoPanel(central_widget)
        self._info_panel.raise_()
        self._info_panel.installEventFilter(self)
        self._info_panel_anim = None  # Geometry animation for smooth bar transitions

        # Article tip panel (right-half overlay for article creation instructions)
        self._article_tip_panel = ArticleTipPanel(central_widget)
        self._article_tip_panel.dismissed.connect(self._on_article_tips_dismissed)
        self._article_tips_btn = None  # Set in _apply_article_mode
        self._article_tip_anim = None  # Geometry animation for smooth bar transitions

        # Moiety tip panel (right-half overlay for "What's a Moiety?" explanation)
        self._moiety_tip_panel = MoietyTipPanel(central_widget)
        self._moiety_tip_panel.dismissed.connect(self._on_moiety_tips_dismissed)
        self._moiety_tip_anim = None

        # Moiety panel (right-half overlay for browsing/inserting moiety steps)
        self._moiety_panel = MoietyPanel(central_widget)
        self._moiety_panel.insert_requested.connect(self._on_moiety_insert)
        self._moiety_panel.preview_requested.connect(self._on_moiety_preview)
        self._moiety_panel.dismissed.connect(self._on_moiety_panel_dismissed)
        self._moiety_panel_anim = None

        # Step indicator (visible when bars are hidden)
        self._step_indicator = QLabel(central_widget)
        self._step_indicator.setAlignment(Qt.AlignCenter)
        self._step_indicator.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 180);
                color: white;
                border-radius: 10px;
                padding: 8px 18px;
                font-size: 22px;
                font-weight: bold;
            }
        """)
        self._step_indicator.hide()

        # Opacity effects for fade animations (enabled only in recipe detail)
        self._cb_opacity = QGraphicsOpacityEffect(self.command_bar)
        self._cb_opacity.setEnabled(False)
        self.command_bar.setGraphicsEffect(self._cb_opacity)
        self._sn_opacity = QGraphicsOpacityEffect(self.step_navigator)
        self._sn_opacity.setEnabled(False)
        self.step_navigator.setGraphicsEffect(self._sn_opacity)
        self._fade_anim_cb = None
        self._fade_anim_sn = None

        # Initialize views
        self.recipe_list = RecipeListView()
        self.stacked_widget.addWidget(self.recipe_list)

        self.recipe_detail = RecipeDetailView()
        self.stacked_widget.addWidget(self.recipe_detail)

        self.video_player = VideoPlayer(video_path)
        self.stacked_widget.addWidget(self.video_player)

        self.book_view = BookView()
        self.stacked_widget.addWidget(self.book_view)

        from views.grocery_list import GroceryListView
        self.grocery_list_view = GroceryListView()
        self.stacked_widget.addWidget(self.grocery_list_view)
        self._grocery_list_source = None  # Tracks where user came from

        self.community_detail = CommunityDetailView()
        self.community_detail.purchase_requested.connect(self._on_community_purchase_requested)
        self.stacked_widget.addWidget(self.community_detail)

        self.community_home = CommunityHomeView()
        self.stacked_widget.addWidget(self.community_home)

        # Restore persisted settings
        self._settings = QSettings(str(SETTINGS_PATH), QSettings.IniFormat)
        saved_delta = self._settings.value("font_size_delta", 0, type=int)
        if saved_delta:
            self.recipe_detail.adjust_font_size(saved_delta)
            self.book_view.toc_widget.adjust_font_size(saved_delta)
            self.book_view.description_editor.adjust_font_size(saved_delta)
            self._article_tip_panel.adjust_font_size(saved_delta)
            self._moiety_tip_panel.adjust_font_size(saved_delta)
        self._tts_enabled = self._settings.value("tts_enabled", False, type=bool)

        # Connect recipe list selection
        self.recipe_list.recipe_selected.connect(self._on_recipe_selected)
        self.recipe_list.book_selected.connect(self._on_book_selected)
        self.recipe_list.copy_recipe.connect(self._on_copy_recipe_from_list)
        self.recipe_list.export_recipe.connect(self._on_export_recipe)
        self.recipe_list.export_book.connect(self._on_export_book_by_id)
        self.recipe_list.delete_recipe.connect(self._on_delete_recipe_from_list)
        self.recipe_list.delete_book.connect(self._on_delete_book_from_list)
        self.recipe_list.upload_recipe.connect(self._on_upload_recipe)
        self.recipe_list.upload_book.connect(self._on_upload_book)
        self.recipe_list.community_download.connect(self._on_community_download_requested)
        self.recipe_list.community_preview.connect(self._on_community_preview)
        self.recipe_list.community_load_next_page.connect(self._fetch_community_page)
        self.recipe_list.review_approve.connect(self._on_review_approve)
        self.recipe_list.review_reject.connect(self._on_review_reject)
        self.recipe_list.review_quarantine.connect(self._on_review_quarantine)
        self.recipe_list.review_preview.connect(self._on_review_preview)
        self.recipe_list.review_load_next_page.connect(self._fetch_review_page)

        # Connect book view recipe click
        self.book_view.recipe_clicked.connect(self._on_book_recipe_clicked)

        # Connect step navigator to detail view
        self.step_navigator.step_changed.connect(self._on_step_changed)
        self.step_navigator.selection_changed.connect(self._on_selection_changed)
        self.step_navigator.step_moved.connect(self._on_step_moved)

        # Connect step link navigation from rich text editor
        self.recipe_detail.step_link_clicked.connect(self._on_step_link_from_editor)
        self.recipe_detail.ingredients_editor.addToGroceryListRequested.connect(
            self._on_add_ingredients_to_grocery_list
        )

        # Connect video play overlay and video player stop/pause
        self.video_play_overlay.clicked.connect(self._on_play_overlay_clicked)
        self.video_player.stop_requested.connect(self._on_video_stopped)
        self.video_player.media_player.playbackStateChanged.connect(
            self._on_video_playback_state_changed
        )

        # Toast notification label (overlaid, hidden by default)
        self._toast_label = QLabel(central_widget)
        self._toast_label.setAlignment(Qt.AlignCenter)
        self._toast_label.setStyleSheet("""
            QLabel {
                background-color: rgba(30, 30, 30, 235);
                color: white;
                border: 1px solid #0078d4;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 15px;
                font-weight: bold;
            }
        """)
        self._toast_label.hide()
        self._toast_label.raise_()
        self._toast_opacity = QGraphicsOpacityEffect(self._toast_label)
        self._toast_label.setGraphicsEffect(self._toast_opacity)
        self._fade_anim_toast = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._fade_out_toast)

        # Track image files added during current edit session (for cancel cleanup)
        self._pending_image_files = []

        # Clipboard state
        self._clipboard_data = load_clipboard()
        self._viewing_clipboard = False
        self._preview_only = False  # True when viewing moiety preview (hides Clear/Create)
        self._pre_clipboard_view = None  # "recipe_list" or ("recipe_detail", recipe_id)

        # Video player return target ("recipe_detail" or "book_view")
        self._video_source_view = "recipe_detail"

        # Book view state
        self._current_book_data = None
        self._book_edit_snapshot = None
        self._add_to_book_data = None
        self._add_to_book_cat_idx = 0
        self._add_to_book_count = 0
        self._book_is_new = False  # True when book was created via New Book (not yet user-saved)
        self._came_from_book = False  # True when recipe detail opened from book TOC
        self._article_mode = False  # True when viewing/editing an article

        # Community browse state
        self._community_api = CommunityApiClient(self)
        self._community_mode = False
        self._community_search_query = ""
        self._community_tags: list[str] = []
        self._community_producers: list[str] = []
        self._community_cuisines: list[str] = []
        self._community_sort = "recent"
        # API-loaded tag/cuisine lists (for homepage tag panel)
        self._api_tags: list[str] = []
        self._api_cuisines: list[str] = []
        # Homepage search state
        self._home_search_query = ""
        self._home_search_tags: list[str] = []
        self._home_search_cuisines: list[str] = []
        self._home_search_cursor = None
        self._home_search_active = False
        self._thumb_debounce = QTimer(self)
        self._thumb_debounce.setSingleShot(True)
        self._thumb_debounce.setInterval(100)
        self._thumb_debounce.timeout.connect(self._request_visible_thumbnails)
        self._home_thumb_cache = {}  # community_id → QPixmap
        self._community_api.page_loaded.connect(self._on_community_page_loaded)
        self._community_api.page_error.connect(self._on_community_page_error)
        self._community_api.thumbnail_loaded.connect(self._on_community_thumbnail_loaded)
        self._community_api.download_ready.connect(self._on_community_download_ready)
        self._community_api.download_error.connect(self._on_community_download_error)
        self._community_api.download_progress.connect(self._on_community_download_progress)
        self._community_api.upload_ready.connect(self._on_upload_ready)
        self._community_api.upload_error.connect(self._on_upload_error)
        self._community_api.upload_size_error.connect(self._on_upload_size_error)
        self._community_api.upload_progress.connect(self._on_upload_progress)
        self._community_api.pending_loaded.connect(self._on_review_page_loaded)
        self._community_api.pending_error.connect(self._on_review_page_error)
        self._community_api.review_done.connect(self._on_review_done)
        self._community_api.review_error.connect(self._on_review_error)
        self._community_api.subscription_status_loaded.connect(self._on_subscription_status)
        self._community_api.subscription_status_error.connect(self._on_subscription_status_error)
        self._community_api.checkout_url_ready.connect(self._on_checkout_url_ready)
        self._community_api.checkout_url_error.connect(self._on_checkout_url_error)
        self._community_api.upload_limit_error.connect(self._on_upload_limit_error)
        self._subscription_status: dict | None = None
        self._pending_upload: tuple | None = None  # ("recipe"|"book", id) for post-limit-check
        self._purchase_poll_cid: str | None = None  # community_id being polled for purchase
        self._purchase_poll_timer = QTimer(self)
        self._purchase_poll_timer.setInterval(5000)  # 5 seconds
        self._purchase_poll_timer.timeout.connect(self._poll_purchase_status)
        self._awaiting_download_detail = False
        self._community_api.recipe_detail_loaded.connect(self._on_recipe_detail_result)
        self._community_api.recipe_detail_error.connect(self._on_detail_error)
        self._community_api.book_detail_loaded.connect(self._on_book_detail_result)
        self._community_api.book_detail_error.connect(self._on_book_detail_error)
        self._community_api.purchases_loaded.connect(self._on_purchases_loaded)
        self._purchased_book_ids: set[str] = set()
        self._community_api.account_action_done.connect(self._on_account_action_done)
        self._community_api.account_action_error.connect(self._on_account_action_error)
        self._community_api.producer_items_loaded.connect(self._on_producer_items_loaded)
        self._community_api.producer_items_error.connect(self._on_producer_items_error)
        # Homepage section signals
        self._community_api.carousel_loaded.connect(self._on_home_carousel_loaded)
        self._community_api.stats_loaded.connect(self._on_home_stats_loaded)
        self._community_api.books_loaded.connect(self._on_home_books_loaded)
        self._community_api.articles_loaded.connect(self._on_home_articles_loaded)
        self._community_api.moieties_loaded.connect(self._on_home_moieties_loaded)
        self._community_api.creators_loaded.connect(self._on_home_creators_loaded)
        self._community_api.feed_loaded.connect(self._on_home_feed_loaded)
        self._feed_cursor = None
        self.community_home.card_clicked.connect(self._on_home_card_clicked)
        self.community_home.creator_clicked.connect(self._on_creator_clicked)
        self.community_home.load_more_clicked.connect(self._on_home_load_more)
        self.community_home.search_load_more.connect(self._on_home_search_load_more)
        self.community_home.back_to_home.connect(self._on_home_search_back)
        self.community_home.tags_changed.connect(self._on_home_tags_changed)
        self._community_api.tags_loaded.connect(self._on_community_tags_loaded)
        self._community_api.cuisines_loaded.connect(self._on_community_cuisines_loaded)
        self._community_api.search_loaded.connect(self._on_home_search_loaded)
        # Homepage section error signals
        self._community_api.carousel_error.connect(self._on_home_section_error)
        self._community_api.stats_error.connect(self._on_home_section_error)
        self._community_api.books_error.connect(self._on_home_section_error)
        self._community_api.articles_error.connect(self._on_home_section_error)
        self._community_api.moieties_error.connect(self._on_home_section_error)
        self._community_api.creators_error.connect(self._on_home_section_error)
        self._community_api.feed_error.connect(self._on_home_section_error)
        self._community_api.tags_error.connect(self._on_home_section_error)
        self._home_error_count = 0
        self.community_home.retry_clicked.connect(self._on_home_retry)
        self._review_mode = False
        self._review_downloading = False
        self._review_temp_imports: dict[str, dict] = {}   # community_id → {"recipe_id":int|None,"book_id":int|None}
        self._review_current_cid: str | None = None       # community_id currently being previewed
        self._review_preview_item: dict | None = None
        self._review_refund_pending: set[str] = set()
        self._review_bom_pending: set[str] = set()  # community_ids to save as BOM candidate on approve
        self._suspended_dialog = None
        self._comparison_downloading = False
        self._comparison_item: dict | None = None  # producer item being compared
        self._comparison_cid: str | None = None    # community_id of imported comparison
        self._showing_comparison = False            # True when showing comparison recipe

        # Auth state — try silent token refresh on startup
        self._auth_id_token: str | None = self._settings.value("cognito_id_token", None)
        self._auth_access_token: str | None = self._settings.value("cognito_access_token", None)
        self._auth_refresh_token: str | None = self._settings.value("cognito_refresh_token", None)
        self._auth_email: str = self._settings.value("cognito_email", "", type=str)
        self._auth_display_name: str = self._settings.value("auth_display_name", "", type=str)
        self._auth_expiry: int = self._settings.value("cognito_token_expiry", 0, type=int)
        # Cached account tier from last successful subscription fetch (persisted
        # so the account menu shows correct options before the async fetch completes)
        self._cached_account_tier: str = self._settings.value("account_tier", "free", type=str)
        if self._auth_refresh_token:
            self._try_silent_refresh()

        # Layout mode menu button (set in _configure_recipe_detail_commands)
        self._layout_mode_btn = None

        # AI agent service
        self._agent = AgentService(parent=self)
        self._agent.result_ready.connect(self._on_agent_result)
        self._agent.processing_started.connect(self._on_agent_processing)

        # Text-to-speech for speaking agent responses to voice commands
        self._tts = TTSService(parent=self)

        # Voice service for push-to-talk and always-on listening
        self._voice = VoiceService(parent=self)
        self._voice._tts = self._tts
        self._voice.transcription_ready.connect(self._on_voice_transcription)
        self._voice.recording_started.connect(self._on_voice_recording_started)
        self._voice.recording_stopped.connect(self._on_voice_recording_stopped)
        self._voice.wake_word_detected.connect(self._on_wake_word_detected)
        self._voice.listening_started.connect(self._on_listening_started)
        self._voice.listening_stopped.connect(self._on_listening_stopped)
        self._voice.followup_started.connect(self._on_followup_started)
        self._voice.followup_expired.connect(self._on_followup_expired)
        self._voice.hands_free_changed.connect(self._on_hands_free_changed)
        self._voice.error.connect(self._on_voice_error)
        self._is_voice_recording = False
        self._is_voice_listening = False
        self._listening_paused = False
        self._hands_free = False
        self._wake_word_active = False  # True when current recording was wake-word-initiated
        self._voice_paused_for_video = False  # True when voice was auto-paused for video playback
        self._hands_free_paused_for_video = False  # True when hands-free was disabled for video
        self._mic_saved_for_nav = None  # Saved mic state when navigating away from recipe detail
        self._headset_override = self._settings.value("headset_mode", False, type=bool)  # Manual override
        self._headset_detected = False  # Auto-detected Bluetooth headset
        self._warned_voice_paused = False  # TTS warning shown this session
        self._pending_video_path = None  # Video path to play after TTS warning
        self._last_command_was_voice = False
        self._ducked_volume: float | None = None  # Video volume before ducking

        # --- Audio device change monitoring ---
        # Poll QMediaDevices every 3s to detect OS sound-setting changes.
        # QMediaDevices queries CoreAudio live (unlike PortAudio which caches).
        self._last_output_device_id = QMediaDevices.defaultAudioOutput().id()
        self._last_input_device_id = QMediaDevices.defaultAudioInput().id()
        self._audio_device_timer = QTimer(self)
        self._audio_device_timer.timeout.connect(self._check_audio_devices)
        self._audio_device_timer.start(3000)

        # Initial Bluetooth headset detection
        dev_id = bytes(self._last_input_device_id).decode("utf-8", errors="replace")
        self._headset_detected = is_bluetooth_headset(dev_id)

        # --- Auto-hide timer for command bar / step navigator ---
        self._autohide_timeout_ms = 7000
        self._autohide_timer = QTimer(self)
        self._autohide_timer.setSingleShot(True)
        self._autohide_timer.timeout.connect(self._autohide_bars)

        # Enable mouse tracking so mouseMoveEvent fires without button press
        self.setMouseTracking(True)
        central_widget.setMouseTracking(True)
        self.stacked_widget.setMouseTracking(True)
        # Install event filter on the app to catch mouse moves from all children
        QApplication.instance().installEventFilter(self)

        # Start with community homepage (1.5s delay — singleShot(0) fires before the
        # network stack is ready on startup, causing frequent carousel load failures)
        QTimer.singleShot(1500, self._enter_community_mode)

        # Check for app updates (5s delay so UI loads first)
        self._update_checker = UpdateChecker(APP_VERSION, self)
        self._update_checker.update_available.connect(self._on_update_available)
        QTimer.singleShot(5000, self._update_checker.check)

        # Preload voice models in background after UI is displayed
        QTimer.singleShot(200, self._voice.preload_model)

        # macOS app menu — "About Foodie Moiety" with version number
        if platform.system() == "Darwin":
            menu_bar = self.menuBar()
            app_menu = menu_bar.addMenu("Foodie Moiety")
            about_action = app_menu.addAction("About Foodie Moiety")
            about_action.setMenuRole(QAction.MenuRole.AboutRole)
            about_action.triggered.connect(self._show_about_dialog)

        # Set initial window size and minimum width
        self.setMinimumWidth(900)
        self.resize(1000, 565)  # 16:9 ratio (1000 * 9/16 = 562.5, rounded up)

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def show_recipe_list(self):
        """Switch to recipe list view and configure command bar."""
        self._community_mode = False
        self._article_mode = False
        self._tips_pulse_checked_id = None
        self._stop_tips_pulse()
        self._autohide_timer.stop()
        self._stop_fade_animations()
        self._voice.set_active_view("recipe_list")
        self.stacked_widget.setCurrentWidget(self.recipe_list)
        self._cb_opacity.setOpacity(1.0)
        self.step_navigator.hide()
        self._info_panel.hide()
        self._article_tip_panel.hide()
        self._moiety_tip_panel.hide()
        self._moiety_panel.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        self._configure_recipe_list_commands()
        self.recipe_list.hide_mode_banner()
        # Embed command bar in recipe list layout (not as overlay)
        self.recipe_list.set_command_bar(self.command_bar)
        # Refresh total count in case recipes were added/deleted
        self.recipe_list.refresh_total_count()
        # Re-apply current filters (keeps text/tag filters if any)
        self.recipe_list.filter_recipes()
        # Re-calculate description elision after view transition
        self.recipe_list.refresh_description_elision()
        self._position_overlays()

    def _return_to_home(self):
        """Return to the appropriate home view (community or recipe list)."""
        if self._community_mode:
            self._stop_fade_animations()
            self._autohide_timer.stop()
            self.step_navigator.hide()
            self._info_panel.hide()
            self._article_tip_panel.hide()
            self._moiety_tip_panel.hide()
            self._moiety_panel.hide()
            self.video_play_overlay.hide()
            self._step_indicator.hide()
            self._cb_opacity.setOpacity(1.0)
            self.stacked_widget.setCurrentWidget(self.community_home)
            self._configure_community_home_commands()
            self.community_home.set_command_bar(self.command_bar)
            self._position_overlays()
        else:
            self.show_recipe_list()

    def show_recipe_detail(self, recipe_id):
        """Switch to recipe detail view and configure command bar/step navigator."""
        self._stop_fade_animations()
        self._voice.set_active_view("recipe_detail")
        self._info_panel.set_view("recipe_detail")
        self.stacked_widget.setCurrentWidget(self.recipe_detail)
        # Enable opacity effects for auto-hide fade (recipe detail only)
        self._cb_opacity.setOpacity(1.0)
        self._sn_opacity.setOpacity(1.0)
        self._cb_opacity.setEnabled(True)
        self._sn_opacity.setEnabled(True)
        # Move command bar back to central widget as overlay
        self.command_bar.setParent(self.centralWidget())
        self.command_bar.show()
        self.command_bar.raise_()
        self.step_navigator.show()
        self._configure_recipe_detail_commands()

        # Load recipe data from DB and pass to detail view
        recipe_data = load_recipe_data(recipe_id)
        if recipe_data:
            if not self._came_from_book:
                mark_recipe_viewed(recipe_id)
            self._article_mode = recipe_data.content_type == "article"
            self.recipe_detail.load_recipe(recipe_data)
            self.recipe_detail.set_layout_mode(
                "directions" if self._article_mode else "both"
            )
            # +1 for the intro step at index 0
            num_nav_steps = len(recipe_data.steps) + 1
            self.step_navigator.load_steps(recipe_id=recipe_id, num_steps=num_nav_steps)
            self._update_play_video_state(0)
            self._update_step_indicator(0)
            self._update_tips_buttons_visibility()
            self._article_tip_panel.hide()
            self._moiety_tip_panel.hide()
            self._moiety_panel.hide()
            self._apply_article_mode()

        # Force layout recalculation so sizeHint() reflects the new buttons
        self.command_bar.updateGeometry()
        QApplication.processEvents()
        self._position_overlays()
        self._autohide_timer.start(self._autohide_timeout_ms)

    def show_book_view(self, book_id=None, book_data=None):
        """Switch to book view and configure command bar.

        Args:
            book_id: Optional book ID to load from database.
            book_data: Optional BookData to display directly. If None, loads
                       from database. Falls back to recipe list if not found.
        """
        self._stop_fade_animations()
        self._article_tip_panel.hide()
        self._moiety_tip_panel.hide()
        self._moiety_panel.hide()
        self.stacked_widget.setCurrentWidget(self.book_view)
        self._cb_opacity.setOpacity(1.0)
        self._sn_opacity.setOpacity(1.0)
        # Move command bar to central widget as overlay
        self.command_bar.setParent(self.centralWidget())
        self.command_bar.show()
        self.command_bar.raise_()
        self.step_navigator.hide()
        self.video_play_overlay.hide()
        self._info_panel.hide()
        self._step_indicator.hide()
        self._configure_book_view_commands()

        if book_data is not None:
            data = book_data
        elif book_id is not None:
            data = load_book_data(book_id)
            if data is None:
                self.show_recipe_list()
                return
        else:
            self.show_recipe_list()
            return
        self._current_book_data = data
        self.book_view.load_book(data)
        self._set_book_editing(False)
        if data.book_id is not None:
            mark_book_viewed(data.book_id)

        # Force layout recalculation
        self.command_bar.updateGeometry()
        QApplication.processEvents()
        self._position_overlays()
        self._update_book_video_overlay()

    def show_book_view_from_add(self):
        """Return to book view after add-to-book mode (reuses current book data)."""
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.book_view)
        self._cb_opacity.setOpacity(1.0)
        self._sn_opacity.setOpacity(1.0)
        self.command_bar.setParent(self.centralWidget())
        self.command_bar.show()
        self.command_bar.raise_()
        self.step_navigator.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        self._configure_book_view_commands()
        # Reload TOC from the (possibly modified) book data
        if self._current_book_data:
            self.book_view.load_book(self._current_book_data)
        # Restore edit mode if it was active before navigating to add recipes
        if getattr(self, "_book_edit_mode_before_add", False):
            self._set_book_editing(True)
            self._book_edit_mode_before_add = False
        self.command_bar.updateGeometry()
        QApplication.processEvents()
        self._position_overlays()
        self._update_book_video_overlay()

    def _on_book_back(self):
        """Navigate back from book view to the previous home view."""
        self._return_to_home()

    def _on_book_edit_toggled(self):
        """Enter edit mode on the book view."""
        # Snapshot the current book data so cancel can restore it
        self._book_edit_snapshot = copy.deepcopy(self._current_book_data)
        self._set_book_editing(True)

    def _on_book_edit_save(self):
        """Save book changes and exit edit mode."""
        # Persist TOC edits back to book data
        data = self._current_book_data
        is_new = self._book_is_new
        if data is not None:
            data.title = self.book_view._title_edit.toPlainText().strip()
            if not data.title:
                msg = QMessageBox(self)
                msg.setWindowTitle("Title Required")
                msg.setText("Please enter a title for the book before saving.")
                msg.setStyleSheet(DIALOG_STYLE)
                msg.exec()
                return
            data.categories = self.book_view.toc_widget.get_categories()
            data.description = self.book_view.description_editor.get_html()
            data.tags = list(self.book_view.tags_editor.get_tags())
            # Persist to database
            if data.book_id is None:
                data.book_id = insert_book_data(data)
            # Copy default.jpg into the book's own media folder if still referenced
            data.cover_image_path = self._copy_default_image(
                data.cover_image_path, "media/books", data.book_id
            )
            save_book_data(data)
            data.dirty = False
        self._book_edit_snapshot = None
        self._book_is_new = False
        self._set_book_editing(False)
        self._show_toast("Book saved")
        # New book: navigate to library so user can see what they created
        if is_new:
            self.show_recipe_list()

    def _on_book_edit_cancel(self):
        """Discard book edits and exit edit mode."""
        # Grab the live book_id before restoring snapshot (snapshot may have None)
        live_book_id = getattr(self._current_book_data, "book_id", None)
        # Restore from the snapshot taken when edit mode was entered
        if self._book_edit_snapshot is not None:
            self._current_book_data = self._book_edit_snapshot
            self._book_edit_snapshot = None
        if self._book_is_new:
            # New book — delete auto-saved DB row (if any) and go back
            if live_book_id is not None:
                delete_book(live_book_id)
            self._current_book_data = None
            self._book_is_new = False
            self._return_to_home()
            return
        # Sync DB to match the restored snapshot — this deletes any recipe
        # copies that were added during the edit session (copy_recipe_to_book
        # creates DB rows immediately, cancel must undo them)
        if self._current_book_data is not None and live_book_id is not None:
            save_book_data(self._current_book_data)
            self.book_view.load_book(self._current_book_data)
        self._set_book_editing(False)

    def _set_book_editing(self, editing):
        """Show/hide edit-mode controls for book view."""
        self.book_view.toc_widget.set_edit_mode(editing)
        self.book_view._title_edit.setReadOnly(not editing)
        self.book_view._title_edit.setStyleSheet(
            self.book_view._TITLE_SS_EDIT if editing else self.book_view._TITLE_SS_VIEW
        )
        self.book_view.description_editor.set_read_only(not editing)
        self.book_view.tags_editor.set_read_only(not editing)
        self._book_edit_btn.setVisible(not editing)
        self._book_back_btn.setVisible(not editing)
        self._book_save_btn.setVisible(editing)
        self._book_cancel_btn.setVisible(editing)
        self._book_edit_separator.setVisible(editing)
        self._book_image_btn.setVisible(editing)
        self._book_video_btn.setVisible(editing)

    def _on_book_layout_mode_changed(self, mode):
        """Handle book view layout mode change from dropdown."""
        self.book_view.set_layout_mode(mode)
        # Sync the dropdown button label
        for act in self._book_layout_mode_btn._menu.actions():
            if act.data() == mode:
                self._book_layout_mode_btn.setText(f"{act.text()}  \u25be")
                break
        self._position_overlays()

    def _on_book_tags_changed(self):
        """Persist tag changes from the book's TagsEditor to BookData."""
        data = self._current_book_data
        if data is not None:
            data.tags = self.book_view.tags_editor.get_tags()
            data.dirty = True

    def _on_book_cover_image(self):
        """Open a file dialog to set the book's cover image."""
        data = self._current_book_data
        if data is None:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Cover Image", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tiff)",
        )
        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            msg = QMessageBox(self)
            msg.setWindowTitle("Invalid Image")
            msg.setText("The selected file could not be loaded as an image.")
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return

        # Validate 16:9 aspect ratio (±2% tolerance)
        ratio = pixmap.width() / pixmap.height()
        target_ratio = 16 / 9
        if abs(ratio - target_ratio) / target_ratio > 0.02:
            msg = QMessageBox(self)
            msg.setWindowTitle("Wrong Aspect Ratio")
            msg.setText(
                f"The image must be 16:9 aspect ratio.\n\n"
                f"Selected image is {pixmap.width()}×{pixmap.height()} "
                f"({ratio:.3f}), expected {target_ratio:.3f}."
            )
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return

        # Remove the old cover image file
        self._remove_old_media(data.cover_image_path)

        # Downscale if larger than 1920x1080 and save as JPEG
        max_w, max_h = 1920, 1080
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(
                max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )

        folder_id = data.book_id if data.book_id is not None else "new"
        dest_dir = os.path.join(
            str(DATA_DIR), "media", "books", str(folder_id),
        )
        os.makedirs(dest_dir, exist_ok=True)
        new_name = f"{uuid.uuid4().hex}.jpg"
        dest_path = os.path.join(dest_dir, new_name)
        pixmap.save(dest_path, "JPEG", 85)

        rel_path = f"media/books/{folder_id}/{new_name}"
        data.cover_image_path = rel_path
        data.dirty = True
        self.book_view.load_book(data)
        self._show_toast("Cover image updated")

    def _on_book_video_btn_clicked(self):
        """Show a menu with Import / Remove video options for book intro video."""
        data = self._current_book_data
        if data is None:
            return
        has_video = bool(data.intro_video_path)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #555;
                padding: 4px 0;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a3a3a; }
            QMenu::item:disabled { color: #666; }
        """)
        import_action = menu.addAction("Import Video")
        remove_action = menu.addAction("Remove Video")
        remove_action.setEnabled(has_video)

        action = menu.exec(self._book_video_btn.mapToGlobal(
            self._book_video_btn.rect().bottomLeft()
        ))
        if action == import_action:
            self._on_book_intro_video()
        elif action == remove_action:
            self._on_remove_book_intro_video()

    def _on_remove_book_intro_video(self):
        """Remove the intro video from the current book."""
        data = self._current_book_data
        if data is None or not data.intro_video_path:
            return
        self._remove_old_media(data.intro_video_path)
        data.intro_video_path = None
        data.dirty = True
        self._update_book_video_overlay()
        self._show_toast("Intro video removed")

    def _on_book_intro_video(self):
        """Open a file dialog to set the book's intro video."""
        data = self._current_book_data
        if data is None:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Intro Video", "",
            "Videos (*.mp4 *.m4v *.mov *.mkv *.avi)",
        )
        if not file_path:
            return

        # Remove the old intro video file
        self._remove_old_media(data.intro_video_path)

        folder_id = data.book_id if data.book_id is not None else "new"
        dest_dir = os.path.join(
            str(DATA_DIR), "media", "books", str(folder_id),
        )
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(file_path)[1].lower() or ".mp4"
        new_name = f"{uuid.uuid4().hex}{ext}"
        dest_path = os.path.join(dest_dir, new_name)
        rel_path = f"media/books/{folder_id}/{new_name}"

        # Warn if video is above 1080p
        from utils.video_compress import is_above_1080p
        is_above, resolution = is_above_1080p(file_path)
        if is_above:
            w, h = resolution
            if not self._warn_high_res_video(w, h):
                return

        # Copy to media folder
        shutil.copy2(file_path, dest_path)
        data.intro_video_path = rel_path
        data.dirty = True
        self._update_book_video_overlay()
        self._show_toast("Intro video updated")

    def _update_book_video_overlay(self):
        """Show or hide the video play overlay based on current book's intro video."""
        data = self._current_book_data
        has_video = bool(data and data.intro_video_path)
        self._vpo_has_video = has_video
        self._fade_video_overlay(visible=has_video)

    def _play_book_intro_video(self):
        """Play the book's intro video in the video player."""
        data = self._current_book_data
        if not data or not data.intro_video_path:
            return
        rel = data.intro_video_path
        path = rel if os.path.isabs(rel) else os.path.join(str(DATA_DIR), rel)
        if not os.path.isfile(path):
            return
        self._video_source_view = "book_view"
        self.video_player.load_video(path)
        self.show_video_player()
        self.video_player.media_player.play()

    # ------------------------------------------------------------------
    # Add-to-book mode
    # ------------------------------------------------------------------

    def _on_add_recipes_to_book(self):
        """Navigate to recipe list in add-to-book mode."""
        data = self._current_book_data
        if data is None:
            return
        # Remember edit mode so we can restore it on return
        self._book_edit_mode_before_add = self.book_view.toc_widget.edit_mode
        # Sync all editor state back to BookData before leaving book view
        new_title = self.book_view._title_edit.toPlainText().strip()
        if new_title:
            data.title = new_title
        data.categories = self.book_view.toc_widget.get_categories()
        data.description = self.book_view.description_editor.get_html()
        data.tags = list(self.book_view.tags_editor.get_tags())
        # Ensure at least one category exists

        if not data.categories:
            from models.recipe_data import BookCategoryData
            data.categories.append(BookCategoryData(
                category_id=None, name="Uncategorized", display_order=0,
            ))
        # Auto-save new books so copy_recipe_to_book has a valid book_id
        # Reload after insert to get category_id values from the DB
        if data.book_id is None:
            data.book_id = insert_book_data(data)
            reloaded = load_book_data(data.book_id)
            if reloaded:
                data.categories = reloaded.categories
                self._current_book_data = data
        self._add_to_book_data = data
        self._add_to_book_cat_idx = 0
        self._add_to_book_count = 0
        # Map book recipe titles back to standalone recipe IDs so the
        # add-to-book UI can mark them as already added and block duplicates
        book_titles = {
            r["title"] for cat in data.categories for r in cat.recipes
            if r.get("title")
        }
        book_recipe_ids = get_standalone_ids_by_titles(book_titles)
        self._show_recipe_list_for_book(book_recipe_ids)

    def _show_recipe_list_for_book(self, book_recipe_ids):
        """Switch to recipe list view in add-to-book mode."""
        self._autohide_timer.stop()
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.recipe_list)
        self._cb_opacity.setOpacity(1.0)
        self.step_navigator.hide()
        self._info_panel.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        self._configure_add_to_book_commands()
        self.recipe_list.set_command_bar(self.command_bar)
        self.recipe_list.refresh_total_count()
        # Set add-to-book mode before loading cards so they get the right overlays
        is_bom = self._add_to_book_data and self._add_to_book_data.is_book_of_moiety
        self.recipe_list.enter_add_to_book_mode(book_recipe_ids, bom_book=bool(is_bom))
        self.recipe_list.filter_recipes()
        self.recipe_list.refresh_description_elision()
        self._position_overlays()

    def _configure_add_to_book_commands(self):
        """Configure command bar for add-to-book mode."""
        self.play_video_toggle = None
        self.command_bar.clear()
        icon_btn_style = """
            QPushButton {
                min-width: 32px; max-width: 32px;
                min-height: 32px; max-height: 32px;
                padding: 0px; font-size: 20px;
            }
        """
        back_btn = self.command_bar.add_button(
            "", self._on_add_to_book_back, tooltip="Back to book",
            icon=platform_icon("arrow.left", weight="regular", point_size=48,
                               color="white", windows_name="ArrowLeft8"),
        )
        back_btn.setFixedSize(32, 32)
        back_btn.setStyleSheet(icon_btn_style)

        # Category dropdown
        cat_items = [
            (cat.name, i) for i, cat in enumerate(self._add_to_book_data.categories)
        ]
        self._add_to_book_cat_btn = self.command_bar.add_menu_button(
            cat_items, self._on_add_to_book_cat_changed,
            tooltip="Target category",
        )

        # Counter label
        self._add_to_book_counter = QLabel("0 added")
        self._add_to_book_counter.setStyleSheet(
            "color: #888888; font-size: 13px; padding: 0 8px;"
        )
        self._add_to_book_counter.setFixedHeight(32)
        self.command_bar.layout.insertWidget(
            len(self.command_bar.command_widgets), self._add_to_book_counter
        )
        self.command_bar.command_widgets.append(self._add_to_book_counter)

        self.command_bar.add_stretch()

        # Search + filter controls (same as normal recipe list)
        self._search_field = self.command_bar.add_search(
            "Search recipes...", self._on_search,
        )
        self._tags_btn = self.command_bar.add_button(
            "Tags  \u25be", self._on_show_tags_filter,
            icon=platform_icon("tag", weight="regular", point_size=48, color="white"),
        )
        self._clear_filters_btn = self.command_bar.add_button(
            "Clear", self._on_clear_filters,
        )
        self._clear_filters_btn.setVisible(self.recipe_list.has_active_filters())
        self.recipe_list.filtersChanged.connect(self._on_filters_changed)

        # Connect add-to-book signal
        self.recipe_list.add_to_book.connect(self._on_add_recipe_to_book)

    def _on_add_to_book_cat_changed(self, idx):
        """Handle category dropdown selection change."""
        self._add_to_book_cat_idx = idx

    def _on_add_recipe_to_book(self, recipe_id):
        """Handle adding a recipe to the book's TOC via deep copy."""
        data = self._add_to_book_data
        if data is None:
            return
        # Prevent duplicate adds — check if this source recipe is already in the book
        if recipe_id in self.recipe_list._book_recipe_ids:
            return
        # Look up recipe title from card
        title = "Untitled"
        for card in self.recipe_list._cards:
            if card._recipe_id == recipe_id:
                title = card._recipe_title
                break
        # Extract a short plain-text snippet from the recipe's description
        snippet = None
        recipe_data = load_recipe_data(recipe_id)
        if recipe_data and recipe_data.description:
            from PySide6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setHtml(recipe_data.description)
            plain = doc.toPlainText().strip()
            # Take first ~120 chars, breaking at a word boundary
            if len(plain) > 120:
                plain = plain[:120].rsplit(" ", 1)[0] + "\u2026"
            if plain:
                snippet = plain
        # Deep-copy the recipe into the book (creates new DB row + media)
        cat = data.categories[self._add_to_book_cat_idx]
        order = len(cat.recipes)
        new_id = copy_recipe_to_book(
            recipe_id, data.book_id, cat.category_id, order, snippet,
        )
        new_rd = load_recipe_data(new_id)
        cat.recipes.append({
            "recipe_id": new_id,
            "title": title,
            "book_description": snippet,
            "main_image_path": new_rd.main_image_path if new_rd else None,
        })
        data.dirty = True
        # Update UI
        self.recipe_list.mark_recipe_in_book(recipe_id)
        self._add_to_book_count += 1
        self._add_to_book_counter.setText(f"{self._add_to_book_count} added")
        self._add_to_book_counter.setStyleSheet(
            "color: #ffffff; font-size: 13px; padding: 0 8px;"
        )
        self._show_toast(f"Added {title} to {cat.name}")

    def _on_add_to_book_back(self):
        """Return from add-to-book mode to book view."""
        # Disconnect signal to avoid duplicate connections
        try:
            self.recipe_list.add_to_book.disconnect(self._on_add_recipe_to_book)
        except RuntimeError:
            pass
        self.recipe_list.exit_add_to_book_mode()
        self._add_to_book_data = None
        self.show_book_view_from_add()

    def _show_new_recipe(self, recipe_data):
        """Show recipe detail view in add mode for a new recipe."""
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.recipe_detail)
        self._cb_opacity.setOpacity(1.0)
        self._sn_opacity.setOpacity(1.0)
        # Move command bar back to central widget as overlay
        self.command_bar.setParent(self.centralWidget())
        self.command_bar.show()
        self.command_bar.raise_()
        self.step_navigator.show()
        self._configure_recipe_detail_commands()

        # Load the new (empty) recipe
        self.recipe_detail.load_recipe(recipe_data)
        self.recipe_detail.set_layout_mode("both")
        # +1 for the intro step at index 0
        num_nav_steps = len(recipe_data.steps) + 1
        self.step_navigator.load_steps(recipe_id=None, num_steps=num_nav_steps)
        self._update_play_video_state(0)
        self._update_step_indicator(0)

        # Enter edit mode immediately (this is "add mode")
        self.recipe_detail.set_editing(True)
        self._back_btn.hide()
        self._edit_btn.hide()  # No edit button in add mode (already editing)
        self._grocery_list_btn.hide()
        self._save_btn.show()
        self._cancel_btn.show()
        self._edit_separator.show()
        self._image_btn.show()
        self._video_btn.show()
        self._update_video_button_state(0)  # Disable video button on intro step
        self._insert_step_btn.show()
        self._append_step_btn.show()
        self._delete_step_btn.show()
        self._paste_clipboard_btn.show()
        self._moiety_btn.show()
        self.step_navigator.set_drag_enabled(True)

        # Force layout recalculation
        self.command_bar.updateGeometry()
        QApplication.processEvents()
        self._position_overlays()
        # Don't auto-hide bars in edit mode
        self._autohide_timer.stop()

    def show_video_player(self):
        """Switch to video player view and hide command bar/step navigator."""
        self._autohide_timer.stop()
        self._stop_fade_animations()
        # VP claims BT headset audio output for echo cancellation,
        # blocking video audio.  Use QAudioSource during playback.
        self._voice.set_force_qaudio(True)
        self._voice.set_active_view("video_player")
        self._info_panel.set_view("video_player")
        self._info_panel.hide()
        self.stacked_widget.setCurrentWidget(self.video_player)
        self.command_bar.hide()
        self.step_navigator.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        # Promote overlays to top-level tool windows so they render
        # above QVideoWidget's native macOS rendering surface.
        self._promote_voice_indicator()
        self._promote_info_panel()
        self._position_overlays()

    def _stop_fade_animations(self):
        """Stop any running fade animations and disable opacity effects."""
        for attr in ("_fade_anim_cb", "_fade_anim_sn"):
            anim = getattr(self, attr)
            if anim is not None:
                anim.stop()
                setattr(self, attr, None)
        self._cb_opacity.setEnabled(False)
        self._sn_opacity.setEnabled(False)

    # ------------------------------------------------------------------
    # Command bar configurations
    # ------------------------------------------------------------------

    def _configure_recipe_list_commands(self):
        """Configure command bar buttons for recipe list view."""
        self.play_video_toggle = None
        self.command_bar.clear()
        _icon_style = "QPushButton { min-width: 38px; max-width: 38px; padding: 0px; }"
        self._search_field = self.command_bar.add_search("Search recipes...", self._on_search)
        self._tags_btn = self.command_bar.add_button(
            "", self._on_show_tags_filter, tooltip="Tags",
            icon=platform_icon("tag", weight="regular", point_size=48, color="white")
        )
        self._tags_btn.setStyleSheet(_icon_style)
        self._clear_filters_btn = self.command_bar.add_button(
            "", self._on_clear_filters, tooltip="Clear Filters",
            icon=platform_icon("xmark.circle", weight="regular", point_size=48, color="white")
        )
        self._clear_filters_btn.setStyleSheet(_icon_style)
        self._clear_filters_btn.setVisible(self.recipe_list.has_active_filters())
        btn = self.command_bar.add_button(
            "", self._on_new_recipe, tooltip="New Recipe",
            icon=platform_icon("plus.circle", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_new_book, tooltip="New Book",
            icon=platform_icon("book", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_new_article, tooltip="New Article",
            icon=platform_icon("square.and.pencil", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_import_recipe, tooltip="Import",
            icon=platform_icon("square.and.arrow.down", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_show_grocery_list_from_recipe_list, tooltip="Grocery List",
            icon=platform_icon("list.bullet", weight="regular", point_size=48, color="white", windows_name="list.bullet.clipboard.fill")
        )
        btn.setStyleSheet(_icon_style)
        self._view_clipboard_btn = self.command_bar.add_button(
            "", self._show_clipboard, tooltip="Clipboard",
            icon=platform_icon("pencil.and.list.clipboard", weight="regular", point_size=48, color="white", windows_name="ClipboardList")
        )
        self._view_clipboard_btn.setStyleSheet(_icon_style)
        self._view_clipboard_btn.setEnabled(self._clipboard_data is not None)
        _toggle_style = "QPushButton { min-width: 48px; max-width: 48px; padding: 0px; }"
        btn = self.command_bar.add_button(
            "", self._on_toggle_community, tooltip="Community",
            icon=platform_icon("person.3.sequence", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_toggle_style)
        self._review_btn = self.command_bar.add_button(
            "", self._enter_review_mode, tooltip="Review",
            icon=platform_icon("checkmark.circle", weight="regular", point_size=48, color="white")
        )
        self._review_btn.setStyleSheet(_icon_style)
        self._review_btn.setVisible(self._is_admin)
        # Subscription status label (right-aligned, muted)
        self._sub_status_label = QLabel()
        self._sub_status_label.setStyleSheet(
            "color: #bbbbbb; font-size: 11px; background: transparent; "
            "padding: 0 4px;"
        )
        self._sub_status_label.hide()
        self.command_bar.add_widget(self._sub_status_label)
        self._update_subscription_label()

        # Account / Sign In button
        self._account_btn = self.command_bar.add_button(
            "", self._on_account_btn_clicked,
            tooltip=self._auth_email or "Sign In",
            icon=platform_icon("person.crop.circle", weight="regular", point_size=48, color="white")
        )
        self._account_btn.setStyleSheet(_icon_style)
        # TTS toggle (visible only when mic is on)
        self._tts_toggle = self._create_tts_toggle()
        # Microphone toggle for always-on voice listening
        if platform.system() == "Windows":
            mic_icon = "\uE720"
            self._mic_font_family = '"Segoe Fluent Icons", "Segoe MDL2 Assets"'
        else:
            mic_icon = ""
            self._mic_font_family = ""
        self._mic_toggle = self.command_bar.add_toggle_button(
            mic_icon, self._on_mic_toggled, size=38, tooltip="Toggle voice listening"
        )
        if platform.system() == "Darwin":
            from PySide6.QtCore import QSize
            self._mic_sf_icon = sf_symbol("microphone", point_size=16, color="#cccccc")
            self._mic_toggle.setIcon(self._mic_sf_icon)
            self._mic_toggle.setIconSize(QSize(20, 20))
        self._update_mic_button_style()
        self._mic_toggle.setChecked(self._is_voice_listening)
        # Hide voice controls in recipe list — shown in recipe detail only
        self._mic_toggle.hide()
        self._tts_toggle.hide()
        # Hands-free toggle (visible only when mic is on)
        self._hands_free_toggle = self._create_hands_free_toggle()
        self._hands_free_toggle.hide()
        # Headset mode toggle (visible only when mic is on)
        self._headset_toggle = self._create_headset_toggle()
        self._headset_toggle.hide()
        # Refresh audio button (visible only when mic is on)
        self._refresh_audio_btn = self._create_refresh_audio_button()
        self._refresh_audio_btn.hide()
        if platform.system() == "Windows":
            fs_btn = self.command_bar.add_button("", self._toggle_fullscreen, tooltip="Fullscreen",
                                                 icon=winui_icon("Fullscreen", point_size=48, color="white"))
            fs_btn.setStyleSheet(_icon_style)
        # Connect filter changes to update Clear button visibility
        self.recipe_list.filtersChanged.connect(self._on_filters_changed)

    def _configure_recipe_detail_commands(self):
        """Configure command bar buttons for recipe detail view."""
        # Reset link button visibility (may have been hidden by article mode)
        self.recipe_detail.directions_editor.btn_link_step.setVisible(True)
        self.recipe_detail.directions_editor.btn_link_web.setVisible(True)
        self.command_bar.clear()
        icon_btn_style_20 = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 20px;
            }
        """
        icon_btn_style_16 = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
        """
        self._back_btn = self.command_bar.add_button("", self._on_back, tooltip="Back",
                                                      icon=platform_icon("arrow.left", weight="regular", point_size=48, color="white", windows_name="ArrowLeft8"))
        self._back_btn.setFixedSize(32, 32)
        self._back_btn.setStyleSheet(icon_btn_style_20)
        self._save_btn = self.command_bar.add_button("", self._on_save_clicked, tooltip="Save",
                                                      icon=platform_icon("square.and.arrow.down", weight="regular", point_size=48, color="white"))
        self._save_btn.setFixedSize(32, 32)
        self._save_btn.setStyleSheet(icon_btn_style_16)
        self._save_btn.hide()
        self._cancel_btn = self.command_bar.add_button("", self._on_edit_cancel, tooltip="Cancel",
                                                        icon=platform_icon("xmark.square", weight="regular", point_size=48, color="white"))
        self._cancel_btn.setFixedSize(32, 32)
        self._cancel_btn.setStyleSheet(icon_btn_style_16)
        self._cancel_btn.hide()
        # Thin separator after save/cancel group
        self._edit_separator = QWidget()
        self._edit_separator.setFixedSize(7, 24)
        self._edit_separator.setStyleSheet("border-left: 1px solid #888888; margin-left: 3px; margin-right: 3px;")
        self.command_bar.layout.insertWidget(len(self.command_bar.command_widgets), self._edit_separator)
        self.command_bar.command_widgets.append(self._edit_separator)
        self._edit_separator.hide()
        self.play_video_toggle = self.command_bar.add_toggle_button(
            "▶", self._on_play_video_toggled, size=32, tooltip="Play Video"
        )
        self.play_video_toggle.setStyleSheet("""
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                color: #cccccc;
            }
            QPushButton:checked {
                background-color: #4a4a4a;
                border: 1px solid #888888;
                color: white;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:disabled {
                color: #555555;
                background-color: #1a1a1a;
            }
        """)
        self.play_video_toggle.setEnabled(False)
        self._edit_btn = self.command_bar.add_button("", self._on_edit_recipe, tooltip="Edit",
                                                      icon=platform_icon("pencil", weight="regular", point_size=48, color="white"))
        self._edit_btn.setStyleSheet("""
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
        """)
        clipboard_btn_style = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                color: white;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:disabled {
                color: #555555;
                background-color: transparent;
            }
        """
        self._copy_steps_btn = self.command_bar.add_button(
            "", self._on_copy_selected_steps, tooltip="Copy to clipboard",
            icon=platform_icon("pencil.and.list.clipboard", weight="regular", point_size=48, color="white", windows_name="Copy")
        )
        self._copy_steps_btn.setFixedSize(32, 32)
        self._copy_steps_btn.setStyleSheet(clipboard_btn_style)
        self._copy_steps_btn.setEnabled(len(self.step_navigator.selected_steps) > 0)
        self._view_clipboard_btn = self.command_bar.add_button(
            "", self._show_clipboard, tooltip="View Clipboard",
            icon=platform_icon("list.bullet.clipboard.fill", weight="black", point_size=48, color="white", windows_name="ClipboardList")
        )
        self._view_clipboard_btn.setFixedSize(32, 32)
        self._view_clipboard_btn.setStyleSheet(clipboard_btn_style)
        self._view_clipboard_btn.setEnabled(self._clipboard_data is not None)
        self._layout_mode_btn = self.command_bar.add_menu_button(
            [
                ("Ingredients && Directions", "both"),
                ("Ingredients", "ingredients"),
                ("Directions", "directions"),
                ("Image", "image"),
                ("Tags", "tags"),
            ],
            self._on_layout_mode_changed,
            tooltip="Customize view",
        )
        # Fixed width so button doesn't resize when switching modes
        fm = self._layout_mode_btn.fontMetrics()
        fixed_w = fm.horizontalAdvance("Ingredients & Directions  ▾") + 40
        self._layout_mode_btn.setFixedWidth(fixed_w)
        self._layout_mode_btn.setStyleSheet(f"""
            QPushButton {{
                min-width: {fixed_w}px;
                max-width: {fixed_w}px;
            }}
        """)
        self._update_layout_mode_menu(step_index=0)  # Start on intro step
        spaced_icon_btn_style = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                margin-left: 5px;
                font-size: 14px;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
        """
        icon_btn_style = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 14px;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
        """
        font_down_btn = self.command_bar.add_button(
            "A-", lambda: self._adjust_font_size(-1),
            tooltip="Decrease font size",
        )
        font_down_btn.setFixedSize(32, 32)
        font_down_btn.setStyleSheet(icon_btn_style)
        font_up_btn = self.command_bar.add_button(
            "A+", lambda: self._adjust_font_size(1),
            tooltip="Increase font size",
        )
        font_up_btn.setFixedSize(32, 32)
        font_up_btn.setStyleSheet(icon_btn_style)
        font_separator = QWidget()
        font_separator.setFixedSize(7, 24)
        font_separator.setStyleSheet("border-left: 1px solid #888888; margin-left: 3px; margin-right: 3px;")
        self.command_bar.layout.insertWidget(len(self.command_bar.command_widgets), font_separator)
        self.command_bar.command_widgets.append(font_separator)
        self._image_btn = self.command_bar.add_button("", self._on_image_btn_clicked, tooltip="Modify image",
                                                       icon=platform_icon("photo.badge.arrow.down", weight="regular", point_size=48, color="white"))
        from PySide6.QtCore import QSize
        self._image_btn.setFixedSize(38, 32)
        self._image_btn.setIconSize(QSize(28, 28))
        self._image_btn.setStyleSheet(icon_btn_style)
        self._image_btn.hide()
        self._video_btn = self.command_bar.add_button("", self._on_video_btn_clicked, tooltip="Video options",
                                                       icon=platform_icon("video", weight="regular", point_size=48, color="white"))
        self._video_btn.setFixedSize(32, 32)
        self._video_btn.setStyleSheet(spaced_icon_btn_style)
        self._video_btn.hide()
        self._insert_step_btn = self.command_bar.add_button(
            "", self._on_insert_step, tooltip="Insert step",
            icon=platform_icon("plus.rectangle.on.rectangle", weight="regular", point_size=48, color="white", windows_name="InsertStep")
        )
        self._insert_step_btn.setFixedSize(32, 32)
        self._insert_step_btn.setStyleSheet(spaced_icon_btn_style)
        self._insert_step_btn.hide()
        self._append_step_btn = self.command_bar.add_button(
            "", self._on_append_step, tooltip="Append step",
            icon=platform_icon("plus.rectangle.on.folder", weight="regular", point_size=48, color="white", windows_name="AppendStep")
        )
        self._append_step_btn.setFixedSize(32, 32)
        self._append_step_btn.setStyleSheet(spaced_icon_btn_style)
        self._append_step_btn.hide()
        self._delete_step_btn = self.command_bar.add_button(
            "", self._on_delete_steps, tooltip="Delete step",
            icon=platform_icon("delete.backward", weight="regular", point_size=48, color="white")
        )
        self._delete_step_btn.setFixedSize(32, 32)
        self._delete_step_btn.setStyleSheet(spaced_icon_btn_style)
        self._delete_step_btn.hide()
        self._paste_clipboard_btn = self.command_bar.add_button(
            "", self._on_paste_from_clipboard, tooltip="Insert from clipboard",
            icon=platform_icon("document.on.clipboard", weight="regular", point_size=48, color="white", windows_name="CopyTo")
        )
        self._paste_clipboard_btn.setFixedSize(32, 32)
        self._paste_clipboard_btn.setStyleSheet(spaced_icon_btn_style)
        self._paste_clipboard_btn.hide()
        self._moiety_btn = self.command_bar.add_button(
            "", self._on_moiety_btn_clicked, tooltip="Moieties",
            icon=platform_icon("puzzlepiece", weight="regular", point_size=48, color="white")
        )
        self._moiety_btn.setFixedSize(32, 32)
        self._moiety_btn.setStyleSheet(spaced_icon_btn_style)
        self._moiety_btn.hide()
        self._grocery_list_btn = self.command_bar.add_button(
            "", self._on_show_grocery_list_from_detail, tooltip="Grocery List",
            icon=platform_icon("list.bullet", weight="regular", point_size=48, color="white", windows_name="list.bullet.clipboard.fill")
        )
        self._grocery_list_btn.setFixedSize(32, 32)
        self._grocery_list_btn.setStyleSheet(icon_btn_style)
        # Community tips buttons (hidden by default, shown for community recipes)
        self._view_tips_btn = self.command_bar.add_button(
            "", self._on_view_tips, tooltip="View community tips",
            icon=platform_icon("pencil.tip.crop.circle", weight="regular", point_size=48, color="white")
        )
        self._view_tips_btn.setFixedSize(32, 32)
        self._view_tips_btn.setStyleSheet(icon_btn_style)
        self._view_tips_btn.hide()
        self._add_tip_btn = self.command_bar.add_button(
            "", self._on_add_tip, tooltip="Add a tip",
            icon=platform_icon("pencil.tip.crop.circle.badge.plus", weight="regular", point_size=48, color="white")
        )
        self._add_tip_btn.setFixedSize(32, 32)
        self._add_tip_btn.setStyleSheet(icon_btn_style)
        self._add_tip_btn.hide()
        self.command_bar.add_stretch()
        # Voice command help toggle
        self._help_btn = self.command_bar.add_button(
            "?", self._on_help_toggled, tooltip="Voice commands",
        )
        self._help_btn.setFixedSize(32, 32)
        self._help_btn.setStyleSheet(icon_btn_style)
        # TTS toggle (visible only when mic is on)
        self._tts_toggle = self._create_tts_toggle()
        # Microphone toggle for always-on voice listening
        if platform.system() == "Windows":
            mic_icon = "\uE720"
            self._mic_font_family = '"Segoe Fluent Icons", "Segoe MDL2 Assets"'
        else:
            mic_icon = ""
            self._mic_font_family = ""
        self._mic_toggle = self.command_bar.add_toggle_button(
            mic_icon, self._on_mic_toggled, size=38, tooltip="Toggle voice listening"
        )
        if platform.system() == "Darwin":
            self._mic_sf_icon = sf_symbol("microphone", point_size=16, color="#cccccc")
            self._mic_toggle.setIcon(self._mic_sf_icon)
            self._mic_toggle.setIconSize(QSize(20, 20))
        self._update_mic_button_style()
        # Update mic toggle state if voice is already listening
        self._mic_toggle.setChecked(self._is_voice_listening)
        # Hands-free toggle (visible only when mic is on)
        self._hands_free_toggle = self._create_hands_free_toggle()
        # Headset mode toggle (visible only when mic is on)
        self._headset_toggle = self._create_headset_toggle()
        # Refresh audio button (visible only when mic is on)
        self._refresh_audio_btn = self._create_refresh_audio_button()
        self._detail_fs_btn = None
        if platform.system() == "Windows":
            self._detail_fs_btn = self.command_bar.add_button("", self._toggle_fullscreen, tooltip="Fullscreen",
                                                              icon=winui_icon("Fullscreen", point_size=48, color="white"))
            self._detail_fs_btn.setFixedSize(32, 32)
            self._detail_fs_btn.setStyleSheet(icon_btn_style)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_search(self, text):
        """Handle search — dispatch to community or local."""
        if self.stacked_widget.currentWidget() is self.community_home:
            self._home_search_query = text
            if text.strip() or self._home_search_tags or self._home_search_cuisines:
                self._fetch_home_search(fresh=True)
            elif self.community_home.in_search_mode:
                self._on_home_search_back()
            return
        self.recipe_list.filter_recipes(text)

    def _on_show_tags_filter(self):
        """Toggle the tag filter panel — dispatch to community or local."""
        if self.stacked_widget.currentWidget() is self.community_home:
            self.community_home.toggle_tag_panel()
            return
        self.recipe_list.toggle_tag_side_panel()

    def _on_clear_filters(self):
        """Clear all active filters — dispatch to community or local."""
        if self.stacked_widget.currentWidget() is self.community_home:
            self._home_search_query = ""
            self._home_search_tags.clear()
            self._home_search_cuisines.clear()
            self._home_search_cursor = None
            self._home_search_active = False
            if hasattr(self, "_search_field") and self._search_field:
                self._search_field.clear()
            self.community_home.tag_side_panel.set_selections([], [], [])
            if self.community_home.in_search_mode:
                self.community_home.exit_search_mode()
            try:
                if hasattr(self, "_clear_filters_btn") and self._clear_filters_btn:
                    self._clear_filters_btn.setVisible(False)
            except RuntimeError:
                pass
            return
        self.recipe_list.clear_all_filters()
        if hasattr(self, "_search_field") and self._search_field:
            self._search_field.clear()

    def _on_filters_changed(self):
        """Update UI when filters change."""
        if self._community_mode:
            # In community mode, tag/producer/cuisine changes trigger a fresh API call
            self._community_tags = self.recipe_list.tag_side_panel.get_selected_tags()
            self._community_producers = self.recipe_list.tag_side_panel.get_selected_producers()
            self._community_cuisines = self.recipe_list.tag_side_panel.get_selected_cuisines()
            if hasattr(self, "_clear_filters_btn") and self._clear_filters_btn:
                self._clear_filters_btn.setVisible(
                    bool(self._community_search_query or self._community_tags
                         or self._community_producers or self._community_cuisines)
                )
            self._fetch_community_page(fresh=True)
            return
        try:
            if hasattr(self, "_clear_filters_btn") and self._clear_filters_btn:
                self._clear_filters_btn.setVisible(self.recipe_list.has_active_filters())
        except RuntimeError:
            pass  # Widget destroyed during mode transition

    # ------------------------------------------------------------------
    # Community browse mode
    # ------------------------------------------------------------------

    def _on_toggle_community(self):
        """Toggle between local recipes and community browse mode."""
        if self.stacked_widget.currentWidget() is self.community_home:
            self._exit_community_mode()
        else:
            self._enter_community_mode()

    def _enter_community_mode(self):
        """Switch to community homepage view."""
        self._stop_fade_animations()
        self._cb_opacity.setOpacity(1.0)
        self._tips_pulse_checked_id = None
        self._stop_tips_pulse()
        self._community_mode = True
        self._feed_cursor = None
        self._home_error_count = 0
        self._home_auto_retry_count = 0
        self.community_home.hide_retry_banner()
        # Reset homepage search state
        self._home_search_query = ""
        self._home_search_tags.clear()
        self._home_search_cuisines.clear()
        self._home_search_cursor = None
        self._home_search_active = False
        self.community_home.clear_all()
        self.stacked_widget.setCurrentWidget(self.community_home)
        self._configure_community_home_commands()
        self.community_home.set_command_bar(self.command_bar)
        # Show skeletons after view is visible (valid geometry)
        QTimer.singleShot(0, self.community_home.show_all_skeletons)
        # Trigger thumbnail loading on scroll
        self.community_home.scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_community_scroll_thumb
        )
        # Fire all section fetches in parallel
        self._community_api.fetch_carousel()
        self._community_api.fetch_stats()
        self._community_api.fetch_books(limit=8)
        self._community_api.fetch_creators()
        self._community_api.fetch_articles(limit=8)
        self._community_api.fetch_moieties(limit=8)
        self._community_api.fetch_feed()
        self._community_api.fetch_tags()
        self._community_api.fetch_cuisines()
        if self._auth_id_token:
            self._community_api.fetch_purchases()
        self._position_overlays()

    def _exit_community_mode(self):
        """Return to local recipe list."""
        self._community_mode = False
        self._stop_purchase_polling()
        self._community_api.cancel_thumbnails()
        self._community_api.cancel_download()
        self._home_thumb_cache.clear()
        try:
            self.community_home.scroll_area.verticalScrollBar().valueChanged.disconnect(
                self._on_community_scroll_thumb
            )
        except RuntimeError:
            pass
        self.show_recipe_list()

    def _configure_community_home_commands(self):
        """Configure command bar for the community homepage."""
        self.play_video_toggle = None
        self.command_bar.clear()
        _icon_style = "QPushButton { min-width: 38px; max-width: 38px; padding: 0px; }"
        self._search_field = self.command_bar.add_search("Search community...", self._on_search)
        self._tags_btn = self.command_bar.add_button(
            "", self._on_show_tags_filter, tooltip="Tags",
            icon=platform_icon("tag", weight="regular", point_size=48, color="white")
        )
        self._tags_btn.setStyleSheet(_icon_style)
        self._clear_filters_btn = self.command_bar.add_button(
            "", self._on_clear_filters, tooltip="Clear Filters",
            icon=platform_icon("xmark.circle", weight="regular", point_size=48, color="white")
        )
        self._clear_filters_btn.setStyleSheet(_icon_style)
        self._clear_filters_btn.setVisible(self._home_search_active)
        btn = self.command_bar.add_button(
            "", self._on_new_recipe, tooltip="New Recipe",
            icon=platform_icon("plus.circle", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_new_book, tooltip="New Book",
            icon=platform_icon("book", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_new_article, tooltip="New Article",
            icon=platform_icon("square.and.pencil", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_import_recipe, tooltip="Import",
            icon=platform_icon("square.and.arrow.down", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_icon_style)
        btn = self.command_bar.add_button(
            "", self._on_show_grocery_list_from_recipe_list, tooltip="Grocery List",
            icon=platform_icon("list.bullet", weight="regular", point_size=48, color="white", windows_name="list.bullet.clipboard.fill")
        )
        btn.setStyleSheet(_icon_style)
        self._view_clipboard_btn = self.command_bar.add_button(
            "", self._show_clipboard, tooltip="Clipboard",
            icon=platform_icon("pencil.and.list.clipboard", weight="regular", point_size=48, color="white", windows_name="ClipboardList")
        )
        self._view_clipboard_btn.setStyleSheet(_icon_style)
        self._view_clipboard_btn.setEnabled(self._clipboard_data is not None)
        _toggle_style = "QPushButton { min-width: 48px; max-width: 48px; padding: 0px; }"
        btn = self.command_bar.add_button(
            "", self._on_toggle_community, tooltip="Library",
            icon=platform_icon("books.vertical", weight="regular", point_size=48, color="white")
        )
        btn.setStyleSheet(_toggle_style)
        self._review_btn = self.command_bar.add_button(
            "", self._enter_review_mode, tooltip="Review",
            icon=platform_icon("checkmark.circle", weight="regular", point_size=48, color="white")
        )
        self._review_btn.setStyleSheet(_icon_style)
        self._review_btn.setVisible(self._is_admin)
        # Subscription status label
        self._sub_status_label = QLabel()
        self._sub_status_label.setStyleSheet(
            "color: #bbbbbb; font-size: 11px; background: transparent; "
            "padding: 0 4px;"
        )
        self._sub_status_label.hide()
        self.command_bar.add_widget(self._sub_status_label)
        self._update_subscription_label()
        # Account / Sign In button
        self._account_btn = self.command_bar.add_button(
            "", self._on_account_btn_clicked,
            tooltip=self._auth_email or "Sign In",
            icon=platform_icon("person.crop.circle", weight="regular", point_size=48, color="white")
        )
        self._account_btn.setStyleSheet(_icon_style)
        if platform.system() == "Windows":
            fs_btn = self.command_bar.add_button("", self._toggle_fullscreen, tooltip="Fullscreen",
                                                 icon=winui_icon("Fullscreen", point_size=48, color="white"))
            fs_btn.setStyleSheet(_icon_style)

    # ------------------------------------------------------------------
    # Community homepage signal handlers
    # ------------------------------------------------------------------

    def _on_home_carousel_loaded(self, items):
        if not self._community_mode:
            return
        self._mark_purchased(items)
        self.community_home.carousel.set_items(items)
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_stats_loaded(self, data):
        if not self._community_mode:
            return
        self.community_home.stats_section.set_stats(data)

    def _on_home_books_loaded(self, items):
        if not self._community_mode:
            return
        self._mark_purchased(items)
        self.community_home.book_row.set_items(items)
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_creators_loaded(self, creators):
        if not self._community_mode:
            return
        self.community_home.creator_row.set_creator_items(creators)
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_articles_loaded(self, items):
        if not self._community_mode:
            return
        self._mark_purchased(items)
        self.community_home.article_row.set_items(items)
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_moieties_loaded(self, items):
        if not self._community_mode:
            return
        self._mark_purchased(items)
        self.community_home.moiety_row.set_items(items)
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_feed_loaded(self, items, next_cursor):
        if not self._community_mode:
            return
        self._mark_purchased(items)
        if self._feed_cursor is None:
            self.community_home.feed_section.set_items(
                items, has_more=next_cursor is not None
            )
        else:
            self.community_home.feed_section.append_items(
                items, has_more=next_cursor is not None
            )
        self._feed_cursor = next_cursor
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_section_error(self, _message):
        if not self._community_mode:
            return
        self._home_error_count += 1
        # Auto-retry up to 2 times on any section failure, with increasing delay
        if self._home_auto_retry_count < 2:
            self._home_auto_retry_count += 1
            delay = self._home_auto_retry_count * 2000  # 2s, then 4s
            QTimer.singleShot(delay, self._auto_retry_community)
        # Show manual retry banner once a majority of sections have failed (5 of 8)
        elif self._home_error_count >= 5:
            self.community_home.show_retry_banner()

    def _auto_retry_community(self):
        """Automatic retry after a section load failure."""
        if not self._community_mode:
            return
        self._home_error_count = 0
        self.community_home.show_all_skeletons()
        self._community_api.fetch_carousel()
        self._community_api.fetch_stats()
        self._community_api.fetch_books(limit=8)
        self._community_api.fetch_creators()
        self._community_api.fetch_articles(limit=8)
        self._community_api.fetch_moieties(limit=8)
        self._community_api.fetch_feed()
        self._community_api.fetch_tags()
        self._community_api.fetch_cuisines()

    def _on_home_retry(self):
        self._home_error_count = 0
        self._home_auto_retry_count = 0
        self.community_home.show_all_skeletons()
        self._community_api.fetch_carousel()
        self._community_api.fetch_stats()
        self._community_api.fetch_books(limit=8)
        self._community_api.fetch_creators()
        self._community_api.fetch_articles(limit=8)
        self._community_api.fetch_moieties(limit=8)
        self._community_api.fetch_feed()
        self._community_api.fetch_tags()
        self._community_api.fetch_cuisines()

    def _on_home_load_more(self):
        if self._feed_cursor:
            self._community_api.fetch_feed(cursor=self._feed_cursor)

    def _on_home_card_clicked(self, community_id):
        """Navigate to CommunityDetailView from a homepage card click."""
        # Find the item data from any section
        for card in self.community_home.all_cards():
            if hasattr(card, 'community_id') and card.community_id == community_id:
                if hasattr(card, '_item'):
                    item = card._item
                    # Articles open on the website, not in-app
                    if item.get("type") == "article":
                        from PySide6.QtGui import QDesktopServices
                        QDesktopServices.openUrl(QUrl(f"https://foodiemoiety.com/articles/{community_id}"))
                        return
                    # Get thumbnail pixmap if available
                    pixmap = None
                    if hasattr(card, '_image_label') and card._image_label.pixmap():
                        pixmap = card._image_label.pixmap()
                    self._show_community_detail(item, pixmap)
                    return

    def _on_creator_clicked(self, profile_slug):
        """Open creator's profile page on the website."""
        if profile_slug:
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(f"{WEBSITE_URL}/creators/{profile_slug}"))

    # ------------------------------------------------------------------
    # Community homepage search handlers
    # ------------------------------------------------------------------

    def _on_community_tags_loaded(self, tags):
        """Populate community tag panel with API tags."""
        self._api_tags = tags
        self.community_home.tag_side_panel.set_external_tags(
            tags, cuisines=self._api_cuisines or None,
        )

    def _on_community_cuisines_loaded(self, cuisines):
        """Add cuisine row to community tag panel."""
        self._api_cuisines = cuisines
        self.community_home.tag_side_panel.set_external_tags(
            self._api_tags, cuisines=cuisines,
        )

    def _on_home_tags_changed(self):
        """Handle tag/cuisine selection changes on community homepage."""
        panel = self.community_home.tag_side_panel
        self._home_search_tags = panel.get_selected_tags()
        self._home_search_cuisines = panel.get_selected_cuisines()
        has_filters = bool(self._home_search_query or self._home_search_tags
                          or self._home_search_cuisines)
        try:
            if hasattr(self, "_clear_filters_btn") and self._clear_filters_btn:
                self._clear_filters_btn.setVisible(has_filters)
        except RuntimeError:
            pass
        if has_filters:
            self._fetch_home_search(fresh=True)
        elif self.community_home.in_search_mode:
            self.community_home.exit_search_mode()
            self._home_search_active = False

    def _fetch_home_search(self, fresh=False):
        """Execute a community search from the homepage."""
        if fresh:
            self._home_search_cursor = None
        self._home_search_active = True
        if not self.community_home.in_search_mode:
            self.community_home.enter_search_mode("Searching...")
        self._community_api.fetch_search(
            query=self._home_search_query,
            tags=self._home_search_tags if self._home_search_tags else None,
            cuisines=self._home_search_cuisines if self._home_search_cuisines else None,
            cursor=self._home_search_cursor,
            limit=20,
        )

    def _on_home_search_loaded(self, items, next_cursor):
        """Handle search results from community API."""
        if not self._community_mode or not self._home_search_active:
            return
        self._mark_purchased(items)
        fresh = self._home_search_cursor is None
        self._home_search_cursor = next_cursor
        count_text = f"{len(items)} result{'s' if len(items) != 1 else ''}"
        if not fresh:
            # Append — update label with total
            existing = len(self.community_home._search_section.get_cards())
            total = existing + len(items)
            count_text = f"{total} result{'s' if total != 1 else ''}"
        # Build label from active filters
        parts = []
        if self._home_search_query:
            parts.append(f'"{self._home_search_query}"')
        if self._home_search_tags:
            parts.append(", ".join(self._home_search_tags))
        label = " · ".join(parts) if parts else "Search Results"
        self.community_home.set_search_label(f"{label} — {count_text}")
        if fresh:
            self.community_home.set_search_results(
                items, has_more=next_cursor is not None
            )
        else:
            self.community_home.append_search_results(
                items, has_more=next_cursor is not None
            )
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_home_search_load_more(self):
        """Fetch next page of search results."""
        if self._home_search_cursor:
            self._fetch_home_search()

    def _on_home_search_back(self):
        """Return from search results to homepage sections."""
        self._home_search_query = ""
        self._home_search_tags.clear()
        self._home_search_cuisines.clear()
        self._home_search_cursor = None
        self._home_search_active = False
        if hasattr(self, "_search_field") and self._search_field:
            self._search_field.clear()
        self.community_home.tag_side_panel.set_selections([], [], [])
        self.community_home.exit_search_mode()
        try:
            if hasattr(self, "_clear_filters_btn") and self._clear_filters_btn:
                self._clear_filters_btn.setVisible(False)
        except RuntimeError:
            pass

    def _configure_community_commands(self):
        """Configure command bar buttons for community browse mode."""
        self.play_video_toggle = None
        self.command_bar.clear()
        self.command_bar.add_button(
            "Back", self._on_toggle_community,
            icon=platform_icon("chevron.left", weight="regular", point_size=48, color="white")
        )
        self.recipe_list.show_mode_banner("COMMUNITY RECIPES", "#1a3a6a")
        self._search_field = self.command_bar.add_search(
            "Search community...", self._on_community_search
        )
        self._tags_btn = self.command_bar.add_button(
            "Tags  \u25be", self._on_community_tags_filter,
            icon=platform_icon("tag", weight="regular", point_size=48, color="white")
        )
        self._clear_filters_btn = self.command_bar.add_button(
            "Clear", self._on_community_clear_filters
        )
        self._clear_filters_btn.hide()
        sort_items = [
            ("Recent", "recent"),
            ("Oldest", "oldest"),
        ]
        self.command_bar.add_menu_button(
            sort_items, self._on_community_sort,
            tooltip="Sort order",
        )
        self.command_bar.add_stretch()
        # Connect filter changes to update Clear button visibility
        self.recipe_list.filtersChanged.connect(self._on_filters_changed)

    def _on_community_search(self, text):
        """Handle search in community mode."""
        self._community_search_query = text
        self._fetch_community_page(fresh=True)

    def _on_community_tags_filter(self):
        """Toggle community tags panel."""
        panel = self.recipe_list.tag_side_panel
        if panel.isVisible():
            panel.hide()
        else:
            # Collect unique tags, producers, and cuisines from loaded items
            all_tags = set()
            all_producers = set()
            all_cuisines = set()
            for item in self.recipe_list._community_items:
                for tag in item.get("tags", []):
                    all_tags.add(tag)
                producer = item.get("producer", "")
                if producer:
                    all_producers.add(producer)
                cuisine = item.get("cuisine_type", "")
                if cuisine:
                    all_cuisines.add(cuisine)
            panel.set_external_tags(
                sorted(all_tags, key=str.lower),
                producers=sorted(all_producers, key=str.lower),
                cuisines=sorted(all_cuisines, key=str.lower),
            )
            panel.set_selected_tags(self._community_tags)
            panel.set_selected_producers(self._community_producers)
            panel.set_selected_cuisines(self._community_cuisines)
            panel._update_clear_btn()
            self.recipe_list._position_tag_panel()
            panel.show()
            panel.raise_()

    def _on_community_clear_filters(self):
        """Clear community filters."""
        self._community_search_query = ""
        self._community_tags.clear()
        self._community_producers.clear()
        self._community_cuisines.clear()
        if hasattr(self, "_search_field") and self._search_field:
            self._search_field.clear()
        self._clear_filters_btn.hide()
        self._fetch_community_page(fresh=True)

    def _on_community_sort(self, sort_key):
        """Change community sort order."""
        if self._community_sort != sort_key:
            self._community_sort = sort_key
            self._fetch_community_page(fresh=True)

    def _fetch_community_page(self, fresh=False):
        """Fetch a page of community recipes from the API."""
        if fresh:
            self.recipe_list._community_cursor = None
            self.recipe_list._community_items.clear()
        self.recipe_list._community_loading = True
        self.recipe_list.show_loading()
        self._community_api.fetch_page(
            query=self._community_search_query,
            tags=self._community_tags if self._community_tags else None,
            sort=self._community_sort,
            cursor=self.recipe_list._community_cursor,
            producers=self._community_producers if self._community_producers else None,
            cuisines=self._community_cuisines if self._community_cuisines else None,
        )

    def _on_community_page_loaded(self, items, next_cursor):
        """Handle API page response."""
        if not self._community_mode:
            return
        # Mark purchased books before creating cards
        self._mark_purchased(items)
        append = self.recipe_list._community_cursor is not None
        self.recipe_list._community_cursor = next_cursor
        self.recipe_list._community_loading = False
        self.recipe_list.hide_loading()
        self.recipe_list.load_community_cards(items, append=append)
        # Trigger lazy thumbnail loading
        self._thumb_debounce.start()

    def _mark_purchased(self, items):
        """Stamp is_purchased on items that appear in _purchased_book_ids."""
        if self._purchased_book_ids:
            for item in items:
                if item.get("community_id") in self._purchased_book_ids:
                    item["is_purchased"] = True

    def _on_purchases_loaded(self, book_ids):
        """Handle purchases list — cache IDs and update existing cards."""
        self._purchased_book_ids = book_ids
        if not self._community_mode:
            return
        # Update any already-loaded cards + cached items
        for item in self.recipe_list._community_items:
            cid = item.get("community_id", "")
            if cid in book_ids:
                item["is_purchased"] = True
        for card in self.recipe_list._cards:
            cid = getattr(card, "_community_id", None)
            if cid and cid in book_ids:
                item_data = next(
                    (i for i in self.recipe_list._community_items
                     if i.get("community_id") == cid), None
                )
                if item_data:
                    card.set_price_info(
                        item_data.get("price_type", "free"),
                        item_data.get("price_cents", 0),
                        True,
                    )
        # Also update homepage cards
        for card in self.community_home.all_cards():
            if hasattr(card, 'community_id') and card.community_id in book_ids:
                if hasattr(card, '_item'):
                    card._item["is_purchased"] = True

    def _on_community_page_error(self, message):
        """Handle API error."""
        self.recipe_list._community_loading = False
        self.recipe_list.hide_loading()
        self._show_toast(f"Community: {message}")

    def _on_community_scroll_thumb(self, _value):
        """Debounce thumbnail requests on scroll."""
        self._thumb_debounce.start()

    def _request_visible_thumbnails(self):
        """Request thumbnails for community/review/homepage cards in the viewport."""
        # Homepage cards — load all eagerly (small count, nested scrollers
        # make viewport intersection checks unreliable)
        if self._community_mode and self.stacked_widget.currentWidget() is self.community_home:
            for card in self.community_home.all_cards():
                if card.has_thumbnail:
                    continue
                url = card.thumbnail_url
                if not url:
                    continue
                # Apply from cache if already downloaded
                cached = self._home_thumb_cache.get(card.community_id)
                if cached is not None:
                    card.set_thumbnail(cached)
                    continue
                self._community_api.fetch_thumbnail(card.community_id, url)
            return
        # Recipe list community/review cards
        if not self._community_mode and not self._review_mode:
            return
        viewport = self.recipe_list.scroll_area.viewport()
        viewport_rect = viewport.rect()
        for card in self.recipe_list._cards:
            if not card._community_mode and not card._review_mode:
                continue
            card_pos = card.mapTo(viewport, card.rect().topLeft())
            card_rect = QRect(card_pos, card.size())
            if not viewport_rect.intersects(card_rect):
                continue
            cid = card._community_id
            if not cid:
                continue
            if card._has_thumbnail:
                continue
            item = (self.recipe_list.get_community_item(cid)
                    or self.recipe_list.get_review_item(cid))
            if item and item.get("thumbnail_url"):
                self._community_api.fetch_thumbnail(cid, item["thumbnail_url"])

    def _on_community_thumbnail_loaded(self, community_id, raw_bytes):
        """Set thumbnail on ALL matching cards (homepage and recipe list)."""
        pixmap = QPixmap()
        pixmap.loadFromData(raw_bytes)
        if pixmap.isNull():
            return
        # Cache for homepage cards (carousel clones, same item in multiple rows)
        self._home_thumb_cache[community_id] = pixmap
        # Apply to every homepage card with this ID
        if self._community_mode:
            for card in self.community_home.all_cards():
                if card.community_id == community_id and not card.has_thumbnail:
                    card.set_thumbnail(pixmap)
        # Also apply to recipe list cards
        for card in self.recipe_list._cards:
            if card._community_id == community_id:
                card.set_thumbnail_pixmap(pixmap)
                break

    def _find_community_item(self, community_id):
        """Look up a community item from the library list or homepage cards."""
        item = self.recipe_list.get_community_item(community_id)
        if item:
            return item
        for card in self.community_home.all_cards():
            if hasattr(card, 'community_id') and card.community_id == community_id:
                if hasattr(card, '_item'):
                    return card._item
        return None

    def _on_community_download_requested(self, community_id):
        """Start downloading a community recipe/book zip.

        Fetches a fresh detail first to get a signed zipUrl, then downloads.
        """
        item = self._find_community_item(community_id)
        if not item:
            self._show_toast("Download URL not available")
            return
        # Guard: paid books require purchase
        if (item.get("price_type") == "paid"
                and not item.get("is_purchased")
                and not item.get("is_creator")):
            self._on_community_purchase_requested(community_id)
            return
        self._community_download_item = item  # stash for origin tracking
        title = item.get("title", "item")
        self._community_download_progress = self._create_styled_progress(
            f"Downloading '{title}'...", "Download",
            cancel_callback=self._community_api.cancel_download,
        )
        # Fetch fresh detail to get a signed zipUrl
        self._awaiting_download_detail = True
        item_type = item.get("type", "recipe")
        if item_type == "book":
            self._community_api.fetch_book_detail(community_id)
        elif item_type == "article":
            self._community_api.fetch_article_detail(community_id)
        else:
            self._community_api.fetch_recipe_detail(community_id)

    def _on_community_download_progress(self, community_id, received, total):
        """Update download progress dialog."""
        dlg = getattr(self, "_community_download_progress", None)
        if dlg and hasattr(dlg, "_bar") and total > 0:
            dlg._bar.setMaximum(total)
            dlg._bar.setValue(received)

    def _on_community_download_ready(self, community_id, local_zip_path):
        """Import the downloaded zip as a local recipe or book."""
        # Route to deep link handler if this was a deep link download
        if getattr(self, "_deep_link_downloading", False):
            self._deep_link_downloading = False
            self._on_deep_link_download_ready(local_zip_path)
            return

        # Route to review handler if this was a review download
        if self._review_downloading:
            self._review_downloading = False
            self._on_review_download_ready(community_id, local_zip_path)
            return

        # Route to comparison handler if this was a comparison download
        if self._comparison_downloading:
            self._comparison_downloading = False
            self._on_comparison_download_ready(community_id, local_zip_path)
            return

        dlg = getattr(self, "_community_download_progress", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._community_download_progress = None

        # Extract community origin from stashed download item
        origin_kwargs = {}
        item = getattr(self, "_community_download_item", None)
        if item:
            origin_kwargs["community_origin_id"] = str(community_id)
            origin_kwargs["community_origin_uploader"] = item.get("uploaded_by") or None
        self._community_download_item = None

        # Auto-detect book vs recipe
        try:
            book_info = peek_book_zip(local_zip_path)
            self._exit_community_mode()
            self._import_book(local_zip_path, book_info,
                              _delete_zip=True, **origin_kwargs)
            return
        except ValueError:
            pass

        try:
            info = peek_recipe_zip(local_zip_path)
        except (ValueError, Exception) as e:
            self._show_toast(f"Import failed: {e}")
            return

        self._exit_community_mode()
        existing = find_recipe_by_title_producer(info["title"], info["producer"])
        if existing:
            if info["producer"]:
                desc = f'There is already a "{info["title"]}" recipe by {info["producer"]}.'
            else:
                desc = f'A recipe called "{info["title"]}" already exists.'
            msg = QMessageBox(self)
            msg.setWindowTitle("Duplicate Recipe")
            msg.setText(desc)
            replace_btn = msg.addButton("Replace", QMessageBox.DestructiveRole)
            keep_btn = msg.addButton("Keep Both", QMessageBox.AcceptRole)
            msg.addButton("Cancel", QMessageBox.RejectRole)
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == replace_btn:
                delete_recipe(existing["id"])
                old_media = os.path.join(
                    str(DATA_DIR),
                    "media", "recipes", str(existing["id"]),
                )
                if os.path.isdir(old_media):
                    shutil.rmtree(old_media, ignore_errors=True)
            elif clicked != keep_btn:
                return

        self._run_import(local_zip_path, _delete_zip=True, **origin_kwargs)

    def _on_community_download_error(self, community_id, message):
        """Handle download failure."""
        self._deep_link_downloading = False
        self._review_downloading = False
        self._comparison_downloading = False
        dlg = getattr(self, "_community_download_progress", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._community_download_progress = None
        self._show_toast(f"Download failed: {message}")

    # ------------------------------------------------------------------
    # Deep link downloads (foodiemoiety:// URL scheme)
    # ------------------------------------------------------------------

    def _handle_deep_link(self, url_string):
        """Handle a foodiemoiety://download?url=...&title=...&type=... deep link."""
        parsed = urllib.parse.urlparse(url_string)
        params = urllib.parse.parse_qs(parsed.query)
        zip_url = params.get("url", [""])[0]
        title = params.get("title", [""])[0]
        producer = params.get("producer", [""])[0]
        item_type = params.get("type", ["recipe"])[0]  # "book" or "recipe"

        if not zip_url:
            log.warning("Deep link missing 'url' parameter: %s", url_string)
            return

        # If the content already exists locally, just open it
        if title:
            if item_type == "book":
                existing = find_book_by_title_producer(title, producer)
                if existing:
                    log.info("Deep link: book '%s' already exists (id=%s), opening", title, existing["id"])
                    self.show_book_view(book_id=existing["id"])
                    self._show_toast(f"Opened '{title}'")
                    return
            else:
                existing = find_recipe_by_title_producer(title, producer)
                if existing:
                    log.info("Deep link: recipe '%s' already exists (id=%s), opening", title, existing["id"])
                    self.show_recipe_detail(existing["id"])
                    self._show_toast(f"Opened '{title}'")
                    return

        log.info("Deep link download: type=%s title=%s producer=%s", item_type, title, producer)
        self._deep_link_downloading = True
        self._deep_link_title = title
        self._deep_link_type = item_type
        self._community_download_progress = self._create_styled_progress(
            f"Downloading '{title or 'content'}'...", "Download",
            cancel_callback=self._community_api.cancel_download,
        )
        self._community_api.download_zip("deeplink", zip_url)

    def _on_deep_link_download_ready(self, local_zip_path):
        """Import the deep-linked zip and clean up."""
        dlg = getattr(self, "_community_download_progress", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._community_download_progress = None

        self._open_after_import = True
        item_type = getattr(self, "_deep_link_type", "recipe")

        if item_type == "book":
            try:
                book_info = peek_book_zip(local_zip_path)
                self._import_book(local_zip_path, book_info, _delete_zip=True)
                return
            except ValueError:
                pass

        # Recipe (or fallback if book peek failed) — check for duplicates
        try:
            info = peek_recipe_zip(local_zip_path)
        except (ValueError, Exception) as e:
            self._show_toast(f"Import failed: {e}")
            return

        existing = find_recipe_by_title_producer(info["title"], info["producer"])
        if existing:
            if info["producer"]:
                desc = f'There is already a "{info["title"]}" recipe by {info["producer"]}.'
            else:
                desc = f'A recipe called "{info["title"]}" already exists.'
            msg = QMessageBox(self)
            msg.setWindowTitle("Duplicate Recipe")
            msg.setText(desc)
            replace_btn = msg.addButton("Replace", QMessageBox.DestructiveRole)
            keep_btn = msg.addButton("Keep Both", QMessageBox.AcceptRole)
            msg.addButton("Cancel", QMessageBox.RejectRole)
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == replace_btn:
                delete_recipe(existing["id"])
                old_media = os.path.join(
                    str(DATA_DIR),
                    "media", "recipes", str(existing["id"]),
                )
                if os.path.isdir(old_media):
                    shutil.rmtree(old_media, ignore_errors=True)
            elif clicked != keep_btn:
                return

        self._run_import(local_zip_path, _delete_zip=True)

    # ------------------------------------------------------------------
    # Community upload
    # ------------------------------------------------------------------

    def _show_styled_warning(self, title, text):
        """Show a styled warning QMessageBox with dark theme."""
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setStyleSheet(DIALOG_STYLE)
        msg.exec()

    def _create_styled_progress(self, label, window_title="Progress", cancel_callback=None):
        """Create a styled progress dialog with QDialog + QProgressBar.

        Uses a plain QDialog instead of QProgressDialog because
        QProgressDialog.setValue() calls processEvents() internally
        when modal, which spuriously triggers the canceled signal on macOS.

        Returns the dialog. The QProgressBar is accessible via dlg._bar.
        """
        from PySide6.QtWidgets import QProgressBar, QPushButton, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle(window_title)
        dlg.setFixedWidth(400)
        dlg.setModal(True)
        dlg.setStyleSheet(PROGRESS_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel(label))
        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate until first progress event
        lay.addWidget(bar)
        if cancel_callback:
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(cancel_callback)
            lay.addWidget(cancel_btn, alignment=Qt.AlignRight)
        dlg._bar = bar
        dlg.show()
        return dlg

    def _create_upload_progress(self, title):
        """Create an upload progress dialog."""
        dlg = self._create_styled_progress(
            f"Sharing '{title}'...", "Upload",
            cancel_callback=self._on_upload_canceled,
        )
        self._upload_progress = dlg
        self._upload_title = title
        self._upload_canceled = False

    def _on_upload_canceled(self):
        """Handle user clicking Cancel on the upload progress dialog."""
        self._upload_canceled = True
        self._community_api.cancel_upload()
        self._close_upload_progress()

    # ------------------------------------------------------------------
    # Video resolution warning
    # ------------------------------------------------------------------

    def _warn_high_res_video(self, width, height):
        """Warn user about a high-res video. Returns True to proceed, False to cancel."""
        msg = QMessageBox(self)
        msg.setWindowTitle("High Resolution Video")
        msg.setText(
            f"This video is {width}\u00d7{height}, which is larger than 1080p.\n\n"
            f"Large video files might affect your file size limit "
            f"for uploading to the community.\n\n"
            f"Consider compressing the video to 1080p before importing."
        )
        msg.addButton("Import Anyway", QMessageBox.AcceptRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.setStyleSheet(DIALOG_STYLE)
        msg.exec()
        return msg.clickedButton() != cancel_btn

    # ------------------------------------------------------------------
    # App update notification
    # ------------------------------------------------------------------

    def _on_update_available(self, version: str, url: str, notes: str):
        """Show a non-blocking dialog when a newer version is available."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        text = f"Foodie Moiety {version} is available (you have {APP_VERSION})."
        if notes:
            text += f"\n\n{notes}"
        msg.setText(text)
        download_btn = msg.addButton("Update", QMessageBox.AcceptRole)
        msg.addButton("Later", QMessageBox.RejectRole)
        msg.setStyleSheet(DIALOG_STYLE)
        msg.exec()
        if msg.clickedButton() == download_btn and url:
            self._download_update(version, url)

    def _download_update(self, version: str, url: str):
        """Download update DMG via QNetworkAccessManager (same pattern as download_zip)."""
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

        self._update_dmg_path = os.path.join(
            tempfile.gettempdir(), f"FoodieMoiety-{version}.dmg")
        self._update_file = open(self._update_dmg_path, "wb")

        # Progress dialog with real progress bar
        self._update_progress = self._create_styled_progress(
            "Downloading update...", "Updating",
            cancel_callback=self._cancel_update_download)

        # Stream download via QNAM (same approach as community_api.download_zip)
        self._update_nam = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(60_000)
        reply = self._update_nam.get(request)
        self._update_reply = reply
        reply.readyRead.connect(self._on_update_data)
        reply.downloadProgress.connect(self._on_update_progress)
        reply.finished.connect(self._on_update_finished)

    def _on_update_data(self):
        """Stream incoming bytes to disk."""
        if self._update_reply and self._update_file:
            self._update_file.write(bytes(self._update_reply.readAll()))

    def _on_update_progress(self, received, total):
        """Update the progress bar."""
        bar = self._update_progress._bar
        if total > 0:
            bar.setRange(0, 100)
            bar.setValue(int(received * 100 / total))

    def _on_update_finished(self):
        """Handle download completion."""
        from PySide6.QtNetwork import QNetworkReply

        reply = self._update_reply
        self._update_reply = None
        if self._update_file:
            self._update_file.close()
            self._update_file = None
        self._update_progress.close()

        if reply is None:
            return

        if reply.error() != QNetworkReply.NetworkError.NoError:
            log.warning("Update download failed: %s", reply.errorString())
            QMessageBox.warning(self, "Update Failed",
                                f"Download failed: {reply.errorString()}")
            reply.deleteLater()
            return

        reply.deleteLater()
        log.info("Update downloaded to %s", self._update_dmg_path)
        os.system(f"open {self._update_dmg_path!r}")
        QApplication.quit()

    def _cancel_update_download(self):
        if self._update_reply:
            self._update_reply.abort()

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------

    def _show_about_dialog(self):
        """Show the About dialog with app version."""
        msg = QMessageBox(self)
        msg.setWindowTitle("About Foodie Moiety")
        msg.setText(f"Foodie Moiety\nVersion {APP_VERSION}")
        msg.setStyleSheet(DIALOG_STYLE)
        msg.exec()

    # ------------------------------------------------------------------
    # Authentication (Cognito)
    # ------------------------------------------------------------------

    def _try_silent_refresh(self):
        """Attempt to refresh auth tokens in the background on startup."""
        from services.cognito_auth import refresh_tokens

        worker = _IOWorker(refresh_tokens, self._auth_refresh_token)
        worker.finished.connect(self._on_silent_refresh_done)
        worker.error.connect(self._on_silent_refresh_failed)
        self._auth_refresh_worker = worker
        worker.start()

    @property
    def _is_admin(self) -> bool:
        """Check if the server has flagged this account as admin."""
        return bool((self._subscription_status or {}).get("isAdmin", False))

    def _on_silent_refresh_done(self, result):
        """Handle successful silent token refresh."""
        self._auth_id_token = result["id_token"]
        self._auth_access_token = result["access_token"]
        self._auth_expiry = int(time.time()) + result["expires_in"]
        self._settings.setValue("cognito_id_token", self._auth_id_token)
        self._settings.setValue("cognito_access_token", self._auth_access_token)
        self._settings.setValue("cognito_token_expiry", self._auth_expiry)
        self._community_api.set_auth_token(self._auth_id_token)
        # Fetch subscription status to populate UI label
        self._community_api.fetch_subscription_status()

    def _on_silent_refresh_failed(self, _msg):
        """Silent refresh failed — clear stored tokens."""
        self._auth_id_token = None
        self._auth_access_token = None
        self._auth_refresh_token = None
        self._auth_email = ""
        self._auth_display_name = ""
        self._auth_expiry = 0
        self._cached_account_tier = "free"
        self._settings.remove("cognito_id_token")
        self._settings.remove("cognito_access_token")
        self._settings.remove("cognito_refresh_token")
        self._settings.remove("cognito_email")
        self._settings.remove("cognito_token_expiry")
        self._settings.remove("auth_display_name")
        self._settings.remove("account_tier")
        self._community_api.clear_auth_token()

    def _ensure_authenticated(self, heading="Sign In to Community") -> bool:
        """Ensure we have a valid auth token. Returns True if authenticated."""
        # 1. Token still valid?
        if self._auth_id_token and time.time() < self._auth_expiry:
            self._community_api.set_auth_token(self._auth_id_token)
            return True

        # 2. Try refresh
        if self._auth_refresh_token:
            from services.cognito_auth import refresh_tokens
            try:
                result = refresh_tokens(self._auth_refresh_token)
                self._auth_id_token = result["id_token"]
                self._auth_access_token = result["access_token"]
                self._auth_expiry = int(time.time()) + result["expires_in"]
                self._settings.setValue("cognito_id_token", self._auth_id_token)
                self._settings.setValue("cognito_access_token", self._auth_access_token)
                self._settings.setValue("cognito_token_expiry", self._auth_expiry)
                self._community_api.set_auth_token(self._auth_id_token)
                return True
            except Exception:
                pass  # Fall through to dialog

        # 3. Show auth dialog
        from widgets.auth_dialog import AuthDialog
        dlg = AuthDialog(self, prefill_email=self._auth_email, heading=heading)
        if dlg.exec() != QDialog.Accepted:
            return False
        tokens = dlg.get_tokens()
        if not tokens:
            return False

        self._auth_id_token = tokens["id_token"]
        self._auth_access_token = tokens["access_token"]
        self._auth_refresh_token = tokens["refresh_token"]
        self._auth_email = tokens.get("email", "")
        self._auth_expiry = int(time.time()) + tokens["expires_in"]
        self._settings.setValue("cognito_id_token", self._auth_id_token)
        self._settings.setValue("cognito_access_token", self._auth_access_token)
        self._settings.setValue("cognito_refresh_token", self._auth_refresh_token)
        self._settings.setValue("cognito_email", self._auth_email)
        self._settings.setValue("cognito_token_expiry", self._auth_expiry)
        self._community_api.set_auth_token(self._auth_id_token)
        if hasattr(self, "_account_btn"):
            self._account_btn.setToolTip(self._auth_email or "Account")
        # Clear stale purchases from previous account and re-fetch
        self._purchased_book_ids.clear()
        self._community_api.fetch_purchases()
        # Fetch subscription status in background (updates label + caches)
        if self._pending_upload is None:
            self._community_api.fetch_subscription_status()
        return True

    def _on_account_btn_clicked(self):
        """Sign in if not authenticated, otherwise show account menu."""
        if not self._auth_id_token or time.time() >= self._auth_expiry:
            if self._ensure_authenticated():
                # Refresh purchases for community view
                if self._community_mode:
                    self._community_api.fetch_purchases()
            return
        self._on_account_menu()

    def _on_account_menu(self):
        """Show account menu with subscription management options."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a; color: #e0e0e0;
                border: 1px solid #555555; padding: 4px 0;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a3a3a; }
            QMenu::separator { height: 1px; background: #555555; margin: 4px 8px; }
        """)

        status = self._subscription_status
        tier = (status or {}).get("tier", self._cached_account_tier)

        if tier == "free":
            upgrade_action = menu.addAction("Upgrade to Creator")
            upgrade_action.triggered.connect(self._open_subscription_page)
        else:
            manage_action = menu.addAction("Manage Subscription")
            manage_action.triggered.connect(
                self._community_api.create_portal_session
            )

        menu.addSeparator()
        sign_out_action = menu.addAction("Sign Out")
        sign_out_action.triggered.connect(self._sign_out)

        # Position below the account button
        btn = self._account_btn
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        menu.exec(pos)

    def _update_subscription_label(self):
        """Update the subscription status label in the command bar."""
        label = getattr(self, "_sub_status_label", None)
        if label is None:
            return
        try:
            label.isVisible()  # probe — raises if C++ object deleted
        except RuntimeError:
            self._sub_status_label = None
            return
        status = self._subscription_status
        if not status or not self._auth_email:
            label.hide()
            return
        acct_status = status.get("accountStatus", "active")
        if acct_status == "suspended":
            label.setText("Account Suspended")
            label.setStyleSheet(
                "color: #cc4444; font-size: 11px; background: transparent; "
                "padding: 0 4px;"
            )
            label.show()
            return
        tier_raw = status.get("tier", "free")
        tier_display = {
            "free": "Free",
            "creator_listing": "Creator Listing",
            "creator_subscription": "Creator",
        }.get(tier_raw, tier_raw.replace("_", " ").title())
        count = status.get("uploadCount", 0)
        limit = status.get("uploadLimit", 5)
        label.setText(f"{tier_display}: {count}/{limit} uploads")
        label.setStyleSheet(
            "color: #bbbbbb; font-size: 11px; background: transparent; "
            "padding: 0 4px;"
        )
        label.show()

    def _sign_out(self):
        """Clear auth tokens and sign out."""
        self._auth_id_token = None
        self._auth_access_token = None
        self._auth_refresh_token = None
        self._auth_email = ""
        self._auth_display_name = ""
        self._auth_expiry = 0
        self._subscription_status = None
        self._cached_account_tier = "free"
        self._purchased_book_ids.clear()
        self._settings.remove("cognito_id_token")
        self._settings.remove("cognito_access_token")
        self._settings.remove("cognito_refresh_token")
        self._settings.remove("cognito_email")
        self._settings.remove("cognito_token_expiry")
        self._settings.remove("auth_display_name")
        self._settings.remove("account_tier")
        self._community_api.clear_auth_token()
        if hasattr(self, "_account_btn"):
            self._account_btn.setToolTip("Sign In")
        if hasattr(self, "_review_btn"):
            self._review_btn.setVisible(False)
        self._update_subscription_label()
        self._show_toast("Signed out")

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _on_upload_recipe(self, recipe_id):
        """Export a recipe to temp zip and upload to the community."""
        if not self._ensure_authenticated():
            return
        self._show_toast("Preparing upload...")
        # Pre-check upload limits before expensive export
        self._pending_upload = ("recipe", recipe_id)
        self._community_api.fetch_subscription_status()

    def _on_upload_book(self, book_id):
        """Export a book to temp zip and upload to the community."""
        if not self._ensure_authenticated():
            return
        self._show_toast("Preparing upload...")
        # Pre-check upload limits before expensive export
        self._pending_upload = ("book", book_id)
        self._community_api.fetch_subscription_status()

    def _on_subscription_status(self, status):
        """Handle subscription status response."""
        self._subscription_status = status
        # Cache tier so the menu shows the right option immediately on next launch
        tier = status.get("tier", "free")
        self._cached_account_tier = tier
        self._settings.setValue("account_tier", tier)
        # Cache display name from backend profile
        dn = status.get("displayName", "")
        if dn:
            self._auth_display_name = dn
            self._settings.setValue("auth_display_name", dn)
        # Update account button tooltip with login email
        if hasattr(self, "_account_btn"):
            self._account_btn.setToolTip(self._auth_email or "Account")
        # Update status label if visible
        if hasattr(self, "_sub_status_label"):
            self._update_subscription_label()
        # Update book upload visibility on recipe cards
        self.recipe_list.set_book_upload_allowed(
            status.get("maxBookSize", 0) > 0
        )
        # Show/hide admin-only review button
        if hasattr(self, "_review_btn"):
            self._review_btn.setVisible(self._is_admin)

        # If there's a pending upload, check limits and proceed
        pending = self._pending_upload
        if pending is None:
            return
        self._pending_upload = None
        item_type, item_id = pending

        # Check for suspended account
        acct_status = status.get("accountStatus", "active")
        if acct_status == "suspended":
            self._show_suspended_dialog()
            return

        count = status.get("uploadCount", 0)
        limit = status.get("uploadLimit", 5)
        tier = status.get("tier", "free")

        if count >= limit:
            self._show_upload_limit_dialog(count, limit, tier)
            return

        # Book uploads require a Creator plan
        if item_type == "book" and status.get("maxBookSize", 0) <= 0:
            self._show_creator_required_dialog("book")
            return

        # Limits OK — proceed with export + upload
        self._start_upload(item_type, item_id)

    def _on_subscription_status_error(self, message):
        """Handle subscription status fetch failure."""
        pending = self._pending_upload
        if pending is None:
            return
        self._pending_upload = None
        # Fail fast — if we can't reach the server, uploading will also fail
        self._show_toast(
            "Could not verify account status — please check your internet connection"
        )

    def _start_upload(self, item_type, item_id):
        """Check for duplicate title, then export and upload."""
        if item_type == "recipe":
            rd = load_recipe_data(item_id)
            if not rd:
                return
            title = rd.title
            is_moiety = rd.is_moiety
        else:
            bd = load_book_data(item_id)
            if not bd:
                return
            title = bd.title
            is_moiety = False

        # Store pending upload info for after duplicate check
        self._pending_upload_info = (item_type, item_id, title, is_moiety)

        def on_dup_done(is_dup, msg):
            self._community_api.duplicate_check_done.disconnect(on_dup_done)
            self._community_api.duplicate_check_error.disconnect(on_dup_err)
            if is_dup:
                self._show_toast(msg or "You already have content with this title")
                self._pending_upload_info = None
                return
            self._do_upload(*self._pending_upload_info)

        def on_dup_err(msg):
            self._community_api.duplicate_check_done.disconnect(on_dup_done)
            self._community_api.duplicate_check_error.disconnect(on_dup_err)
            self._pending_upload_info = None
            self._show_toast(
                "Could not prepare upload — please check your internet connection"
            )

        self._community_api.duplicate_check_done.connect(on_dup_done)
        self._community_api.duplicate_check_error.connect(on_dup_err)
        self._community_api.check_duplicate_title(title)

    def _do_upload(self, item_type, item_id, title, is_moiety=False):
        """Export and upload after duplicate check passes."""
        self._pending_upload_info = None
        self._upload_source = (item_type, item_id)  # stash for origin update on success

        # Ask moiety uploaders if they want to submit as a Book of Moiety candidate
        self._upload_bom_candidate = False
        if is_moiety and item_type == "recipe":
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Book of Moiety")
            dlg.setIconPixmap(platform_icon(
                "questionmark.circle", weight="regular", point_size=36, color="white",
            ).pixmap(48, 48))
            dlg.setText("Submit as a Book of Moiety candidate?")
            dlg.setInformativeText(
                "Your moiety could be included in a future volume of "
                "The Book of Moiety \u2014 a curated collection of the best "
                "community moieties."
            )
            yes_btn = dlg.addButton("Yes, Submit", QMessageBox.ButtonRole.AcceptRole)
            dlg.addButton("No Thanks", QMessageBox.ButtonRole.RejectRole)
            dlg.setStyleSheet(DIALOG_STYLE)
            dlg.exec()
            self._upload_bom_candidate = (dlg.clickedButton() == yes_btn)

        if item_type == "recipe":
            tmp = tempfile.NamedTemporaryFile(suffix=".fmr", delete=False)
            tmp_path = tmp.name
            tmp.close()
            self._create_upload_progress(title)
            worker = _IOWorker(export_recipe_to_zip, item_id, tmp_path)
            worker.finished.connect(lambda _: QTimer.singleShot(
                0, lambda: self._upload_after_validation(tmp_path, "recipe")
            ))
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".fmb", delete=False)
            tmp_path = tmp.name
            tmp.close()
            self._create_upload_progress(title)
            worker = _IOWorker(export_book_to_zip, item_id, tmp_path)
            worker.finished.connect(lambda _: QTimer.singleShot(
                0, lambda: self._upload_after_validation(tmp_path, "book")
            ))
        worker.error.connect(
            lambda msg, p=tmp_path: self._on_upload_export_error(msg, p)
        )
        self._io_worker = worker
        worker.start()

    def _upload_after_validation(self, tmp_path, item_type):
        """Validate exported zip against tier limits, then upload."""
        ok, reason = self._validate_upload(tmp_path, item_type)
        if not ok:
            self._close_upload_progress()
            self._handle_validation_failure(reason, tmp_path)
            return
        self._community_api.upload_zip(
            tmp_path, item_type,
            bom_candidate=self._upload_bom_candidate,
        )

    def _validate_upload(self, zip_path, item_type):
        """Check file size and video presence against tier limits.

        Returns (ok, reason) where reason is "" on success or a code like
        "size:<actual>:<max>" or "video".
        """
        status = self._subscription_status or {}
        file_size = os.path.getsize(zip_path)
        if item_type == "book":
            max_size = status.get("maxBookSize", 0)
        else:
            max_size = status.get("maxRecipeSize", 0)
        if max_size and file_size > max_size:
            return False, f"size:{file_size}:{max_size}"
        if not status.get("videoAllowed", True):
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                if any(n.lower().endswith(".mp4") for n in zf.namelist()):
                    return False, "video"
        return True, ""

    def _handle_validation_failure(self, reason, tmp_path):
        """Show appropriate dialog for a pre-upload validation failure."""
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if reason == "video":
            self._show_creator_required_dialog("video")
        elif reason.startswith("size:"):
            _, actual, maximum = reason.split(":")
            self._on_upload_size_error("recipe", float(actual), float(maximum))
        else:
            self._show_toast("Upload validation failed")

    def _show_suspended_dialog(self):
        """Show dialog when a suspended user tries to upload."""
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Account Suspended")
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setText("Your account has been suspended.")
        dlg.setInformativeText(
            "You are unable to upload while your account is suspended. "
            "If you believe this is an error, please contact support."
        )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.setStyleSheet(DIALOG_STYLE)
        dlg.exec()

    def _show_upload_limit_dialog(self, count, limit, tier):
        """Show dialog when upload limit is reached."""
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Upload Limit Reached")
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setText(
            f"You've used {count}/{limit} uploads this month."
        )
        if tier == "free":
            dlg.setInformativeText(
                "Upgrade to a Creator plan for 8 uploads per month, video "
                "support, and the ability to sell your recipe books."
            )
            upgrade_btn = dlg.addButton("Upgrade", QMessageBox.ButtonRole.ActionRole)
            dlg.addButton(QMessageBox.StandardButton.Cancel)
        else:
            dlg.setInformativeText(
                "Your upload limit resets at the start of your next billing cycle."
            )
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            upgrade_btn = None
        dlg.setStyleSheet(DIALOG_STYLE)
        dlg.exec()
        if upgrade_btn and dlg.clickedButton() == upgrade_btn:
            self._open_subscription_page()

    def _show_creator_required_dialog(self, content_type):
        """Show dialog when Free tier user attempts a creator-only action."""
        from PySide6.QtWidgets import QMessageBox
        if content_type == "book":
            title = "Uploading books requires a Creator plan."
            detail = (
                "Upgrade to a Creator plan to upload books, include videos, "
                "and sell your content on the community."
            )
        else:
            title = "Including video in community uploads requires a Creator plan."
            detail = (
                "Upgrade to a Creator plan to include video in your uploads. "
                "This limit only applies to community uploads — you can always "
                "export recipes with video to a file without restrictions."
            )
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Creator Plan Required")
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.setText(title)
        dlg.setInformativeText(detail)
        upgrade_btn = dlg.addButton(
            "Upgrade", QMessageBox.ButtonRole.ActionRole
        )
        dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.setStyleSheet(DIALOG_STYLE)
        dlg.exec()
        if dlg.clickedButton() == upgrade_btn:
            self._open_subscription_page()

    def _on_upload_limit_error(self, count, limit, tier):
        """Handle 403 from upload endpoint — server-side limit enforcement."""
        self._close_upload_progress()
        self._show_upload_limit_dialog(count, limit, tier)

    def _open_subscription_page(self):
        """Open the website subscription page via desktop-handoff with all three Cognito tokens."""
        from PySide6.QtGui import QDesktopServices
        id_tok = self._auth_id_token
        access_tok = self._auth_access_token
        refresh_tok = self._auth_refresh_token
        if id_tok and access_tok and refresh_tok:
            from urllib.parse import quote
            fragment = (
                f"idToken={quote(id_tok, safe='')}"
                f"&accessToken={quote(access_tok, safe='')}"
                f"&refreshToken={quote(refresh_tok, safe='')}"
                f"&redirect=/subscription"
            )
            url = f"{WEBSITE_URL}/auth/desktop-handoff#{fragment}"
        else:
            url = f"{WEBSITE_URL}/subscription?source=desktop"
        QDesktopServices.openUrl(QUrl(url))

    def _on_checkout_url_ready(self, url):
        """Open a Stripe checkout/portal URL in the system browser."""
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))
        # Start polling if this was a book purchase checkout
        if self._purchase_poll_cid:
            self._show_toast("Complete your purchase in the browser")
            self._purchase_poll_timer.start()

    def _on_checkout_url_error(self, message):
        """Handle checkout URL generation failure — show dialog so user can read it."""
        self._purchase_poll_cid = None
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Checkout Error")
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setText(f"Could not open checkout: {message}")
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.setStyleSheet(DIALOG_STYLE)
        dlg.exec()

    def _on_upload_export_error(self, message, zip_path=None):
        """Handle export-to-zip failure during upload."""
        self._close_upload_progress()
        if zip_path:
            try:
                os.remove(zip_path)
            except OSError:
                pass
        self._show_toast(f"Upload failed: {message}")

    def _on_upload_progress(self, sent, total):
        """Update upload progress dialog."""
        if self._upload_canceled:
            return
        dlg = getattr(self, "_upload_progress", None)
        if dlg and hasattr(dlg, "_bar") and total > 0:
            dlg._bar.setMaximum(total)
            dlg._bar.setValue(sent)

    def _close_upload_progress(self):
        """Safely close and discard the upload progress dialog."""
        dlg = getattr(self, "_upload_progress", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._upload_progress = None

    def _on_upload_ready(self, _item_id):
        """Handle successful upload."""
        # Store backend-assigned community_id as origin in local DB so
        # re-uploads (even from a different account) carry the origin forward.
        source = getattr(self, "_upload_source", None)
        if source and _item_id:
            item_type, item_id = source
            # Extract current user's Cognito sub from id_token
            uploader_sub = None
            try:
                import base64
                import json as _json
                payload = self._auth_id_token.split(".")[1]
                payload += "=" * (4 - len(payload) % 4)
                claims = _json.loads(base64.urlsafe_b64decode(payload))
                uploader_sub = claims.get("sub")
            except Exception:
                pass
            if item_type == "recipe":
                rd = load_recipe_data(item_id)
                if rd and not rd.community_origin_id:
                    rd.community_origin_id = _item_id
                    rd.community_origin_uploader = uploader_sub
                    save_recipe_data(rd)
            else:
                bd = load_book_data(item_id)
                if bd and not bd.community_origin_id:
                    bd.community_origin_id = _item_id
                    bd.community_origin_uploader = uploader_sub
                    save_book_data(bd)
        self._upload_source = None
        self._close_upload_progress()
        # Refresh subscription status to update upload count label
        self._community_api.fetch_subscription_status()
        from PySide6.QtWidgets import QMessageBox
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Upload Received")
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.setText("Upload received successfully!")
        dlg.setInformativeText(
            "All uploads are reviewed to ensure they meet community standards. "
            "This typically takes one to two days. You'll receive an email once "
            "your submission has been reviewed."
        )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.setStyleSheet(DIALOG_STYLE)
        dlg.exec()

    def _on_upload_error(self, message):
        """Handle upload failure."""
        self._close_upload_progress()
        if not self._upload_canceled:
            self._show_toast(f"Upload failed: {message}")

    def _on_upload_size_error(self, item_type, file_size, max_size):
        """Handle 413 or client-side size limit — file exceeds upload limit."""
        self._close_upload_progress()

        def _fmt(size_bytes):
            if size_bytes >= 1_073_741_824:
                return f"{size_bytes / 1_073_741_824:.1f} GB"
            return f"{size_bytes / 1_048_576:.0f} MB"

        tier = (self._subscription_status or {}).get("tier", "free")
        type_label = "Books" if item_type == "book" else "Recipes"
        from PySide6.QtWidgets import QMessageBox
        dlg = QMessageBox(self)
        dlg.setWindowTitle("File Too Large")
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setText(
            f"{type_label} cannot exceed {_fmt(max_size)} for community uploads."
            if max_size else "This file exceeds the upload size limit."
        )
        info = ""
        if file_size:
            info = f"Your file is {_fmt(file_size)}. "
        if tier == "free":
            info += (
                "Upgrade to a Creator plan to unlock larger uploads. "
                "This limit only applies to community uploads — you can "
                "always export recipes to a file without restrictions."
            )
            upgrade_btn = dlg.addButton("Upgrade", QMessageBox.ButtonRole.ActionRole)
            dlg.addButton(QMessageBox.StandardButton.Cancel)
        else:
            info += (
                "This limit only applies to community uploads — you can "
                "always export recipes to a file without restrictions."
            )
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            upgrade_btn = None
        dlg.setInformativeText(info)
        dlg.setStyleSheet(DIALOG_STYLE)
        dlg.exec()
        if upgrade_btn and dlg.clickedButton() == upgrade_btn:
            self._open_subscription_page()

    # ------------------------------------------------------------------
    # Admin review mode
    # ------------------------------------------------------------------

    def _enter_review_mode(self):
        """Switch to admin review mode to moderate pending uploads."""
        if not self._ensure_authenticated(heading="Sign In for Review Panel (Administrator Only)"):
            return
        if not self._is_admin:
            self._show_toast("Not authorized for review")
            return
        self._review_mode = True
        self._review_came_from_community = self._community_mode
        self.stacked_widget.setCurrentWidget(self.recipe_list)
        self.recipe_list.hide_tag_side_panel()
        self.recipe_list.enter_review_mode()
        self._configure_review_commands()
        self.recipe_list.set_command_bar(self.command_bar)
        self._fetch_review_page(fresh=True)

    def _exit_review_mode(self):
        """Return from admin review mode."""
        self._review_mode = False
        self._cleanup_all_review_imports()
        self.recipe_list.exit_review_mode()
        self.recipe_list.clear_all_filters()
        if self._review_came_from_community:
            self._enter_community_mode()
        else:
            self.show_recipe_list()

    def _configure_review_commands(self):
        """Configure command bar for review mode."""
        self.play_video_toggle = None
        self.command_bar.clear()
        self.command_bar.add_button(
            "Back", self._exit_review_mode,
            icon=platform_icon("chevron.left", weight="regular", point_size=48, color="white")
        )
        self.recipe_list.show_mode_banner("PENDING REVIEW", "#6a4a1a")
        self._review_count_label = self.command_bar.add_button("Pending...", lambda: None)
        self._review_count_label.setEnabled(False)
        self._review_count_label.setStyleSheet(
            "QPushButton { color: #888888; border: none; background: transparent; font-size: 14px; }"
        )
        self.command_bar.add_spacer()
        self.command_bar.add_button(
            "Suspended", self._on_show_suspended,
            icon=platform_icon("person.badge.minus", weight="regular", point_size=48, color="white")
        )
        self.command_bar.add_stretch()

    def _fetch_review_page(self, fresh=False):
        """Fetch a page of pending items."""
        if fresh:
            self.recipe_list._review_cursor = None
            self.recipe_list._clear_cards()
            self.recipe_list._review_items.clear()
        self.recipe_list._review_loading = True
        self.recipe_list.show_loading()
        self._community_api.fetch_pending(
            cursor=self.recipe_list._review_cursor,
        )

    def _on_review_page_loaded(self, items, next_cursor):
        """Handle pending items response."""
        if not self._review_mode:
            return
        append = self.recipe_list._review_cursor is not None
        self.recipe_list._review_cursor = next_cursor
        self.recipe_list._review_loading = False
        self.recipe_list.hide_loading()
        self.recipe_list.load_review_cards(items, append=append)
        # Update count label
        count = len(self.recipe_list._review_items)
        if hasattr(self, "_review_count_label"):
            self._review_count_label.setText(
                f"{count} pending" if count else "No pending uploads"
            )
        # Trigger lazy thumbnail loading
        self._thumb_debounce.start()

    def _on_review_page_error(self, message):
        """Handle pending items API error."""
        self.recipe_list._review_loading = False
        self.recipe_list.hide_loading()
        self._show_toast(f"Review: {message}")

    def _get_review_item(self, community_id):
        """Look up a review item from cards or the active preview."""
        item = self.recipe_list.get_review_item(community_id)
        if not item and self._review_preview_item:
            if self._review_preview_item.get("community_id") == community_id:
                item = self._review_preview_item
        return item

    def _on_review_approve(self, community_id):
        """Approve a pending upload — show optional feedback dialog."""
        item = self._get_review_item(community_id)
        if not item:
            return
        is_bom = bool(item.get("bookOfMoietyCandidate"))
        from widgets.review_action_dialog import ReviewActionDialog
        dlg = ReviewActionDialog(
            self, title="Approve",
            label=f"Feedback for '{item.get('title', 'Untitled')}':",
            show_bom_candidate=is_bom,
        )
        if not dlg.exec():
            return
        if dlg.save_bom_candidate():
            self._review_bom_pending.add(community_id)
        item_type = item.get("type", "recipe")
        self._community_api.review_item(
            item_type, community_id, "approve", dlg.reason()
        )

    def _on_review_reject(self, community_id):
        """Reject and delete — optional reason + refund checkbox."""
        item = self._get_review_item(community_id)
        if not item:
            return
        from widgets.review_action_dialog import ReviewActionDialog
        dlg = ReviewActionDialog(
            self, title="Reject && Delete",
            label=f"Reason for rejecting '{item.get('title', 'Untitled')}':",
            show_refund=True,
        )
        if not dlg.exec():
            return
        if dlg.refund_upload():
            self._review_refund_pending.add(community_id)
        item_type = item.get("type", "recipe")
        self._community_api.review_item(
            item_type, community_id, "reject_delete", dlg.reason(),
            refund_upload=dlg.refund_upload(),
        )

    def _on_review_quarantine(self, community_id):
        """Quarantine content — required reason dialog."""
        item = self._get_review_item(community_id)
        if not item:
            return
        from widgets.review_action_dialog import ReviewActionDialog
        dlg = ReviewActionDialog(
            self, title="Quarantine",
            label=f"Reason for quarantining '{item.get('title', 'Untitled')}':",
            reason_required=True,
        )
        if not dlg.exec():
            return
        item_type = item.get("type", "recipe")
        self._community_api.review_item(
            item_type, community_id, "quarantine", dlg.reason()
        )

    def _on_review_done(self, item_id, action):
        """Handle successful review action."""
        in_preview = self._review_current_cid is not None

        # Get title before cleanup
        item = self._get_review_item(item_id)
        title = item.get("title", "Item") if item else "Item"

        if in_preview:
            self._review_current_cid = None
            self._review_preview_item = None

        # If this approval included "save as BOM candidate", keep the temp import
        bom_kept = False
        bom_missed = False
        if action == "approve" and item_id in self._review_bom_pending:
            self._review_bom_pending.discard(item_id)
            cached = self._review_temp_imports.pop(item_id, None)
            if cached and cached.get("recipe_id"):
                from utils.database import keep_as_bom_candidate
                keep_as_bom_candidate(cached["recipe_id"])
                bom_kept = True
            else:
                bom_missed = True

        if not bom_kept:
            # Clean up the temp import for this specific item
            self._cleanup_review_import(item_id)
        # Clean up any comparison import associated with this review
        if self._comparison_cid:
            self._cleanup_review_import(self._comparison_cid)
            self._comparison_cid = None
            self._comparison_item = None
            self._showing_comparison = False
        self.recipe_list.remove_review_card(item_id)
        action_labels = {
            "approve": "Approved",
            "reject_delete": "Rejected",
            "quarantine": "Quarantined",
        }
        label = action_labels.get(action, action.title())

        if in_preview:
            self._return_to_review_list()

        refunded = item_id in self._review_refund_pending
        self._review_refund_pending.discard(item_id)
        toast_msg = f"{label}: '{title}'"
        if bom_kept:
            toast_msg = f"Approved: '{title}' (saved as BOM candidate)"
        elif bom_missed:
            toast_msg = f"Approved: '{title}' (BOM not saved \u2014 preview first to save locally)"
        elif refunded:
            toast_msg += " (upload refunded)"
        self._show_toast(toast_msg)
        # Invalidate community cache so approved items appear immediately
        self._community_api.invalidate_cache()
        # Refresh subscription status (upload count may change after refund)
        self._community_api.fetch_subscription_status()
        # Update count
        count = len(self.recipe_list._review_items)
        if hasattr(self, "_review_count_label"):
            self._review_count_label.setText(
                f"{count} pending" if count else "No pending uploads"
            )

    def _on_review_error(self, _item_id, message):
        """Handle review action failure."""
        self._show_toast(f"Review failed: {message}")

    # ------------------------------------------------------------------
    # Review — temp import preview
    # ------------------------------------------------------------------

    def _cleanup_review_import(self, community_id):
        """Delete a single cached temp import by community_id."""
        cached = self._review_temp_imports.pop(community_id, None)
        if not cached:
            return
        bid = cached.get("book_id")
        if bid is not None:
            try:
                delete_book(bid)
                book_media = os.path.join(
                    str(DATA_DIR), "media", "books", str(bid),
                )
                if os.path.isdir(book_media):
                    shutil.rmtree(book_media, ignore_errors=True)
            except Exception:
                pass
        rid = cached.get("recipe_id")
        if rid is not None:
            try:
                delete_recipe(rid)
                recipe_media = os.path.join(
                    str(DATA_DIR), "media", "recipes", str(rid),
                )
                if os.path.isdir(recipe_media):
                    shutil.rmtree(recipe_media, ignore_errors=True)
            except Exception:
                pass

    def _cleanup_all_review_imports(self):
        """Delete all cached temp imports (on exit review mode)."""
        for cid in list(self._review_temp_imports):
            self._cleanup_review_import(cid)
        self._review_current_cid = None
        self._review_preview_item = None
        self._comparison_cid = None
        self._comparison_item = None
        self._showing_comparison = False

    def _on_review_preview(self, community_id):
        """Download and temp-import a pending item for full detail preview."""
        item = self.recipe_list.get_review_item(community_id)
        if not item:
            return

        # Check if already downloaded and imported
        cached = self._review_temp_imports.get(community_id)
        if cached:
            self._review_current_cid = community_id
            self._review_preview_item = item
            if cached.get("book_id") is not None:
                self.show_book_view(book_id=cached["book_id"])
            else:
                self.show_recipe_detail(cached["recipe_id"])
            self._configure_review_preview_commands()
            self.command_bar.updateGeometry()
            QApplication.processEvents()
            self._position_overlays()
            return

        self._review_preview_item = item
        self._review_downloading = True
        title = item.get("title", "item")
        self._community_download_progress = self._create_styled_progress(
            f"Downloading '{title}'...", "Download",
            cancel_callback=self._community_api.cancel_download,
        )
        # Fetch fresh detail to get a signed zipUrl
        self._awaiting_download_detail = True
        item_type = item.get("type", "recipe")
        if item_type == "book":
            self._community_api.fetch_book_detail(community_id)
        elif item_type == "article":
            self._community_api.fetch_article_detail(community_id)
        else:
            self._community_api.fetch_recipe_detail(community_id)

    def _on_review_download_ready(self, _community_id, local_zip_path):
        """Handle download completion for review preview — import then open."""
        dlg = getattr(self, "_community_download_progress", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._community_download_progress = None

        cid = (self._review_preview_item or {}).get("community_id", "")

        # Auto-detect book vs recipe
        is_book = False
        try:
            peek_book_zip(local_zip_path)
            is_book = True
        except ValueError:
            pass

        if not is_book:
            try:
                peek_recipe_zip(local_zip_path)
            except (ValueError, Exception) as e:
                self._show_toast(f"Import failed: {e}")
                self._review_downloading = False
                return

        zip_to_clean = local_zip_path
        progress = self._create_styled_progress(
            "Loading preview...", "Review"
        )

        def _remove_zip():
            try:
                os.remove(zip_to_clean)
            except OSError:
                pass

        if is_book:
            worker = _IOWorker(import_book_from_zip, local_zip_path)

            def _on_book_imported(new_book_id):
                progress.close()
                progress.deleteLater()
                _remove_zip()
                hide_temp_book(new_book_id)
                self._review_temp_imports[cid] = {"book_id": new_book_id, "recipe_id": None}
                self._review_current_cid = cid
                self.show_book_view(book_id=new_book_id)
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()
        else:
            worker = _IOWorker(import_recipe_from_zip, local_zip_path)

            def _on_book_imported(new_recipe_id):
                progress.close()
                progress.deleteLater()
                _remove_zip()
                hide_temp_recipe(new_recipe_id)
                self._review_temp_imports[cid] = {"book_id": None, "recipe_id": new_recipe_id}
                self._review_current_cid = cid
                self.show_recipe_detail(new_recipe_id)
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()

        worker.finished.connect(_on_book_imported)
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_toast(f"Preview failed: {msg}")
        )
        self._io_worker = worker
        worker.start()

    def _configure_review_preview_commands(self):
        """Replace command bar contents with review actions for the preview."""
        community_id = (self._review_preview_item or {}).get("community_id", "")
        self.play_video_toggle = None
        self.command_bar.clear()
        self.command_bar.add_button(
            "Back", self._on_review_detail_back,
            icon=platform_icon("chevron.left", weight="regular", point_size=48, color="white")
        )
        self.command_bar.add_stretch()
        approve_btn = self.command_bar.add_button(
            "Approve", lambda: self._on_review_preview_action(community_id, "approve"),
            icon=platform_icon("checkmark.circle", weight="regular", point_size=48, color="white")
        )
        approve_btn.setStyleSheet("""
            QPushButton { background-color: #2a5a2a; color: white; border: 1px solid #3a7a3a;
                border-radius: 4px; padding: 8px 18px; font-size: 14px; }
            QPushButton:hover { background-color: #3a7a3a; }
        """)
        reject_btn = self.command_bar.add_button(
            "Reject", lambda: self._on_review_preview_action(community_id, "reject"),
            icon=platform_icon("xmark.circle", weight="regular", point_size=48, color="white")
        )
        reject_btn.setStyleSheet("""
            QPushButton { background-color: #6a4a1a; color: white; border: 1px solid #8a6a2a;
                border-radius: 4px; padding: 8px 18px; font-size: 14px; }
            QPushButton:hover { background-color: #8a6a2a; }
        """)
        quarantine_btn = self.command_bar.add_button(
            "Quarantine", lambda: self._on_review_preview_action(community_id, "quarantine"),
            icon=platform_icon("exclamationmark.shield", weight="regular", point_size=48, color="white")
        )
        quarantine_btn.setStyleSheet("""
            QPushButton { background-color: #6a2a2a; color: white; border: 1px solid #8a3a3a;
                border-radius: 4px; padding: 8px 18px; font-size: 14px; }
            QPushButton:hover { background-color: #8a3a3a; }
        """)

        # Account management menu (only if uploadedBy is available)
        uploaded_by = (self._review_preview_item or {}).get("uploaded_by", "")
        if uploaded_by:
            self.command_bar.add_spacer()
            self._review_account_uid = uploaded_by
            account_btn = self.command_bar.add_button(
                "Account",
                self._on_review_account_menu,
                icon=platform_icon("person.crop.circle", weight="regular", point_size=48, color="white")
            )
            account_btn.setStyleSheet("""
                QPushButton { background-color: #2a2a2a; color: #cccccc; border: 1px solid #555555;
                    border-radius: 4px; padding: 8px 18px; font-size: 14px; }
                QPushButton:hover { background-color: #4a4a4a; color: white; }
            """)
            self._review_account_btn = account_btn

        # Origin indicator — show if this upload was derived from existing content
        origin_producer = (self._review_preview_item or {}).get("community_origin_producer", "")
        origin_uploader = (self._review_preview_item or {}).get("community_origin_uploader", "")
        if origin_producer or origin_uploader:
            origin_label = origin_producer or "Unknown"
            origin_btn = self.command_bar.add_button(
                f"Origin: {origin_label}",
                lambda: self._on_review_show_producer_items(origin_uploader),
            )
            origin_btn.setStyleSheet("""
                QPushButton { background-color: #5a3a1a; color: #ffcc66; border: 1px solid #8a6a2a;
                    border-radius: 4px; padding: 8px 18px; font-size: 14px; }
                QPushButton:hover { background-color: #7a5a2a; }
            """)

        # Comparison toggle — when a comparison has been imported
        if self._comparison_cid:
            toggle_label = "Show Original" if not self._showing_comparison else "Show Upload"
            toggle_btn = self.command_bar.add_button(
                toggle_label, self._on_review_toggle_comparison,
            )
            toggle_btn.setStyleSheet("""
                QPushButton { background-color: #2a4a6a; color: white; border: 1px solid #3a6a8a;
                    border-radius: 4px; padding: 8px 18px; font-size: 14px; }
                QPushButton:hover { background-color: #3a6a8a; }
            """)

        # Reporting procedure reference
        report_btn = self.command_bar.add_button(
            "Report", self._show_reporting_procedure,
            icon=platform_icon("exclamationmark.triangle", weight="regular", point_size=48, color="white")
        )
        report_btn.setStyleSheet("""
            QPushButton { background-color: #2a2a2a; color: #cccccc; border: 1px solid #555555;
                border-radius: 4px; padding: 8px 18px; font-size: 14px; }
            QPushButton:hover { background-color: #4a4a4a; color: white; }
        """)

    def _show_reporting_procedure(self):
        """Show the illegal content reporting procedure in a read-only dialog."""
        procedure_path = str(CSAM_REPORT_DOC)
        try:
            with open(procedure_path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            self._show_toast("Reporting procedure file not found")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Illegal Content Reporting Procedure")
        dlg.setMinimumSize(700, 600)
        dlg.setStyleSheet(DIALOG_STYLE)
        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setMarkdown(content)
        text.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #e0e0e0; border: none; "
            "font-size: 14px; padding: 12px; }"
        )
        layout.addWidget(text)

        # Report data display area (hidden until data is fetched)
        report_data_text = QTextEdit()
        report_data_text.setReadOnly(True)
        report_data_text.setVisible(False)
        report_data_text.setMaximumHeight(200)
        report_data_text.setStyleSheet(
            "QTextEdit { background-color: #1a2a1a; color: #c0e0c0; border: 1px solid #3a5a3a; "
            "border-radius: 4px; font-size: 13px; font-family: monospace; padding: 8px; }"
        )
        layout.addWidget(report_data_text)

        btn_row = QHBoxLayout()
        # Gather Report Data button — only if we have a review item
        community_id = (self._review_preview_item or {}).get("community_id", "")
        if community_id:
            gather_btn = QPushButton("Gather Report Data")
            gather_btn.setStyleSheet(
                "QPushButton { background-color: #6a2a2a; color: white; border: 1px solid #8a3a3a; "
                "border-radius: 4px; padding: 8px 24px; font-size: 14px; }"
                "QPushButton:hover { background-color: #8a3a3a; }"
            )
            gather_btn.clicked.connect(
                lambda: self._gather_report_data(community_id, gather_btn, report_data_text)
            )
            btn_row.addWidget(gather_btn)

        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: white; border: 1px solid #555555; "
            "border-radius: 4px; padding: 8px 24px; font-size: 14px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    def _gather_report_data(self, community_id, btn, output_text):
        """Call backend API to gather upload metadata for legal reporting."""
        btn.setEnabled(False)
        btn.setText("Fetching...")
        item = self._review_preview_item or {}

        def on_data(data):
            self._community_api.report_data_loaded.disconnect(on_data)
            self._community_api.report_data_error.disconnect(on_error)
            btn.setEnabled(True)
            btn.setText("Gather Report Data")
            from datetime import datetime, timezone
            lines = [
                f"Upload ID:          {data.get('communityId', community_id)}",
                f"S3 Key:             {data.get('s3Key', 'N/A')}",
                f"Type:               {data.get('type', item.get('type', 'N/A'))}",
                f"Title:              {data.get('title', item.get('title', 'N/A'))}",
                f"Producer:           {data.get('producer', item.get('producer', 'N/A'))}",
                f"Uploader User ID:   {data.get('uploadedBy', item.get('uploaded_by', 'N/A'))}",
                f"Uploader Email:     {data.get('uploaderEmail', 'N/A')}",
                f"Uploader IP:        {data.get('uploaderIp', 'N/A')}",
                f"Upload Date:        {data.get('uploadedAt', item.get('uploaded_at', 'N/A'))}",
                f"Discovery Date:     {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            ]
            output_text.setPlainText("\n".join(lines))
            output_text.setVisible(True)

        def on_error(msg):
            self._community_api.report_data_loaded.disconnect(on_data)
            self._community_api.report_data_error.disconnect(on_error)
            btn.setEnabled(True)
            btn.setText("Gather Report Data")
            output_text.setPlainText(f"Error fetching report data: {msg}")
            output_text.setVisible(True)

        self._community_api.report_data_loaded.connect(on_data)
        self._community_api.report_data_error.connect(on_error)
        self._community_api.fetch_report_data(community_id)

    def _on_review_account_menu(self):
        """Open account management menu from review preview."""
        btn = getattr(self, "_review_account_btn", None)
        uid = getattr(self, "_review_account_uid", "")
        if btn and uid:
            self._show_account_manage_menu(btn, uid)

    def _show_account_manage_menu(self, btn, user_id):
        """Show dropdown with suspend / cancel subscription actions."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a; color: #e0e0e0;
                border: 1px solid #555555; padding: 4px 0;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a3a3a; }
        """)
        suspend_action = menu.addAction("Suspend Account")
        suspend_action.triggered.connect(
            lambda: self._on_account_manage_action(user_id, "suspend")
        )
        cancel_sub_action = menu.addAction("Cancel Subscription")
        cancel_sub_action.triggered.connect(
            lambda: self._on_account_manage_action(user_id, "cancel_subscription")
        )
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        menu.exec(pos)

    def _on_account_manage_action(self, user_id, action):
        """Prompt for reason (if suspend) or confirm, then call API."""
        if action == "suspend":
            from widgets.review_action_dialog import ReviewActionDialog
            dlg = ReviewActionDialog(
                self, title="Suspend Account",
                label="Reason for suspending this account:",
                reason_required=True,
            )
            if not dlg.exec():
                return
            self._community_api.manage_account(user_id, action, dlg.reason())
        elif action == "cancel_subscription":
            msg = QMessageBox(self)
            msg.setWindowTitle("Cancel Subscription")
            msg.setText("Cancel this user's creator subscription?")
            msg.setInformativeText("This will immediately downgrade them to free tier.")
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            msg.setDefaultButton(QMessageBox.StandardButton.No)
            msg.setStyleSheet(DIALOG_STYLE)
            if msg.exec() != QMessageBox.StandardButton.Yes:
                return
            self._community_api.manage_account(user_id, action)
        else:  # unsuspend
            msg = QMessageBox(self)
            msg.setWindowTitle("Unsuspend Account")
            msg.setText("Unsuspend this user's account?")
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            msg.setDefaultButton(QMessageBox.StandardButton.No)
            msg.setStyleSheet(DIALOG_STYLE)
            if msg.exec() != QMessageBox.StandardButton.Yes:
                return
            self._community_api.manage_account(user_id, action)

    def _on_account_action_done(self, user_id, action):
        """Handle successful account management action."""
        labels = {
            "suspend": "Account suspended",
            "unsuspend": "Account unsuspended",
            "cancel_subscription": "Subscription cancelled",
        }
        self._show_toast(labels.get(action, action.title()))
        # Update suspended dialog if open
        dlg = getattr(self, "_suspended_dialog", None)
        if dlg and action == "unsuspend":
            dlg.remove_user(user_id)

    def _on_account_action_error(self, _user_id, message):
        """Handle account management failure."""
        self._show_toast(f"Account action failed: {message}")

    # ------------------------------------------------------------------
    # Suspended users dialog
    # ------------------------------------------------------------------

    def _on_show_suspended(self):
        """Open the suspended users dialog and fetch the list."""
        from widgets.suspended_users_dialog import SuspendedUsersDialog
        dlg = SuspendedUsersDialog(self)
        self._suspended_dialog = dlg

        # Connect fetch results to dialog
        self._community_api.suspended_loaded.connect(dlg.set_users)
        self._community_api.suspended_error.connect(dlg.set_error)

        # Connect action buttons
        dlg.unsuspend_clicked.connect(self._on_suspended_unsuspend)
        dlg.cancel_sub_clicked.connect(self._on_suspended_cancel_sub)

        self._community_api.fetch_suspended()
        dlg.exec()

        # Disconnect to avoid stale references
        self._community_api.suspended_loaded.disconnect(dlg.set_users)
        self._community_api.suspended_error.disconnect(dlg.set_error)
        self._suspended_dialog = None
        dlg.deleteLater()

    def _on_suspended_unsuspend(self, user_id):
        """Confirm and unsuspend a user from the suspended dialog."""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Unsuspend Account",
            "Unsuspend this user's account?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._suspended_action_uid = user_id
        self._community_api.manage_account(user_id, "unsuspend")

    def _on_suspended_cancel_sub(self, user_id):
        """Confirm and cancel subscription from the suspended dialog."""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Cancel Subscription",
            "Cancel this user's subscription?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._community_api.manage_account(user_id, "cancel_subscription")

    def _on_review_preview_action(self, community_id, action):
        """Handle approve/reject/quarantine from the detail preview."""
        if action == "reject":
            self._on_review_reject(community_id)
        elif action == "quarantine":
            self._on_review_quarantine(community_id)
        else:
            self._on_review_approve(community_id)

    def _return_to_review_list(self):
        """Shared helper to return from review detail preview to review card list."""
        self._autohide_timer.stop()
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.recipe_list)
        self._cb_opacity.setOpacity(1.0)
        self.step_navigator.hide()
        self._info_panel.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        self._configure_review_commands()
        self.recipe_list.set_command_bar(self.command_bar)
        self._position_overlays()

    def _on_review_detail_back(self):
        """Return from review detail preview to review list (keep cached import)."""
        self._review_current_cid = None
        self._review_preview_item = None
        self._comparison_cid = None
        self._comparison_item = None
        self._showing_comparison = False
        self._return_to_review_list()

    # ── Origin / Producer items / Comparison ─────────────────────────

    def _on_review_show_producer_items(self, uploader_user_id):
        """Open dialog showing all published items by the origin uploader."""
        if not uploader_user_id:
            self._show_toast("No uploader info available")
            return

        from widgets.producer_items_dialog import ProducerItemsDialog

        origin_name = (self._review_preview_item or {}).get(
            "community_origin_producer", ""
        )
        # Filter to same type as the item being reviewed
        self._producer_items_type_filter = (
            self._review_preview_item or {}
        ).get("type", "")
        dlg = ProducerItemsDialog(producer_name=origin_name, parent=self)
        self._producer_items_dialog = dlg
        dlg.compare_clicked.connect(self._on_producer_compare_clicked)
        dlg.show()
        self._community_api.fetch_producer_items(uploader_user_id)

    def _on_producer_items_loaded(self, items):
        """Populate producer items dialog with results."""
        dlg = getattr(self, "_producer_items_dialog", None)
        if not dlg:
            return
        # Filter to match the type being reviewed (recipe→recipes, book→books)
        type_filter = getattr(self, "_producer_items_type_filter", "")
        if type_filter:
            items = [i for i in items if i.get("type") == type_filter]
        dlg.set_items(items)

    def _on_producer_items_error(self, message):
        """Show error in producer items dialog."""
        dlg = getattr(self, "_producer_items_dialog", None)
        if dlg:
            dlg.set_error(message)

    def _on_producer_compare_clicked(self, item):
        """Download a producer's item for comparison."""
        dlg = getattr(self, "_producer_items_dialog", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._producer_items_dialog = None

        community_id = item.get("community_id", "")

        # Check if already downloaded and cached (kept until exit review mode)
        cached = self._review_temp_imports.get(community_id)
        if cached:
            self._comparison_item = item
            self._comparison_cid = community_id
            is_book = item.get("type") == "book"
            if is_book and cached.get("book_id") is not None:
                self._showing_comparison = True
                self.show_book_view(book_id=cached["book_id"])
            elif cached.get("recipe_id") is not None:
                self._showing_comparison = True
                self.show_recipe_detail(cached["recipe_id"])
            self._configure_review_preview_commands()
            self.command_bar.updateGeometry()
            QApplication.processEvents()
            self._position_overlays()
            return

        self._comparison_item = item
        self._comparison_downloading = True
        title = item.get("title", "item")
        self._community_download_progress = self._create_styled_progress(
            f"Downloading '{title}' for comparison...", "Compare",
            cancel_callback=self._community_api.cancel_download,
        )
        # Fetch fresh detail to get a signed zipUrl
        self._awaiting_download_detail = True
        item_type = item.get("type", "recipe")
        if item_type == "book":
            self._community_api.fetch_book_detail(community_id)
        elif item_type == "article":
            self._community_api.fetch_article_detail(community_id)
        else:
            self._community_api.fetch_recipe_detail(community_id)

    def _on_comparison_download_ready(self, _community_id, local_zip_path):
        """Import the comparison item and enable toggle."""
        dlg = getattr(self, "_community_download_progress", None)
        if dlg:
            dlg.close()
            dlg.deleteLater()
            self._community_download_progress = None

        item = self._comparison_item or {}
        is_book = item.get("type") == "book"
        zip_to_clean = local_zip_path

        progress = self._create_styled_progress("Loading comparison...", "Compare")

        def _remove_zip():
            try:
                os.remove(zip_to_clean)
            except OSError:
                pass

        if is_book:
            worker = _IOWorker(import_book_from_zip, local_zip_path)

            def _on_imported(new_book_id):
                progress.close()
                progress.deleteLater()
                _remove_zip()
                hide_temp_book(new_book_id)
                cid = item.get("community_id", "")
                self._review_temp_imports[cid] = {"book_id": new_book_id, "recipe_id": None}
                self._comparison_cid = cid
                self.show_book_view(book_id=new_book_id)
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()
        else:
            worker = _IOWorker(import_recipe_from_zip, local_zip_path)

            def _on_imported(new_recipe_id):
                progress.close()
                progress.deleteLater()
                _remove_zip()
                hide_temp_recipe(new_recipe_id)
                cid = item.get("community_id", "")
                self._review_temp_imports[cid] = {"book_id": None, "recipe_id": new_recipe_id}
                self._comparison_cid = cid
                self._showing_comparison = True
                self.show_recipe_detail(new_recipe_id)
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()

        worker.finished.connect(_on_imported)
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_toast(f"Comparison import failed: {msg}")
        )
        self._io_worker = worker
        worker.start()

    def _on_review_toggle_comparison(self):
        """Toggle between showing the upload and the comparison item."""
        if not self._comparison_cid or not self._review_current_cid:
            return

        if self._showing_comparison:
            # Switch back to the upload
            cached = self._review_temp_imports.get(self._review_current_cid)
            if cached:
                self._showing_comparison = False
                if cached.get("book_id") is not None:
                    self.show_book_view(book_id=cached["book_id"])
                elif cached.get("recipe_id") is not None:
                    self.show_recipe_detail(cached["recipe_id"])
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()
        else:
            # Switch to comparison
            cached = self._review_temp_imports.get(self._comparison_cid)
            if cached:
                self._showing_comparison = True
                if cached.get("book_id") is not None:
                    self.show_book_view(book_id=cached["book_id"])
                elif cached.get("recipe_id") is not None:
                    self.show_recipe_detail(cached["recipe_id"])
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()

    def _on_community_preview(self, community_id):
        """Show the community detail view for a recipe/book."""
        item = self.recipe_list.get_community_item(community_id)
        if not item:
            return
        # Find the card's current thumbnail pixmap
        pixmap = None
        for card in self.recipe_list._cards:
            if card._community_id == community_id:
                pm = card.image_label.pixmap()
                if pm and not pm.isNull():
                    pixmap = pm
                break
        self._show_community_detail(item, pixmap)

    def _show_community_detail(self, item, pixmap=None):
        """Switch to community detail view."""
        self._autohide_timer.stop()
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.community_detail)
        self._cb_opacity.setOpacity(1.0)
        self.step_navigator.hide()
        self._info_panel.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        self._configure_community_detail_commands(item)
        self.community_detail.set_command_bar(self.command_bar)
        self.community_detail.load_item(item, pixmap)
        self._position_overlays()
        # For paid books, fetch fresh detail to get current purchase status.
        # The _on_book_detail_result handler will update the item + refresh UI.
        if item.get("price_type") == "paid" and self._auth_id_token:
            cid = item.get("community_id", "")
            if cid:
                self._community_api.fetch_book_detail(cid)

    def _configure_community_detail_commands(self, item):
        """Configure command bar for community detail view."""
        self.play_video_toggle = None
        self.command_bar.clear()
        self.command_bar.add_button(
            "Back", self._on_community_detail_back,
            icon=platform_icon("chevron.left", weight="regular", point_size=48, color="white")
        )
        self.command_bar.add_stretch()

        community_id = item.get("community_id", "")
        price_type = item.get("price_type", "free")
        price_cents = item.get("price_cents", 0)
        is_purchased = item.get("is_purchased", False)
        is_creator = item.get("is_creator", False)

        if price_type == "paid" and price_cents > 0 and not is_purchased and not is_creator:
            # Paid book user hasn't bought — show Buy button
            price_str = f"${price_cents / 100:.2f}"
            buy_btn = self.command_bar.add_button(
                f"Buy \u2014 {price_str}",
                lambda: self._on_community_purchase_requested(community_id),
                icon=platform_icon("cart", weight="regular", point_size=48, color="white")
            )
            buy_btn.setStyleSheet("""
                QPushButton { background-color: #1a6b3a; color: white; border: 1px solid #22874a;
                    border-radius: 4px; padding: 8px 18px; font-size: 14px; font-weight: bold; }
                QPushButton:hover { background-color: #22874a; }
            """)
        else:
            # Free book, or user owns it — show Download button
            dl_btn = self.command_bar.add_button(
                "Download", lambda: self._on_community_download_requested(community_id),
                icon=platform_icon("arrow.down.circle", weight="regular", point_size=48, color="white")
            )
            dl_btn.setStyleSheet("""
                QPushButton { background-color: #1a3a6a; color: white; border: 1px solid #2a5a9a;
                    border-radius: 4px; padding: 8px 18px; font-size: 14px; }
                QPushButton:hover { background-color: #2a5a9a; }
            """)

    def _on_community_purchase_requested(self, community_id):
        """Open website checkout page for a paid book purchase."""
        if not self._ensure_authenticated():
            return
        # Open the website checkout page in the default browser
        self._purchase_poll_cid = community_id
        from PySide6.QtGui import QDesktopServices
        url = f"{WEBSITE_URL}/checkout/{community_id}?source=desktop"
        QDesktopServices.openUrl(QUrl(url))
        self._show_toast("Complete your purchase in the browser")
        self._purchase_poll_timer.start()

    def _poll_purchase_status(self):
        """Poll book detail to detect completed purchase."""
        cid = self._purchase_poll_cid
        if not cid:
            self._purchase_poll_timer.stop()
            return
        self._community_api.fetch_book_detail(cid)

    def _on_book_detail_result(self, item):
        """Handle book detail response — updates cached item and refreshes UI.

        Used for both purchase polling and fresh detail fetches on preview.
        """
        cid = item.get("community_id", "")
        if not cid:
            return

        # Update the cached community item with fresh purchase/pricing data
        is_purchased = item.get("is_purchased", False)
        price_type = item.get("price_type", "free")
        price_cents = item.get("price_cents", 0)
        for i, existing in enumerate(self.recipe_list._community_items):
            if existing.get("community_id") == cid:
                self.recipe_list._community_items[i].update({
                    "is_purchased": is_purchased,
                    "is_creator": item.get("is_creator", False),
                    "zip_url": item.get("zip_url", ""),
                    "price_type": price_type,
                    "price_cents": price_cents,
                })
                break

        # Update the card's price badge to reflect purchase status
        for card in self.recipe_list._cards:
            if getattr(card, "_community_id", None) == cid:
                card.set_price_info(price_type, price_cents, is_purchased)
                break

        # Update homepage card's cached item so next click uses fresh data
        for card in self.community_home.all_cards():
            if hasattr(card, 'community_id') and card.community_id == cid:
                if hasattr(card, '_item'):
                    card._item["is_purchased"] = is_purchased
                    card._item["is_creator"] = item.get("is_creator", False)
                    card._item["zip_url"] = item.get("zip_url", "")
                break

        # If detail view is showing this item, refresh it
        if (self.stacked_widget.currentWidget() == self.community_detail
                and self.community_detail._community_id == cid):
            updated = self.recipe_list.get_community_item(cid) or item
            pixmap = self.community_detail._image_label.pixmap()
            self.community_detail.load_item(updated, pixmap)
            self._configure_community_detail_commands(updated)
            self.community_detail.set_command_bar(self.command_bar)

        # If purchase polling is active and purchase completed, stop and notify
        if self._purchase_poll_cid == cid and item.get("is_purchased"):
            self._purchase_poll_timer.stop()
            self._purchase_poll_cid = None
            self._purchased_book_ids.add(cid)
            self._show_toast("Purchase complete! You can now download this book.")

        # If awaiting detail for download, start the zip download
        if self._awaiting_download_detail:
            self._awaiting_download_detail = False
            self._start_download_from_detail(item)

    def _on_recipe_detail_result(self, item):
        """Handle recipe detail response — used for download detail fetch."""
        if self._awaiting_download_detail:
            self._awaiting_download_detail = False
            self._start_download_from_detail(item)

    def _start_download_from_detail(self, detail_item):
        """Extract signed zipUrl from detail response and start download."""
        zip_url = detail_item.get("zip_url", "")
        if not zip_url:
            dlg = getattr(self, "_community_download_progress", None)
            if dlg:
                dlg.close()
            self._show_toast("Download URL not available")
            return
        community_id = detail_item.get("community_id", "")
        self._community_api.download_zip(community_id, zip_url)

    def _on_detail_error(self, error_msg):
        """Handle recipe detail fetch error — abort download if awaiting."""
        if self._awaiting_download_detail:
            self._awaiting_download_detail = False
            dlg = getattr(self, "_community_download_progress", None)
            if dlg:
                dlg.close()
            self._show_toast(f"Download failed: {error_msg}")

    def _on_book_detail_error(self, message):
        """Handle book detail error — abort download if awaiting, else ignore (polling retries)."""
        if self._awaiting_download_detail:
            self._awaiting_download_detail = False
            dlg = getattr(self, "_community_download_progress", None)
            if dlg:
                dlg.close()
            self._show_toast(f"Download failed: {message}")

    def _stop_purchase_polling(self):
        """Stop polling for purchase status."""
        self._purchase_poll_timer.stop()
        self._purchase_poll_cid = None

    def _on_community_detail_back(self):
        """Return from community detail view to community homepage."""
        self.stacked_widget.setCurrentWidget(self.community_home)
        self._configure_community_home_commands()
        self.community_home.set_command_bar(self.command_bar)
        # Restore search field text and filter state if search was active
        if self._home_search_active:
            if hasattr(self, "_search_field") and self._search_field:
                self._search_field.setText(self._home_search_query)
            try:
                if hasattr(self, "_clear_filters_btn") and self._clear_filters_btn:
                    self._clear_filters_btn.setVisible(True)
            except RuntimeError:
                pass
        self._position_overlays()
        QTimer.singleShot(100, self._request_visible_thumbnails)

    def _on_new_recipe(self):
        """Create a new recipe and open it in add mode (edit mode for a new recipe)."""
        from models.recipe_data import RecipeData

        # Create empty RecipeData with recipe_id=None to indicate "add mode"
        new_recipe = RecipeData(
            recipe_id=None,
            title="New Recipe",
            description="",
            prep_time_min=None,
            cook_time_min=None,
            cuisine_type=None,
            difficulty=None,
            main_image_path="media/default.jpg",
            intro_video_path=None,
            steps=[],
            intro_ingredients=[],
            tags=[],
            producer=self._auth_display_name,
        )

        # Show recipe detail view with the new recipe
        self._show_new_recipe(new_recipe)

    def _on_new_article(self):
        """Create a new article and open it in add mode."""
        from models.recipe_data import RecipeData

        new_article = RecipeData(
            recipe_id=None,
            title="New Article",
            description="",
            prep_time_min=None,
            cook_time_min=None,
            cuisine_type=None,
            difficulty=None,
            main_image_path=None,
            intro_video_path=None,
            steps=[],
            intro_ingredients=[],
            tags=[],
            content_type="article",
            producer=self._auth_display_name,
        )

        self._article_mode = True
        self._show_new_recipe(new_article)
        self._apply_article_mode()

        # Auto-show article tips for new articles
        self._article_tip_panel.show()
        self._article_tip_panel.raise_()
        if self._article_tips_btn is not None:
            self._article_tips_btn.blockSignals(True)
            self._article_tips_btn.setChecked(True)
            self._article_tips_btn.blockSignals(False)
        self._position_overlays()

    def _apply_article_mode(self):
        """Hide/relabel controls for article mode. Restores state when not in article mode."""
        if not self._article_mode:
            # Restore widgets that article mode hides
            self.recipe_detail.directions_editor.set_article_mode(False)
            self.recipe_detail.directions_editor.btn_link_step.setVisible(True)
            self.recipe_detail.directions_editor.btn_link_web.setVisible(True)
            self.recipe_detail.ingredients_editor.setVisible(
                self.recipe_detail._layout_mode in ("both", "ingredients")
            )
            return

        # Hide voice controls
        for attr in ("_help_btn", "_mic_toggle", "_tts_toggle",
                     "_hands_free_toggle", "_headset_toggle", "_refresh_audio_btn"):
            w = getattr(self, attr, None)
            if w is not None:
                w.hide()

        # Hide clipboard and moiety buttons
        self._copy_steps_btn.hide()
        self._view_clipboard_btn.hide()
        if hasattr(self, "_paste_clipboard_btn"):
            self._paste_clipboard_btn.hide()
        if hasattr(self, "_moiety_btn"):
            self._moiety_btn.hide()

        # Hide grocery list and tips buttons
        self._grocery_list_btn.hide()
        if hasattr(self, "_view_tips_btn"):
            self._view_tips_btn.hide()
        if hasattr(self, "_add_tip_btn"):
            self._add_tip_btn.hide()

        # Relabel step tooltips to "paragraph"
        if hasattr(self, "_insert_step_btn"):
            self._insert_step_btn.setToolTip("Insert paragraph")
        if hasattr(self, "_append_step_btn"):
            self._append_step_btn.setToolTip("Append paragraph")
        if hasattr(self, "_delete_step_btn"):
            self._delete_step_btn.setToolTip("Delete paragraph")

        # Hide ingredients editor and aggregate warning
        self.recipe_detail.ingredients_editor.hide()
        self.recipe_detail.aggregate_warning.hide()

        # Enable article link mode (URL links instead of step links)
        self.recipe_detail.directions_editor.set_article_mode(True)

        # Relabel directions editor title
        self.recipe_detail.directions_editor.set_title("Paragraph")

        # Update layout mode menu for article
        self._update_layout_mode_menu(
            self.recipe_detail._current_step_index
        )

        # Update step indicator
        self._update_step_indicator(
            self.recipe_detail._current_step_index
        )

        # Default to directions layout (paragraph view)
        self.recipe_detail.set_layout_mode("directions")

        # Article tips toggle button — only add if not already in the command bar
        if self._article_tips_btn is not None \
                and self._article_tips_btn in self.command_bar.command_widgets:
            self._article_tips_btn.setVisible(True)
        else:
            self._article_tips_btn = self.command_bar.add_toggle_button(
                "", self._on_article_tips_toggled, size=32,
                tooltip="Article Tips",
            )
            self._article_tips_btn.setIcon(
                platform_icon("lightbulb.max", weight="regular", point_size=48, color="white")
            )
        # Sync with current panel visibility
        self._article_tips_btn.setChecked(self._article_tip_panel.isVisible())
        # Move fullscreen button to the right of article tips
        if self._detail_fs_btn is not None:
            idx = self.command_bar.layout.indexOf(self._detail_fs_btn)
            if idx >= 0:
                self.command_bar.layout.removeWidget(self._detail_fs_btn)
                self.command_bar.layout.addWidget(self._detail_fs_btn)

    def _on_article_tips_toggled(self, checked):
        """Show or hide the article tip panel."""
        if checked:
            self._article_tip_panel.show()
            self._article_tip_panel.raise_()
            self._position_overlays()
        else:
            self._article_tip_panel.hide()

    def _on_article_tips_dismissed(self):
        """Handle 'Got it' or close button on the article tip panel."""
        btn = self._article_tips_btn
        if btn is not None:
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

    def _show_moiety_tip_panel(self):
        """Show the 'What's a Moiety?' explanation panel."""
        self._moiety_tip_panel.show()
        self._moiety_tip_panel.raise_()
        self._position_moiety_tip_panel()

    def _on_moiety_tips_dismissed(self):
        """Handle 'Got it' or close button on the moiety tip panel."""
        self._moiety_tip_panel.hide()

    def _on_moiety_btn_clicked(self):
        """Show moiety submenu: Open Moiety Panel / What's This?"""
        menu = QMenu(self._moiety_btn)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #555;
                padding: 4px 0;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a3a3a; }
            QMenu::separator { height: 1px; background: #555555; margin: 4px 8px; }
        """)
        act_open = menu.addAction("Open Moiety Panel")
        menu.addSeparator()
        act_whats = menu.addAction("What's This?")

        pos = self._moiety_btn.mapToGlobal(self._moiety_btn.rect().bottomLeft())
        chosen = menu.exec(pos)

        if chosen == act_open:
            self._show_moiety_panel()
        elif chosen == act_whats:
            self._show_moiety_tip_panel()

    def _show_moiety_panel(self):
        """Show the moiety browser panel on the right half."""
        if self._moiety_tip_panel.isVisible():
            self._moiety_tip_panel.hide()
        self._moiety_panel.refresh()
        self._moiety_panel.show()
        self._moiety_panel.raise_()
        self._position_moiety_panel()

    def _on_moiety_panel_dismissed(self):
        """Handle close button on the moiety panel."""
        self._moiety_panel.hide()

    def _on_new_book(self):
        """Create a new book and open it in book view edit mode.

        Admin users see a submenu to choose between a normal book and a
        Book of Moiety.  Non-admin users create a normal book directly.
        """
        is_bom = False
        if self._is_admin:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: #2a2a2a;
                    color: white;
                    border: 1px solid #555;
                    padding: 4px 0;
                }
                QMenu::item { padding: 6px 20px; }
                QMenu::item:selected { background-color: #3a3a3a; }
            """)
            menu.addAction("New Book")
            act_bom = menu.addAction("New Book of Moiety")
            pos = self.cursor().pos()
            chosen = menu.exec(pos)
            if chosen is None:
                return  # cancelled
            is_bom = (chosen == act_bom)

        new_book = BookData(
            book_id=None,
            title="New Book of Moiety" if is_bom else "New Book",
            description="",
            producer=self._auth_display_name,
            cover_image_path="media/default.jpg",
            is_book_of_moiety=is_bom,
            categories=[
                BookCategoryData(
                    category_id=None, name="Uncategorized", display_order=0,
                ),
            ],
        )
        self.show_book_view(book_data=new_book)
        # Enter edit mode immediately
        self._book_edit_snapshot = copy.deepcopy(new_book)
        self._book_is_new = True
        self._set_book_editing(True)

    def _adjust_font_size(self, delta):
        """Adjust font size and persist the setting."""
        self.recipe_detail.adjust_font_size(delta)
        self.book_view.toc_widget.adjust_font_size(delta)
        self.book_view.description_editor.adjust_font_size(delta)
        self._article_tip_panel.adjust_font_size(delta)
        self._moiety_tip_panel.adjust_font_size(delta)
        # Only persist when not in fullscreen/maximized — fullscreen forces
        # max font and restores the saved value on exit.
        if not self.isFullScreen() and not self.isMaximized():
            current = self.recipe_detail.directions_editor._font_size
            self._settings.setValue("font_size_delta", current - 14)

    def _configure_book_view_commands(self):
        """Configure command bar buttons for book view."""
        self.play_video_toggle = None
        self.command_bar.clear()
        icon_btn_style = """
            QPushButton {
                min-width: 32px; max-width: 32px;
                min-height: 32px; max-height: 32px;
                padding: 0px; font-size: 20px;
            }
        """
        icon_btn_style_16 = """
            QPushButton {
                min-width: 32px; max-width: 32px;
                min-height: 32px; max-height: 32px;
                padding: 0px; font-size: 16px;
                color: #cccccc;
            }
            QPushButton:hover { background-color: #4a4a4a; color: white; }
            QPushButton:pressed { background-color: #1e1e1e; }
        """
        self._book_back_btn = self.command_bar.add_button(
            "", self._on_book_back, tooltip="Back",
            icon=platform_icon("arrow.left", weight="regular", point_size=48,
                               color="white", windows_name="ArrowLeft8")
        )
        self._book_back_btn.setFixedSize(32, 32)
        self._book_back_btn.setStyleSheet(icon_btn_style)
        self._book_save_btn = self.command_bar.add_button(
            "", self._on_book_edit_save, tooltip="Save",
            icon=platform_icon("square.and.arrow.down", weight="regular",
                               point_size=48, color="white")
        )
        self._book_save_btn.setFixedSize(32, 32)
        self._book_save_btn.setStyleSheet(icon_btn_style_16)
        self._book_save_btn.hide()
        self._book_cancel_btn = self.command_bar.add_button(
            "", self._on_book_edit_cancel, tooltip="Cancel",
            icon=platform_icon("xmark.square", weight="regular",
                               point_size=48, color="white")
        )
        self._book_cancel_btn.setFixedSize(32, 32)
        self._book_cancel_btn.setStyleSheet(icon_btn_style_16)
        self._book_cancel_btn.hide()
        # Thin separator after save/cancel group
        self._book_edit_separator = QWidget()
        self._book_edit_separator.setFixedSize(7, 24)
        self._book_edit_separator.setStyleSheet(
            "border-left: 1px solid #888888; margin-left: 3px; margin-right: 3px;"
        )
        self.command_bar.layout.insertWidget(
            len(self.command_bar.command_widgets), self._book_edit_separator
        )
        self.command_bar.command_widgets.append(self._book_edit_separator)
        self._book_edit_separator.hide()
        self._book_edit_btn = self.command_bar.add_button(
            "", self._on_book_edit_toggled, tooltip="Edit",
            icon=platform_icon("pencil", weight="regular", point_size=48, color="white")
        )
        self._book_edit_btn.setFixedSize(32, 32)
        self._book_edit_btn.setStyleSheet(icon_btn_style)
        font_down_btn = self.command_bar.add_button(
            "A-", lambda: self._adjust_font_size(-1),
            tooltip="Decrease font size",
        )
        font_down_btn.setFixedSize(32, 32)
        font_down_btn.setStyleSheet(icon_btn_style)
        font_up_btn = self.command_bar.add_button(
            "A+", lambda: self._adjust_font_size(1),
            tooltip="Increase font size",
        )
        font_up_btn.setFixedSize(32, 32)
        font_up_btn.setStyleSheet(icon_btn_style)
        # Layout mode dropdown
        self._book_layout_mode_btn = self.command_bar.add_menu_button(
            [
                ("TOC && Description", "both"),
                ("Table of Contents", "toc"),
                ("Description", "description"),
                ("Image", "image"),
                ("Tags", "tags"),
                ("Details", "details"),
            ],
            self._on_book_layout_mode_changed,
            tooltip="Customize view",
        )
        # Edit-only: cover image and intro video buttons
        self._book_image_btn = self.command_bar.add_button(
            "", self._on_book_cover_image, tooltip="Modify cover image",
            icon=platform_icon("photo.badge.arrow.down", weight="regular",
                               point_size=48, color="white"),
        )
        self._book_image_btn.setFixedSize(38, 32)
        self._book_image_btn.setIconSize(QSize(28, 28))
        self._book_image_btn.setStyleSheet(icon_btn_style)
        self._book_image_btn.hide()
        self._book_video_btn = self.command_bar.add_button(
            "", self._on_book_video_btn_clicked, tooltip="Video options",
            icon=platform_icon("video", weight="regular", point_size=48,
                               color="white"),
        )
        self._book_video_btn.setFixedSize(32, 32)
        self._book_video_btn.setStyleSheet(icon_btn_style)
        self._book_video_btn.hide()
        self.command_bar.add_stretch()
        # Reset edit mode and layout mode (in case returning from recipe detail)
        self.book_view.toc_widget.set_edit_mode(False)
        self.book_view.set_layout_mode("both")
        # Wire the TOC widget's "+ Recipes" button (edit-mode toolbar)
        try:
            self.book_view.toc_widget.add_recipes_requested.disconnect(
                self._on_add_recipes_to_book
            )
        except RuntimeError:
            pass
        self.book_view.toc_widget.add_recipes_requested.connect(
            self._on_add_recipes_to_book
        )
        # Wire the book's tags editor
        try:
            self.book_view.tags_editor.tagsChanged.disconnect(
                self._on_book_tags_changed
            )
        except RuntimeError:
            pass
        self.book_view.tags_editor.tagsChanged.connect(
            self._on_book_tags_changed
        )

    def _on_recipe_selected(self, recipe_id):
        """Handle recipe card click - navigate to detail view."""
        self._came_from_book = False
        self.show_recipe_detail(recipe_id)

    def _on_book_recipe_clicked(self, recipe_id):
        """Handle recipe click from book TOC - navigate to detail view."""
        self._came_from_book = True
        self.show_recipe_detail(recipe_id)

    def _on_book_selected(self, book_id):
        """Handle book card click - navigate to book view."""
        self.show_book_view(book_id)

    def _on_step_changed(self, step_index):
        """Handle step navigator click — update detail view."""
        self.recipe_detail.load_step(step_index)
        self._update_play_video_state(step_index)
        self._update_step_indicator(step_index)
        self._update_layout_mode_menu(step_index)
        self._update_video_button_state(step_index)
        if self._article_mode:
            # Re-hide ingredients after step load refreshes them
            self.recipe_detail.ingredients_editor.hide()
            self.recipe_detail.aggregate_warning.hide()
            self.recipe_detail.directions_editor.set_title("Paragraph")

    def _on_step_link_from_editor(self, step_index):
        """Handle step link navigation from the rich text editor.

        Args:
            step_index: 0-based step index to navigate to
        """
        # Validate step index
        rd = self.recipe_detail._recipe_data
        if not rd:
            return

        total_steps = len(rd.steps) + 1
        if 0 <= step_index < total_steps:
            # Update navigator selection
            self.step_navigator.current_step = step_index
            self.step_navigator._update_active_step()
            self.step_navigator.scroll_to_step(step_index)

            # Navigate to the step
            self._on_step_changed(step_index)

    def _update_video_button_state(self, step_index):
        """Enable/disable video button - videos can be added to any step including intro."""
        if hasattr(self, "_video_btn") and self._video_btn is not None:
            # Videos are supported on all steps (intro stores in intro_video_path)
            self._video_btn.setEnabled(True)

    def _update_layout_mode_menu(self, step_index):
        """Update layout mode menu - Tags/Details/Tips only available on intro step."""
        if not hasattr(self, "_layout_mode_btn") or self._layout_mode_btn is None:
            return

        # Article mode: simplified menu
        if self._article_mode:
            items = [("Paragraph", "directions"), ("Image", "image")]
            if step_index == 0:
                items.append(("Tags", "tags"))
            else:
                if self.recipe_detail._layout_mode in ("tags",):
                    self.recipe_detail.set_layout_mode("directions")
            self.command_bar.update_menu_button_items(self._layout_mode_btn, items)
            return

        base_items = [
            ("Ingredients && Directions", "both"),
            ("Ingredients", "ingredients"),
            ("Directions", "directions"),
            ("Image", "image"),
        ]
        if step_index == 0:
            # Intro step - include Tags and Details options
            items = base_items + [("Tags", "tags"), ("Details", "details")]
            # Add Tips option for community recipes
            rd = self.recipe_detail._recipe_data
            if rd and rd.community_origin_id:
                items.append(("Recipe Tips", "tips"))
        else:
            # Other steps - no Tags, Details, or Tips option
            items = base_items
            # If currently in an intro-only mode, switch to default
            if self.recipe_detail._layout_mode in ("tags", "details", "tips"):
                self.recipe_detail.set_layout_mode("both")
        self.command_bar.update_menu_button_items(self._layout_mode_btn, items)

    def _update_step_indicator(self, step_index):
        """Update the step indicator label text (e.g. 'Step 3 of 8')."""
        rd = self.recipe_detail._recipe_data
        if not rd:
            self._step_indicator.hide()
            return
        total = len(rd.steps) + 1  # +1 for intro
        if self._article_mode:
            if step_index == 0:
                self._step_indicator.setText(
                    f"Intro \u00b7 {total - 1} paragraph{'s' if total - 1 != 1 else ''}"
                )
            else:
                self._step_indicator.setText(
                    f"Paragraph {step_index} of {total - 1}"
                )
        else:
            if step_index == 0:
                self._step_indicator.setText(f"Intro \u00b7 {total - 1} steps")
            else:
                self._step_indicator.setText(f"Step {step_index} of {total - 1}")
        self._step_indicator.adjustSize()
        self._position_step_indicator()

    def _position_step_indicator(self):
        """Position the step indicator in the top-right corner of the central widget."""
        cw = self.centralWidget()
        if not cw:
            return
        margin = 16
        self._step_indicator.move(
            cw.width() - self._step_indicator.width() - margin,
            margin,
        )

    def _on_back(self):
        if self._viewing_clipboard:
            self._on_clipboard_back()
            return
        # Leaving recipe detail via back — turn off mic permanently
        if self._is_voice_listening:
            self._voice.stop_listening()
        self._mic_saved_for_nav = None
        if self._came_from_book and self._current_book_data is not None:
            self._came_from_book = False
            self.show_book_view(book_id=self._current_book_data.book_id)
            # Restore review preview commands if we're reviewing a book
            if self._review_current_cid is not None:
                self._configure_review_preview_commands()
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._position_overlays()
        else:
            self._return_to_home()

    def _on_play_overlay_clicked(self):
        """Route video play overlay click to the appropriate handler."""
        if self.stacked_widget.currentWidget() is self.book_view:
            self._play_book_intro_video()
        else:
            self._play_current_step_video()

    def _get_current_step_video_path(self):
        """Return the absolute video path for the current step, or None."""
        rd = self.recipe_detail._recipe_data
        idx = self.recipe_detail._current_step_index
        if rd and idx == 0:
            # Intro step uses intro_video_path from recipe
            rel = rd.intro_video_path
            if rel:
                if os.path.isabs(rel):
                    return rel
                return os.path.join(str(DATA_DIR), rel)
        elif rd and 1 <= idx <= len(rd.steps):
            rel = rd.steps[idx - 1].video_path
            if rel:
                if os.path.isabs(rel):
                    return rel
                return os.path.join(str(DATA_DIR), rel)
        return None

    def _play_current_step_video(self):
        """Load and play the current step's video in the video player."""
        self._video_source_view = "recipe_detail"
        path = self._get_current_step_video_path()
        if not path or not os.path.isfile(path):
            return

        if self._is_voice_listening:
            if self._headset_active:
                # Headset active — keep voice active, disable hands-free
                # (video audio leaking through headset mic triggers false recordings)
                if self._voice._hands_free:
                    self._hands_free_paused_for_video = True
                    self._hands_free = False
                    self._voice._hands_free = False
            else:
                # Headset mode off — pause voice during video
                self._voice_paused_for_video = True
                if not self._warned_voice_paused:
                    self._warned_voice_paused = True
                    self._tts.speech_finished.connect(self._play_pending_video_after_warning)
                    self._pending_video_path = path
                    self._tts.speak(
                        "Voice control pauses during video playback. "
                        "To use voice commands during video, connect a headset, "
                        "toggle the headset button on, and use the wake word."
                    )
                    return
                self._voice.stop_listening()

        self.video_player.load_video(path)
        self.show_video_player()
        self.video_player.media_player.play()

    def _play_pending_video_after_warning(self):
        """Play the video that was deferred for the one-time TTS warning."""
        self._tts.speech_finished.disconnect(self._play_pending_video_after_warning)
        self._voice.stop_listening()
        path = self._pending_video_path
        self._pending_video_path = None
        if path:
            self.video_player.load_video(path)
            self.show_video_player()
            self.video_player.media_player.play()

    def _on_video_stopped(self):
        """Return to the previous view when video is stopped."""
        self._stop_fade_animations()
        # Re-enable AVAudioEngine VP after the view transition completes.
        # VP setup reconfigures audio hardware which blocks the main thread;
        # deferring avoids a visible glitch during the view switch.
        QTimer.singleShot(200, lambda: self._voice.set_force_qaudio(False))
        # Demote overlays back to child widgets of central_widget
        self._demote_voice_indicator()
        self._demote_info_panel()
        # Restore hands-free mode if it was disabled for video playback
        if getattr(self, "_hands_free_paused_for_video", False):
            self._hands_free_paused_for_video = False
            self._hands_free = True
            self._voice._hands_free = True
        # Resume voice listening if it was auto-paused for video playback
        if self._voice_paused_for_video:
            self._voice_paused_for_video = False
            self._listening_paused = False
            self._voice.start_listening()

        # Return to the view that launched the video
        if self._video_source_view == "book_view":
            self._voice.set_active_view("book_view")
            self._info_panel.set_view("book_view")
            self.stacked_widget.setCurrentWidget(self.book_view)
            self._cb_opacity.setOpacity(1.0)
            self._sn_opacity.setOpacity(1.0)
            self.command_bar.show()
            self.step_navigator.hide()
            self._position_overlays()
            self._update_book_video_overlay()
            return

        self._voice.set_active_view("recipe_detail")
        self._info_panel.set_view("recipe_detail")
        self.stacked_widget.setCurrentWidget(self.recipe_detail)
        self._cb_opacity.setOpacity(1.0)
        self._sn_opacity.setOpacity(1.0)
        self.command_bar.show()
        self.step_navigator.show()
        self._position_overlays()

        if self._viewing_clipboard:
            self._configure_clipboard_commands()
            self.command_bar.updateGeometry()
            self._autohide_timer.stop()
        else:
            # Uncheck the play toggle without firing the signal
            if hasattr(self, "play_video_toggle") and self.play_video_toggle is not None:
                try:
                    self.play_video_toggle.blockSignals(True)
                    self.play_video_toggle.setChecked(False)
                    self.play_video_toggle.blockSignals(False)
                except RuntimeError:
                    self.play_video_toggle = None
            # Don't start autohide timer if in edit mode
            if self.recipe_detail._editing:
                self._autohide_timer.stop()
            else:
                self._autohide_timer.start(self._autohide_timeout_ms)

        # Restore overlay visibility for the current step
        step_idx = self.recipe_detail._current_step_index
        self._update_play_video_state(step_idx)

    def _on_video_playback_state_changed(self, state):
        """Resume or pause voice control when video pauses or resumes."""
        if not self._voice_paused_for_video:
            return
        if state in (
            QMediaPlayer.PlaybackState.PausedState,
            QMediaPlayer.PlaybackState.StoppedState,
        ):
            # Video paused or ended — speaker audio stopped, safe to resume voice
            self._listening_paused = False
            self._voice.start_listening()
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            # Video resumed — re-pause voice to avoid feedback
            if self._is_voice_listening:
                self._voice.stop_listening()

    def _on_play_video_toggled(self, checked):
        """Handle play video toggle button."""
        if checked:
            self._play_current_step_video()
        else:
            pass  # Unchecking handled by _on_video_stopped

    def _update_play_video_state(self, step_index):
        """Enable/disable the play video button and overlay based on current step's video_path."""
        recipe = self.recipe_detail._recipe_data
        if recipe and step_index == 0:
            # Intro step uses intro_video_path from recipe
            has_video = bool(recipe.intro_video_path)
        elif recipe and step_index >= 1 and step_index <= len(recipe.steps):
            step = recipe.steps[step_index - 1]
            has_video = bool(step.video_path)
        else:
            has_video = False

        # Update toggle button if it still exists
        toggle_alive = False
        if hasattr(self, "play_video_toggle") and self.play_video_toggle is not None:
            try:
                self.play_video_toggle.isEnabled()
                toggle_alive = True
            except RuntimeError:
                self.play_video_toggle = None
        if toggle_alive:
            if not has_video and self.play_video_toggle.isChecked():
                self.play_video_toggle.blockSignals(True)
                self.play_video_toggle.setChecked(False)
                self.play_video_toggle.blockSignals(False)
            self.play_video_toggle.setEnabled(has_video)

        # Show or hide the video play overlay
        self._vpo_has_video = has_video
        self._fade_video_overlay(visible=has_video)

    def _on_edit_recipe(self):
        editing = not self.recipe_detail._editing
        self.recipe_detail.set_editing(editing)
        self._edit_btn.setVisible(not editing)
        self._back_btn.setVisible(not editing)
        self._grocery_list_btn.setVisible(not editing)
        self._save_btn.setVisible(editing)
        self._cancel_btn.setVisible(editing)
        self._edit_separator.setVisible(editing)
        self._image_btn.setVisible(editing)
        self._video_btn.setVisible(editing)
        self._insert_step_btn.setVisible(editing)
        self._append_step_btn.setVisible(editing)
        self._delete_step_btn.setVisible(editing)
        self._paste_clipboard_btn.setVisible(editing)
        self._moiety_btn.setVisible(editing)
        self.step_navigator.set_drag_enabled(editing)
        self._update_tips_buttons_visibility()
        if editing:
            # Disable video button if on intro step (videos only for steps 1+)
            self._update_video_button_state(self.recipe_detail._current_step_index)
            # Ensure bars are visible and stop the hide timer
            self._autohide_timer.stop()
            self._fade_bars(visible=True)
            # Save mic state, turn off, and disable the button while editing
            self._mic_saved_for_nav = self._is_voice_listening
            if self._is_voice_listening:
                self._voice.stop_listening()
            if hasattr(self, "_mic_toggle") and self._mic_toggle:
                self._mic_toggle.setEnabled(False)
            for attr in ("_tts_toggle", "_hands_free_toggle", "_headset_toggle", "_refresh_audio_btn"):
                w = getattr(self, attr, None)
                if w:
                    w.hide()
        else:
            # Re-enable mic button and defer mic restore so the UI repaints first
            if hasattr(self, "_mic_toggle") and self._mic_toggle:
                self._mic_toggle.setEnabled(True)
            self._deferred_mic_restore()
            # Resume auto-hide behavior
            self._autohide_timer.start(self._autohide_timeout_ms)
        # Re-apply article mode to hide buttons that were just made visible
        self._apply_article_mode()

    def _update_tips_buttons_visibility(self):
        """Show/hide Tips and Add Tip buttons based on recipe and edit state."""
        rd = self.recipe_detail._recipe_data
        is_community = bool(rd and rd.community_origin_id)
        editing = self.recipe_detail._editing
        show = is_community and not editing
        if hasattr(self, "_view_tips_btn"):
            self._view_tips_btn.setVisible(show)
        if hasattr(self, "_add_tip_btn"):
            self._add_tip_btn.setVisible(show)
        # Fetch tip count for pulse animation (only on first show per recipe)
        if show and not hasattr(self, "_tips_pulse_checked_id"):
            self._tips_pulse_checked_id = None
        if show and rd and rd.community_origin_id != getattr(self, "_tips_pulse_checked_id", None):
            self._tips_pulse_checked_id = rd.community_origin_id
            self._check_tips_for_pulse(rd.community_origin_id)

    # ------------------------------------------------------------------
    # Tips button pulse animation
    # ------------------------------------------------------------------

    def _check_tips_for_pulse(self, community_id):
        """Fetch tips to see if any exist, then start pulse if so."""
        # Tag this request so we can ignore stale responses
        self._tips_pulse_check_id = community_id

        def on_loaded(tips, _cursor):
            self._community_api.tips_loaded.disconnect(on_loaded)
            self._community_api.tips_error.disconnect(on_error)
            # Ignore if we navigated away or a different recipe is now active
            if getattr(self, "_tips_pulse_check_id", None) != community_id:
                return
            if tips:
                self._start_tips_pulse()

        def on_error(_msg):
            self._community_api.tips_loaded.disconnect(on_loaded)
            self._community_api.tips_error.disconnect(on_error)

        self._community_api.tips_loaded.connect(on_loaded)
        self._community_api.tips_error.connect(on_error)
        self._community_api.fetch_tips(community_id, limit=1)

    def _start_tips_pulse(self):
        """Pulse the tips button between white and blue."""
        if not hasattr(self, "_view_tips_btn"):
            return
        self._tips_pulse_timer = QTimer(self)
        self._tips_pulse_phase = 0.0
        self._tips_pulse_timer.setInterval(50)
        self._tips_pulse_timer.timeout.connect(self._tips_pulse_tick)
        self._tips_pulse_timer.start()

    def _tips_pulse_tick(self):
        """Update tips button icon color each tick."""
        import math
        try:
            if not hasattr(self, "_view_tips_btn") or self._view_tips_btn is None:
                self._stop_tips_pulse()
                return
            self._tips_pulse_phase += 0.05
            # Sine wave 0→1→0 for smooth pulse
            t = (math.sin(self._tips_pulse_phase * 2) + 1) / 2
            # Interpolate between white (255,255,255) and blue (0,120,212)
            r = int(255 - t * 255)
            g = int(255 - t * 135)
            b = int(255 - t * 43)
            color = f"#{r:02x}{g:02x}{b:02x}"
            self._view_tips_btn.setIcon(
                platform_icon("pencil.tip.crop.circle", weight="regular",
                              point_size=48, color=color)
            )
        except RuntimeError:
            self._stop_tips_pulse()  # Widget deleted, stop ticking

    def _stop_tips_pulse(self):
        """Stop the pulse, invalidate pending checks, and reset icon."""
        # Invalidate any in-flight pulse check so late responses are ignored
        self._tips_pulse_check_id = None
        if hasattr(self, "_tips_pulse_timer") and self._tips_pulse_timer is not None:
            self._tips_pulse_timer.stop()
            self._tips_pulse_timer = None
        try:
            if hasattr(self, "_view_tips_btn") and self._view_tips_btn is not None:
                self._view_tips_btn.setIcon(
                    platform_icon("pencil.tip.crop.circle", weight="regular",
                                  point_size=48, color="white")
                )
        except RuntimeError:
            pass  # Widget already deleted

    def _on_view_tips(self):
        """Toggle tips layout mode. Restores previous mode on second click."""
        if self.recipe_detail._layout_mode == "tips":
            # Restore previous layout mode
            restore = getattr(self, "_pre_tips_layout_mode", "both")
            self._on_layout_mode_changed(restore)
            return
        # Save current mode before switching to tips
        self._pre_tips_layout_mode = self.recipe_detail._layout_mode
        # Tips are on the intro step — navigate there first if needed
        if self.recipe_detail._current_step_index != 0:
            self._on_step_changed(0)
        self._on_layout_mode_changed("tips")
        self._stop_tips_pulse()
        self._fetch_tips()

    def _fetch_tips(self):
        """Fetch approved tips for the current community recipe."""
        rd = self.recipe_detail._recipe_data
        if not rd or not rd.community_origin_id:
            return

        def on_loaded(tips, next_cursor):
            self._community_api.tips_loaded.disconnect(on_loaded)
            self._community_api.tips_error.disconnect(on_error)
            self.recipe_detail.tips_widget.load_tips(tips)

        def on_error(msg):
            self._community_api.tips_loaded.disconnect(on_loaded)
            self._community_api.tips_error.disconnect(on_error)
            self.recipe_detail.tips_widget.load_tips([])

        self._community_api.tips_loaded.connect(on_loaded)
        self._community_api.tips_error.connect(on_error)
        self._community_api.fetch_tips(rd.community_origin_id)

    def _on_add_tip(self):
        """Open the Add Tip dialog and submit to backend."""
        if not self._ensure_authenticated("Sign In to Add a Tip"):
            return
        rd = self.recipe_detail._recipe_data
        if not rd or not rd.community_origin_id:
            return
        from widgets.add_tip_dialog import AddTipDialog
        dlg = AddTipDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        tip_text = dlg.get_tip_text()
        if not tip_text:
            return

        def on_submitted(tip_id):
            self._community_api.tip_submitted.disconnect(on_submitted)
            self._community_api.tip_submit_error.disconnect(on_submit_error)
            self._show_toast("Tip submitted — it will appear after the creator approves it")

        def on_submit_error(msg):
            self._community_api.tip_submitted.disconnect(on_submitted)
            self._community_api.tip_submit_error.disconnect(on_submit_error)
            self._show_toast(f"Could not submit tip: {msg}")

        self._community_api.tip_submitted.connect(on_submitted)
        self._community_api.tip_submit_error.connect(on_submit_error)
        self._community_api.submit_tip(rd.community_origin_id, tip_text)

    def _on_save_clicked(self):
        """Show save submenu (recipe vs moiety) or save directly for articles."""
        if self._article_mode:
            self._on_edit_save()
            return

        menu = QMenu(self._save_btn)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #555;
                padding: 4px 0;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a3a3a; }
            QMenu::separator { height: 1px; background: #555555; margin: 4px 8px; }
        """)
        act_recipe = menu.addAction("Save as Recipe")
        act_moiety = menu.addAction("Save as Moiety")
        menu.addSeparator()
        act_whats = menu.addAction("What's a Moiety?")

        pos = self._save_btn.mapToGlobal(self._save_btn.rect().bottomLeft())
        chosen = menu.exec(pos)

        if chosen == act_recipe:
            rd = self.recipe_detail._recipe_data
            if rd:
                rd.is_moiety = False
            self._on_edit_save()
        elif chosen == act_moiety:
            rd = self.recipe_detail._recipe_data
            if rd:
                rd.is_moiety = True
            self._on_edit_save()
        elif chosen == act_whats:
            self._show_moiety_tip_panel()

    def _on_edit_save(self):
        """Persist in-memory RecipeData to the database and exit edit mode."""
        # Flush the current step's editor state into RecipeData
        self.recipe_detail._save_current_step()

        rd = self.recipe_detail._recipe_data
        if not rd:
            return

        # Validate: require a title
        if not rd.title or not rd.title.strip():
            msg = QMessageBox(self)
            msg.setWindowTitle("Title Required")
            msg.setText("Please enter a title for the recipe before saving.")
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return

        is_new = rd.recipe_id is None
        if is_new:
            # Add mode: INSERT new recipe
            new_id = insert_recipe_data(rd)
            rd.recipe_id = new_id

            # Rename temp media folder from "new" to the actual recipe ID
            temp_dir = os.path.join(str(DATA_DIR), "media", "recipes", "new")
            final_dir = os.path.join(str(DATA_DIR), "media", "recipes", str(new_id))
            if os.path.isdir(temp_dir):
                # Move temp folder to final location
                if os.path.exists(final_dir):
                    # Merge into existing folder (shouldn't happen for new recipes)
                    for fname in os.listdir(temp_dir):
                        shutil.move(os.path.join(temp_dir, fname), final_dir)
                    os.rmdir(temp_dir)
                else:
                    shutil.move(temp_dir, final_dir)

                # Update all paths in recipe data from "new" to the actual ID
                old_prefix = "media/recipes/new/"
                new_prefix = f"media/recipes/{new_id}/"
                if rd.main_image_path and rd.main_image_path.startswith(old_prefix):
                    rd.main_image_path = rd.main_image_path.replace(old_prefix, new_prefix, 1)
                if rd.intro_video_path and rd.intro_video_path.startswith(old_prefix):
                    rd.intro_video_path = rd.intro_video_path.replace(old_prefix, new_prefix, 1)
                for step in rd.steps:
                    if step.image_path and step.image_path.startswith(old_prefix):
                        step.image_path = step.image_path.replace(old_prefix, new_prefix, 1)
                    if step.video_path and step.video_path.startswith(old_prefix):
                        step.video_path = step.video_path.replace(old_prefix, new_prefix, 1)

            # Copy default.jpg into recipe folder for any paths still referencing it
            # (articles can have no image — skip default image copy)
            if not self._article_mode:
                rd.main_image_path = self._copy_default_image(
                    rd.main_image_path, "media/recipes", new_id
                )
                for step in rd.steps:
                    step.image_path = self._copy_default_image(
                        step.image_path, "media/recipes", new_id
                    )

            # Save again to persist the updated paths
            save_recipe_data(rd)

            # Rebuild step navigator with the new recipe_id
            num_nav_steps = len(rd.steps) + 1
            self.step_navigator.load_steps(recipe_id=new_id, num_steps=num_nav_steps)
            self.step_navigator.current_step = self.recipe_detail._current_step_index
            self.step_navigator._update_active_step()
        else:
            # Edit mode: UPDATE existing recipe
            # Copy default.jpg for any new steps still referencing the shared default
            # (articles can have no image — skip default image copy)
            if not self._article_mode:
                changed = False
                new_path = self._copy_default_image(
                    rd.main_image_path, "media/recipes", rd.recipe_id
                )
                if new_path != rd.main_image_path:
                    rd.main_image_path = new_path
                    changed = True
                for step in rd.steps:
                    new_path = self._copy_default_image(
                        step.image_path, "media/recipes", rd.recipe_id
                    )
                    if new_path != step.image_path:
                        step.image_path = new_path
                        changed = True
            save_recipe_data(rd)

        # Imported images are kept — clear the pending list
        self._pending_image_files.clear()
        # Explicitly exit edit mode (don't use the toggle _on_edit_recipe)
        self.recipe_detail.set_editing(False)
        self._back_btn.setVisible(True)
        self._edit_btn.setVisible(True)  # Show edit button after saving
        self._grocery_list_btn.setVisible(True)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._edit_separator.setVisible(False)
        self._image_btn.setVisible(False)
        self._video_btn.setVisible(False)
        self._insert_step_btn.setVisible(False)
        self._append_step_btn.setVisible(False)
        self._delete_step_btn.setVisible(False)
        self._paste_clipboard_btn.setVisible(False)
        self._moiety_btn.setVisible(False)
        self.step_navigator.set_drag_enabled(False)
        self._update_tips_buttons_visibility()
        self._apply_article_mode()
        self._moiety_tip_panel.hide()
        self._moiety_panel.hide()
        # Re-enable mic button and defer mic restore so the UI repaints first
        if hasattr(self, "_mic_toggle") and self._mic_toggle:
            self._mic_toggle.setEnabled(True)
        self._deferred_mic_restore()
        self._autohide_timer.start(self._autohide_timeout_ms)

        # New recipe: navigate to library so user can see what they created
        if is_new:
            self.show_recipe_list()

    def _on_edit_cancel(self):
        """Discard all edits, reload from DB, and exit edit mode."""
        self._article_tip_panel.hide()
        self._moiety_tip_panel.hide()
        self._moiety_panel.hide()
        # Release any video the media player may be holding open
        self.video_player.media_player.stop()
        self.video_player.media_player.setSource(QUrl())
        # Remove any media files imported during this edit session
        for path in self._pending_image_files:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except PermissionError:
                pass
        self._pending_image_files.clear()

        rd = self.recipe_detail._recipe_data

        # Add mode: return to previous view without saving
        if rd is None or rd.recipe_id is None:
            # Clean up the temp "new" media folder if it exists
            temp_dir = os.path.join(str(DATA_DIR), "media", "recipes", "new")
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir)
            self.recipe_detail.set_editing(False)
            self._return_to_home()
            return

        # Edit mode: reload the original recipe data from the database
        original = load_recipe_data(rd.recipe_id)
        if original:
            # Clamp step index to valid range after reload
            max_idx = len(original.steps)  # 0=intro, 1..N=steps
            step_idx = min(self.recipe_detail._current_step_index, max_idx)
            self.recipe_detail.load_recipe(original)
            # Rebuild navigator to match restored step count
            num_nav_steps = len(original.steps) + 1
            self.step_navigator.load_steps(
                recipe_id=original.recipe_id, num_steps=num_nav_steps
            )
            self.recipe_detail.load_step(step_idx)
            self.step_navigator.current_step = step_idx
            self.step_navigator._update_active_step()
            self._update_play_video_state(step_idx)

        # Explicitly exit edit mode (don't use the toggle _on_edit_recipe)
        self.recipe_detail.set_editing(False)
        self._back_btn.setVisible(True)
        self._edit_btn.setVisible(True)
        self._grocery_list_btn.setVisible(True)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._edit_separator.setVisible(False)
        self._image_btn.setVisible(False)
        self._video_btn.setVisible(False)
        self._insert_step_btn.setVisible(False)
        self._append_step_btn.setVisible(False)
        self._delete_step_btn.setVisible(False)
        self._paste_clipboard_btn.setVisible(False)
        self._moiety_btn.setVisible(False)
        self.step_navigator.set_drag_enabled(False)
        self._update_tips_buttons_visibility()
        self._apply_article_mode()
        self._moiety_tip_panel.hide()
        self._moiety_panel.hide()
        # Re-enable mic button and defer mic restore so the UI repaints first
        if hasattr(self, "_mic_toggle") and self._mic_toggle:
            self._mic_toggle.setEnabled(True)
        self._deferred_mic_restore()
        self._autohide_timer.start(self._autohide_timeout_ms)

    def _on_insert_step(self):
        """Insert a new step after the current step."""
        rd = self.recipe_detail._recipe_data
        if not rd or not self.recipe_detail._editing:
            return

        # Flush current edits before modifying the steps list
        self.recipe_detail._save_current_step()

        # Determine insert position in rd.steps (0-based)
        current_idx = self.recipe_detail._current_step_index
        if current_idx == 0:
            insert_pos = 0  # After intro = before step 1
        else:
            insert_pos = current_idx  # After step N = index N in rd.steps

        new_step = StepData(
            step_id=None,
            step_number=insert_pos + 1,
            instruction="",
            image_path="media/default.jpg",
        )
        rd.steps.insert(insert_pos, new_step)

        # Renumber all steps
        for i, step in enumerate(rd.steps, 1):
            step.step_number = i

        # Rebuild navigator and navigate to the new step
        new_nav_idx = insert_pos + 1  # +1 for intro offset
        num_nav_steps = len(rd.steps) + 1
        self.step_navigator.load_steps(
            recipe_id=rd.recipe_id, num_steps=num_nav_steps
        )
        self.step_navigator.current_step = new_nav_idx
        self.step_navigator._update_active_step()
        # Use _populate_step directly to avoid load_step's auto-save
        # (rd.steps was just modified, so a second save would corrupt data)
        self.recipe_detail._current_step_index = new_nav_idx
        self.recipe_detail._populate_step(new_nav_idx)
        self.step_navigator.animate_button_insert(new_nav_idx)
        self.step_navigator.scroll_to_step(new_nav_idx)
        self._update_play_video_state(new_nav_idx)
        self.recipe_detail.directions_editor.set_step_count(len(rd.steps) + 1)

    def _on_append_step(self):
        """Append a new step at the end of the recipe."""
        rd = self.recipe_detail._recipe_data
        if not rd or not self.recipe_detail._editing:
            return

        # Flush current edits before modifying the steps list
        self.recipe_detail._save_current_step()

        new_step = StepData(
            step_id=None,
            step_number=len(rd.steps) + 1,
            instruction="",
            image_path="media/default.jpg",
        )
        rd.steps.append(new_step)

        # Rebuild navigator and navigate to the new step
        new_nav_idx = len(rd.steps)  # Last step (1-based nav index)
        num_nav_steps = len(rd.steps) + 1
        self.step_navigator.load_steps(
            recipe_id=rd.recipe_id, num_steps=num_nav_steps
        )
        self.step_navigator.current_step = new_nav_idx
        self.step_navigator._update_active_step()
        self.recipe_detail._current_step_index = new_nav_idx
        self.recipe_detail._populate_step(new_nav_idx)
        self.step_navigator.animate_button_insert(new_nav_idx)
        self.step_navigator.scroll_to_step(new_nav_idx)
        self._update_play_video_state(new_nav_idx)
        self.recipe_detail.directions_editor.set_step_count(len(rd.steps) + 1)

    def _on_paste_from_clipboard(self):
        """Insert clipboard steps after the current step."""
        rd = self.recipe_detail._recipe_data
        if not rd or not self.recipe_detail._editing or not self._clipboard_data:
            return

        clip_steps = self._clipboard_data.steps
        if not clip_steps:
            return

        # Flush current edits before modifying the steps list
        self.recipe_detail._save_current_step()

        # Determine insert position (same logic as _on_insert_step)
        current_idx = self.recipe_detail._current_step_index
        if current_idx == 0:
            insert_pos = 0
        else:
            insert_pos = current_idx

        # Deep-copy clipboard steps and insert them
        for i, step in enumerate(clip_steps):
            s = copy.deepcopy(step)
            s.step_id = None
            for ing in s.ingredients:
                ing.ingredient_id = None
            rd.steps.insert(insert_pos + i, s)

        # Renumber all steps
        for i, step in enumerate(rd.steps, 1):
            step.step_number = i

        # Rebuild navigator and navigate to the first inserted step
        first_nav_idx = insert_pos + 1  # +1 for intro offset
        num_nav_steps = len(rd.steps) + 1
        self.step_navigator.load_steps(
            recipe_id=rd.recipe_id, num_steps=num_nav_steps
        )
        self.step_navigator.current_step = first_nav_idx
        self.step_navigator._update_active_step()
        self.recipe_detail._current_step_index = first_nav_idx
        self.recipe_detail._populate_step(first_nav_idx)
        self.step_navigator.animate_button_insert(first_nav_idx)
        self.step_navigator.scroll_to_step(first_nav_idx)
        self._update_play_video_state(first_nav_idx)

        n = len(clip_steps)
        self._show_toast(f"{n} step{'s' if n != 1 else ''} inserted from clipboard")
        self.recipe_detail.directions_editor.set_step_count(len(rd.steps) + 1)

    def _on_moiety_insert(self, moiety_recipe_id):
        """Load a moiety recipe and insert its steps into the current recipe."""
        rd = self.recipe_detail._recipe_data
        if not rd or not self.recipe_detail._editing:
            return

        moiety_rd = load_recipe_data(moiety_recipe_id)
        if not moiety_rd or not moiety_rd.steps:
            self._show_toast("Moiety has no steps to insert")
            return

        # Flush current edits before modifying the steps list
        self.recipe_detail._save_current_step()

        # Determine insert position (same logic as _on_paste_from_clipboard)
        current_idx = self.recipe_detail._current_step_index
        insert_pos = 0 if current_idx == 0 else current_idx

        # Deep-copy moiety steps and insert them
        inserted_nav_indices = []
        for i, step in enumerate(moiety_rd.steps):
            s = copy.deepcopy(step)
            s.step_id = None
            for ing in s.ingredients:
                ing.ingredient_id = None
            rd.steps.insert(insert_pos + i, s)
            inserted_nav_indices.append(insert_pos + i + 1)  # +1 for intro offset

        # Renumber all steps
        for i, step in enumerate(rd.steps, 1):
            step.step_number = i

        # Rebuild navigator and navigate to the first inserted step
        first_nav_idx = insert_pos + 1
        num_nav_steps = len(rd.steps) + 1
        self.step_navigator.load_steps(
            recipe_id=rd.recipe_id, num_steps=num_nav_steps
        )
        self.step_navigator.current_step = first_nav_idx
        self.step_navigator._update_active_step()
        self.recipe_detail._current_step_index = first_nav_idx
        self.recipe_detail._populate_step(first_nav_idx)

        # Blue highlight fade on all inserted step buttons
        self.step_navigator.animate_highlight_fade(inserted_nav_indices)
        self.step_navigator.scroll_to_step(first_nav_idx)
        self._update_play_video_state(first_nav_idx)

        n = len(moiety_rd.steps)
        self._show_toast(
            f"{n} step{'s' if n != 1 else ''} inserted from {moiety_rd.title}"
        )

        # Close the moiety panel after insert
        self._moiety_panel.hide()
        self.recipe_detail.directions_editor.set_step_count(len(rd.steps) + 1)

    def _on_moiety_preview(self, moiety_recipe_id):
        """Show a moiety's steps in preview-only clipboard mode."""
        moiety_rd = load_recipe_data(moiety_recipe_id)
        if not moiety_rd or not moiety_rd.steps:
            self._show_toast("Moiety has no steps to preview")
            return

        # Build a preview RecipeData from the moiety's steps
        preview_data = build_clipboard_recipe(moiety_rd.steps)
        preview_data.title = moiety_rd.title
        preview_data.description = moiety_rd.description or f"{len(moiety_rd.steps)} step(s)"

        self._moiety_panel.hide()
        self._show_clipboard(preview_only=True, preview_data=preview_data)

    def _on_delete_steps(self):
        """Delete the current step or all blue-selected steps."""
        rd = self.recipe_detail._recipe_data
        if not rd or not self.recipe_detail._editing:
            return

        # Determine which step indices to delete (1-based nav indices)
        selected = self.step_navigator.selected_steps
        if selected:
            # Blue-selected range — exclude intro (0) just in case
            steps_to_delete = sorted(s for s in selected if s > 0)
        else:
            # Single current step — skip if on intro
            current = self.recipe_detail._current_step_index
            if current == 0:
                return
            steps_to_delete = [current]

        if not steps_to_delete:
            return

        # Animate the buttons out, then perform the actual data removal
        self.step_navigator.animate_button_delete(
            steps_to_delete, lambda: self._finish_delete_steps(steps_to_delete)
        )

    def _finish_delete_steps(self, steps_to_delete):
        """Complete step deletion after the button animation finishes."""
        rd = self.recipe_detail._recipe_data
        if not rd:
            return

        # Convert nav indices to rd.steps list indices (0-based) and remove
        # Reverse order so removals don't shift earlier indices
        for nav_idx in reversed(steps_to_delete):
            data_idx = nav_idx - 1
            if 0 <= data_idx < len(rd.steps):
                step = rd.steps.pop(data_idx)
                self._remove_old_media(step.image_path)
                self._remove_old_media(step.video_path)

        # Renumber remaining steps
        for i, step in enumerate(rd.steps, 1):
            step.step_number = i

        # Button removal and renumbering already handled by animate_button_delete
        self.step_navigator.selected_steps.clear()

        # Navigate to intro step
        self.recipe_detail._current_step_index = 0
        self.recipe_detail._populate_step(0)
        self.step_navigator.current_step = 0
        self.step_navigator._update_active_step()
        self._update_play_video_state(0)
        self.recipe_detail.directions_editor.set_step_count(len(rd.steps) + 1)

    def _on_step_moved(self, from_step, to_step):
        """Handle drag-and-drop reorder: move a step from one position to another."""
        rd = self.recipe_detail._recipe_data
        if not rd or not self.recipe_detail._editing:
            return

        # Flush current edits before reordering
        self.recipe_detail._save_current_step()

        from_idx = from_step - 1  # Convert 1-based step index to rd.steps index
        to_idx = to_step - 1

        if from_idx < 0 or from_idx >= len(rd.steps):
            return

        # Pop and re-insert (_finish_drag already adjusted to_step for removal)
        step = rd.steps.pop(from_idx)
        to_idx = max(0, min(to_idx, len(rd.steps)))
        rd.steps.insert(to_idx, step)

        # Renumber all steps sequentially
        for i, s in enumerate(rd.steps, 1):
            s.step_number = i

        # Animate the button sliding to its new position
        new_nav_idx = to_idx + 1  # +1 for intro offset
        self.step_navigator.animate_step_move(from_step, to_step)
        self.step_navigator.current_step = new_nav_idx
        self.step_navigator._update_active_step()
        self.recipe_detail._current_step_index = new_nav_idx
        self.recipe_detail._populate_step(new_nav_idx)
        self.step_navigator.scroll_to_step(new_nav_idx)
        self._update_play_video_state(new_nav_idx)

    def _on_image_btn_clicked(self):
        """Image button handler — context menu in article mode, direct import otherwise."""
        if self._article_mode:
            rd = self.recipe_detail._recipe_data
            step_idx = self.recipe_detail._current_step_index
            has_image = False
            if rd:
                if step_idx == 0:
                    has_image = bool(rd.main_image_path)
                elif 1 <= step_idx <= len(rd.steps):
                    has_image = bool(rd.steps[step_idx - 1].image_path)
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: #2a2a2a;
                    color: white;
                    border: 1px solid #555;
                    padding: 4px 0;
                }
                QMenu::item { padding: 6px 20px; }
                QMenu::item:selected { background-color: #3a3a3a; }
                QMenu::item:disabled { color: #666; }
            """)
            import_action = menu.addAction("Import Image")
            remove_action = menu.addAction("Remove Image")
            remove_action.setEnabled(has_image)
            action = menu.exec(self._image_btn.mapToGlobal(
                self._image_btn.rect().bottomLeft()
            ))
            if action == import_action:
                self._on_import_step_image()
            elif action == remove_action:
                self._on_remove_step_image()
        else:
            self._on_import_step_image()

    def _on_remove_step_image(self):
        """Remove the image from the current step (article mode)."""
        rd = self.recipe_detail._recipe_data
        if not rd:
            return
        step_idx = self.recipe_detail._current_step_index
        if step_idx == 0:
            self._remove_old_media(rd.main_image_path)
            rd.main_image_path = None
        elif 1 <= step_idx <= len(rd.steps):
            self._remove_old_media(rd.steps[step_idx - 1].image_path)
            rd.steps[step_idx - 1].image_path = None
        rd.dirty = True
        self.recipe_detail.load_step_image(None)

    def _on_import_step_image(self):
        """Open a file dialog to import an image for the current step."""
        if not self.recipe_detail._editing:
            return
        rd = self.recipe_detail._recipe_data
        if not rd:
            return

        # Use "new" as placeholder for unsaved recipes
        folder_id = rd.recipe_id if rd.recipe_id is not None else "new"

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Step Image",
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tiff)",
        )
        if not file_path:
            return

        # Validate 16:9 aspect ratio (±2% tolerance)
        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            msg = QMessageBox(self)
            msg.setWindowTitle("Invalid Image")
            msg.setText("The selected file could not be loaded as an image.")
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return
        ratio = pixmap.width() / pixmap.height()
        target_ratio = 16 / 9
        if abs(ratio - target_ratio) / target_ratio > 0.02:
            msg = QMessageBox(self)
            msg.setWindowTitle("Wrong Aspect Ratio")
            msg.setText(
                f"The image must be 16:9 aspect ratio.\n\n"
                f"Selected image is {pixmap.width()}×{pixmap.height()} "
                f"({ratio:.3f}), expected {target_ratio:.3f}."
            )
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return

        # Remove the old image file for this step
        step_idx = self.recipe_detail._current_step_index
        if step_idx == 0:
            self._remove_old_media(rd.main_image_path)
        elif 1 <= step_idx <= len(rd.steps):
            self._remove_old_media(rd.steps[step_idx - 1].image_path)

        # Downscale if larger than 1920x1080 and save as JPEG
        max_w, max_h = 1920, 1080
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(
                max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

        dest_dir = os.path.join(
            str(DATA_DIR), "media", "recipes", str(folder_id)
        )
        os.makedirs(dest_dir, exist_ok=True)
        new_name = f"{uuid.uuid4().hex}.jpg"
        dest_path = os.path.join(dest_dir, new_name)
        pixmap.save(dest_path, "JPEG", 85)

        # Track for cancel cleanup
        self._pending_image_files.append(dest_path)

        # Update in-memory data
        rel_path = f"media/recipes/{folder_id}/{new_name}"
        if step_idx == 0:
            rd.main_image_path = rel_path
        elif step_idx >= 1 and step_idx <= len(rd.steps):
            rd.steps[step_idx - 1].image_path = rel_path
        rd.dirty = True

        # Refresh the displayed image
        self.recipe_detail.load_step_image(rel_path)

    # Supported video containers — H.264, HEVC, and VP9 all have
    # hardware decode on M1+ Macs and modern Windows GPUs.
    # AV1 is excluded: PySide6's bundled FFmpeg lacks a software AV1
    # decoder (no dav1d/libaom), so playback fails on pre-M3 Macs.
    _EXT_TO_FORMAT = {
        ".mp4": QMediaFormat.FileFormat.MPEG4,
        ".m4v": QMediaFormat.FileFormat.MPEG4,
        ".mov": QMediaFormat.FileFormat.QuickTime,
        ".mkv": QMediaFormat.FileFormat.Matroska,
        ".avi": QMediaFormat.FileFormat.AVI,
    }

    def _is_video_format_supported(self, file_path: str) -> bool:
        """Check if the video file's container format is supported for playback."""
        ext = os.path.splitext(file_path)[1].lower()
        fmt = self._EXT_TO_FORMAT.get(ext)
        if fmt is None:
            return False
        supported = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
        return fmt in supported

    _DEFAULT_IMAGE = "media/default.jpg"

    def _copy_default_image(self, image_path, media_subdir, folder_id):
        """If image_path is the shared default, copy it into the item's own folder.

        Returns the new relative path, or the original path if no copy was needed.
        """
        if image_path != self._DEFAULT_IMAGE:
            return image_path
        src = os.path.join(str(DATA_DIR), self._DEFAULT_IMAGE)
        if not os.path.isfile(src):
            return image_path
        dest_dir = os.path.join(
            str(DATA_DIR), media_subdir, str(folder_id)
        )
        os.makedirs(dest_dir, exist_ok=True)
        new_name = f"{uuid.uuid4().hex}.jpg"
        shutil.copy2(src, os.path.join(dest_dir, new_name))
        return f"{media_subdir}/{folder_id}/{new_name}"

    def _remove_old_media(self, rel_path):
        """Delete an old media file from disk if it's inside a recipe/book folder.

        Skips shared assets like media/default.jpg. Silently ignores missing files.
        """
        if not rel_path or rel_path == self._DEFAULT_IMAGE:
            return
        # Only delete files inside media/recipes/<id>/ or media/books/<id>/
        if not (rel_path.startswith("media/recipes/") or rel_path.startswith("media/books/")):
            return
        abs_path = os.path.join(str(DATA_DIR), rel_path)
        if abs_path in self._pending_image_files:
            self._pending_image_files.remove(abs_path)
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
        except OSError:
            pass

    def _on_import_step_video(self):
        """Open a file dialog to import a video for the current step."""
        if not self.recipe_detail._editing:
            return
        rd = self.recipe_detail._recipe_data
        if not rd:
            return

        # Use "new" as placeholder for unsaved recipes
        folder_id = rd.recipe_id if rd.recipe_id is not None else "new"

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Step Video",
            "",
            "Videos (*.mp4 *.m4v *.mov *.mkv *.avi)",
        )
        if not file_path:
            return

        # Remove the old video file for this step
        step_idx = self.recipe_detail._current_step_index
        if step_idx == 0:
            self._remove_old_media(rd.intro_video_path)
        elif 1 <= step_idx <= len(rd.steps):
            self._remove_old_media(rd.steps[step_idx - 1].video_path)

        # Prepare destination
        dest_dir = os.path.join(
            str(DATA_DIR), "media", "recipes", str(folder_id)
        )
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(file_path)[1].lower() or ".mp4"
        new_name = f"{uuid.uuid4().hex}{ext}"
        dest_path = os.path.join(dest_dir, new_name)
        rel_path = f"media/recipes/{folder_id}/{new_name}"

        # Warn if video is above 1080p
        from utils.video_compress import is_above_1080p
        is_above, resolution = is_above_1080p(file_path)
        if is_above:
            w, h = resolution
            if not self._warn_high_res_video(w, h):
                return

        # Copy to media folder
        shutil.copy2(file_path, dest_path)
        self._pending_image_files.append(dest_path)

        # Update in-memory data
        if step_idx == 0:
            rd.intro_video_path = rel_path
        elif step_idx >= 1 and step_idx <= len(rd.steps):
            rd.steps[step_idx - 1].video_path = rel_path
        rd.dirty = True
        self._update_play_video_state(step_idx)

    def _on_video_btn_clicked(self):
        """Show a menu with Import / Remove video options."""
        rd = self.recipe_detail._recipe_data
        if not rd:
            return
        step_idx = self.recipe_detail._current_step_index
        if step_idx == 0:
            has_video = bool(rd.intro_video_path)
        elif 1 <= step_idx <= len(rd.steps):
            has_video = bool(rd.steps[step_idx - 1].video_path)
        else:
            has_video = False

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #555;
                padding: 4px 0;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #3a3a3a; }
            QMenu::item:disabled { color: #666; }
        """)
        import_action = menu.addAction("Import Video")
        remove_action = menu.addAction("Remove Video")
        remove_action.setEnabled(has_video)

        action = menu.exec(self._video_btn.mapToGlobal(
            self._video_btn.rect().bottomLeft()
        ))
        if action == import_action:
            self._on_import_step_video()
        elif action == remove_action:
            self._on_remove_step_video()

    def _on_remove_step_video(self):
        """Remove the video from the current step."""
        rd = self.recipe_detail._recipe_data
        if not rd:
            return
        step_idx = self.recipe_detail._current_step_index
        if step_idx == 0:
            self._remove_old_media(rd.intro_video_path)
            rd.intro_video_path = None
        elif 1 <= step_idx <= len(rd.steps):
            step = rd.steps[step_idx - 1]
            self._remove_old_media(step.video_path)
            step.video_path = None
        else:
            return
        rd.dirty = True
        self._update_play_video_state(step_idx)
        self._show_toast("Video removed")

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    def _show_toast(self, message, duration_ms=5000):
        """Show a brief toast notification that fades out.

        Args:
            message: Text to display.
            duration_ms: How long to show before fade-out (default 1500ms).
        """
        self._toast_label.setText(message)
        self._toast_label.adjustSize()
        # Center horizontally, position below command bar
        cw = self.centralWidget()
        cb_h = self.command_bar.sizeHint().height()
        tx = (cw.width() - self._toast_label.width()) // 2
        ty = cb_h + 12
        self._toast_label.move(tx, ty)
        self._toast_opacity.setOpacity(1.0)
        self._toast_label.show()
        self._toast_label.raise_()
        # Stop any running fade
        if self._fade_anim_toast is not None:
            self._fade_anim_toast.stop()
            self._fade_anim_toast = None
        self._toast_timer.start(duration_ms)

    def _fade_out_toast(self):
        """Fade out the toast notification."""
        anim = QPropertyAnimation(self._toast_opacity, b"opacity")
        anim.setDuration(400)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(self._toast_label.hide)
        self._fade_anim_toast = anim
        anim.start()

    def _copy_to_clipboard(self, steps, origin_recipe=None):
        """Replace clipboard with deep copies of the given steps."""
        self._clipboard_data = build_clipboard_recipe(steps)
        # Propagate community origin so create-recipe-from-clipboard preserves it
        if origin_recipe:
            self._clipboard_data.community_origin_id = origin_recipe.community_origin_id
            self._clipboard_data.community_origin_uploader = origin_recipe.community_origin_uploader
        save_clipboard(self._clipboard_data)
        self._update_clipboard_button_states()
        n = len(steps)
        self._show_toast(f"{n} step{'s' if n != 1 else ''} copied to clipboard")

    def _on_copy_selected_steps(self):
        """Copy steps selected in step navigator to clipboard.

        Navigator indices 1+ map to rd.steps[idx - 1] since index 0 is the
        virtual intro step (excluded from selection by the navigator).
        """
        rd = self.recipe_detail._recipe_data
        if not rd:
            return
        selected = sorted(self.step_navigator.selected_steps)
        steps_to_copy = []
        for idx in selected:
            data_idx = idx - 1  # Navigator index 1 → rd.steps[0], etc.
            if 0 <= data_idx < len(rd.steps):
                steps_to_copy.append(rd.steps[data_idx])
        if steps_to_copy:
            self._copy_to_clipboard(steps_to_copy, origin_recipe=rd)

    def _on_copy_recipe_from_list(self, recipe_id):
        """Copy all steps from a recipe (by ID) to clipboard."""
        recipe_data = load_recipe_data(recipe_id)
        if recipe_data and recipe_data.steps:
            self._copy_to_clipboard(recipe_data.steps, origin_recipe=recipe_data)

    # ── Grocery List ────────────────────────────────────────────────────

    def _on_add_ingredients_to_grocery_list(self):
        """Add the current recipe's ingredients to the grocery list."""
        from utils.database import add_grocery_item

        ingredients = self.recipe_detail.ingredients_editor.get_ingredients()
        if not ingredients:
            self._show_toast("No ingredients to add")
            return

        count = 0
        for ing in ingredients:
            qty = ing.get("quantity", "").strip()
            unit = ing.get("unit", "").strip()
            name = ing.get("item_name", "").strip()
            if not name:
                continue
            parts = []
            if qty:
                parts.append(qty)
            if unit:
                parts.append(unit)
            parts.append(name)
            add_grocery_item(" ".join(parts))
            count += 1

        self._show_toast(f"Added {count} item{'s' if count != 1 else ''} to grocery list")

    def _on_show_grocery_list_from_recipe_list(self):
        if self._community_mode and self.stacked_widget.currentWidget() is self.community_home:
            self._grocery_list_source = "community_home"
        else:
            self._grocery_list_source = "recipe_list"
        self.show_grocery_list()

    def _on_show_grocery_list_from_detail(self):
        rd = self.recipe_detail._recipe_data
        recipe_id = rd.recipe_id if rd else None
        self._grocery_list_source = ("recipe_detail", recipe_id)
        self.show_grocery_list()

    def show_grocery_list(self):
        """Switch to grocery list view and configure command bar."""
        self._mic_saved_for_nav = self._is_voice_listening
        if self._is_voice_listening:
            self._voice.stop_listening()
        self._autohide_timer.stop()
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.grocery_list_view)
        self._cb_opacity.setOpacity(1.0)
        self.step_navigator.hide()
        self._info_panel.hide()
        self.video_play_overlay.hide()
        self._step_indicator.hide()
        self._configure_grocery_list_commands()
        self.grocery_list_view.set_command_bar(self.command_bar)
        self.grocery_list_view.load_items()
        self._position_overlays()

    def _configure_grocery_list_commands(self):
        """Configure command bar buttons for grocery list view."""
        self.play_video_toggle = None
        self.command_bar.clear()
        icon_btn_style = """
            QPushButton {
                min-width: 32px; max-width: 32px;
                min-height: 32px; max-height: 32px;
                padding: 0px; font-size: 20px;
            }
        """
        back_btn = self.command_bar.add_button(
            "", self._on_grocery_list_back, tooltip="Back",
            icon=platform_icon("arrow.left", weight="regular", point_size=48,
                               color="white", windows_name="ArrowLeft8")
        )
        back_btn.setFixedSize(32, 32)
        back_btn.setStyleSheet(icon_btn_style)
        self.command_bar.add_stretch()
        self.command_bar.add_button(
            "Send to Phone", self._on_send_grocery_list_to_phone,
        )
        self.command_bar.add_button(
            "Clear List", self._on_clear_grocery_list,
        )

    def _on_grocery_list_back(self):
        """Navigate back from grocery list to the previous view."""
        saved_mic = self._mic_saved_for_nav
        self._mic_saved_for_nav = None
        if isinstance(self._grocery_list_source, tuple):
            _, recipe_id = self._grocery_list_source
            self.show_recipe_detail(recipe_id)
            if saved_mic and not self._is_voice_listening and not self.recipe_detail._editing:
                self._voice.start_listening()
        elif self._grocery_list_source == "community_home":
            self._return_to_home()
        else:
            self.show_recipe_list()

    def _on_send_grocery_list_to_phone(self):
        """Send the grocery list to the user's phone via Pushover."""
        if getattr(self, "_pushover_sending", False):
            return
        from utils.pushover import send_pushover_message
        from widgets.pushover_setup_dialog import PushoverSetupDialog

        items = self.grocery_list_view.get_all_text()
        if not items:
            self._show_toast("Grocery list is empty")
            return

        settings = QSettings(str(SETTINGS_PATH), QSettings.IniFormat)
        token = settings.value("pushover_api_token", "", type=str)
        user = settings.value("pushover_user_key", "", type=str)

        if not token or not user:
            dlg = PushoverSetupDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            token, user = dlg.get_credentials()
            settings.setValue("pushover_api_token", token)
            settings.setValue("pushover_user_key", user)

        body = "\n\n".join(f"• {item}" for item in items)
        title = "Grocery List"

        self._pushover_sending = True
        worker = _IOWorker(send_pushover_message, token, user, body, title)
        worker.finished.connect(self._on_pushover_result)
        worker.error.connect(self._on_pushover_error)
        self._io_worker = worker
        worker.start()

    def _on_clear_grocery_list(self):
        """Clear all items from the grocery list after confirmation."""
        count = self.grocery_list_view.item_count
        if count == 0:
            self._show_toast("List is already empty")
            return
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Clear Grocery List")
        msg_box.setText(
            f"Remove all {count} item{'s' if count != 1 else ''} from the grocery list?"
        )
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setStyleSheet(DIALOG_STYLE)
        if msg_box.exec() == QMessageBox.Yes:
            self.grocery_list_view.clear_all()
            self._show_toast("Grocery list cleared")

    # ── Pushover result handlers ──────────────────────────────────────

    def _on_pushover_result(self, result):
        self._pushover_sending = False
        ok, detail, is_client_error = result
        if ok:
            self._show_toast("Sent to phone")
        elif is_client_error:
            self._show_toast(f"Send failed: {detail}")
            self._prompt_pushover_reconfigure()
        else:
            self._show_toast(f"Send failed: {detail}")

    def _on_pushover_error(self, msg):
        self._pushover_sending = False
        self._show_toast(f"Send failed: {msg}")

    def _prompt_pushover_reconfigure(self):
        """Offer to reopen the Pushover setup dialog after a send failure."""
        from widgets.pushover_setup_dialog import PushoverSetupDialog

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Pushover")
        msg_box.setText(
            "Would you like to update your Pushover credentials?"
        )
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setStyleSheet(DIALOG_STYLE)
        if msg_box.exec() != QMessageBox.Yes:
            return
        settings = QSettings(str(SETTINGS_PATH), QSettings.IniFormat)
        dlg = PushoverSetupDialog(self)
        if dlg.exec() == QDialog.Accepted:
            token, user = dlg.get_credentials()
            settings.setValue("pushover_api_token", token)
            settings.setValue("pushover_user_key", user)

    def _on_delete_recipe_from_list(self, recipe_id, title):
        """Show confirmation dialog and delete recipe if confirmed."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Recipe")
        msg_box.setText(
            f"Are you sure you want to delete \"{title}\"?\n\n"
            "This action cannot be undone."
        )
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setStyleSheet(DIALOG_STYLE)
        yes_btn = msg_box.button(QMessageBox.Yes)
        yes_btn.setStyleSheet("background-color: #444; color: white; border: 1px solid #555555; border-radius: 4px; padding: 8px 18px; min-width: 80px; font-size: 14px;")
        no_btn = msg_box.button(QMessageBox.No)
        no_btn.setStyleSheet("background-color: #0078d4; color: white; border: 1px solid #0078d4; border-radius: 4px; padding: 8px 18px; min-width: 80px; font-size: 14px;")
        reply = msg_box.exec()
        if reply == QMessageBox.Yes:
            delete_recipe(recipe_id)

            media_dir = os.path.join(
                str(DATA_DIR), "media", "recipes", str(recipe_id)
            )
            if os.path.isdir(media_dir):
                shutil.rmtree(media_dir)

            self.recipe_list.filter_recipes()
            self.recipe_list.refresh_total_count()

    def _on_delete_book_from_list(self, book_id, title):
        """Show confirmation dialog and delete book if confirmed.

        In the owned-copies model, deleting a book always deletes all its
        recipe copies and their media folders.
        """
        # Count recipes in the book
        bd = load_book_data(book_id)
        recipe_count = sum(len(cat.recipes) for cat in bd.categories) if bd else 0

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Book")
        if recipe_count > 0:
            msg_box.setText(
                f"Delete \"{title}\"?\n\n"
                f"This will also delete all {recipe_count} recipe(s) "
                "in this book. This action cannot be undone."
            )
        else:
            msg_box.setText(f"Delete \"{title}\"?")
        msg_box.setStyleSheet(DIALOG_STYLE)
        delete_btn = msg_box.addButton("Delete", QMessageBox.DestructiveRole)
        delete_btn.setStyleSheet("background-color: #444; color: white; border: 1px solid #555555; border-radius: 4px; padding: 8px 18px; min-width: 80px; font-size: 14px;")
        cancel_btn = msg_box.addButton(QMessageBox.Cancel)
        cancel_btn.setStyleSheet("background-color: #0078d4; color: white; border: 1px solid #0078d4; border-radius: 4px; padding: 8px 18px; min-width: 80px; font-size: 14px;")
        msg_box.setDefaultButton(cancel_btn)
        msg_box.exec()
        if msg_box.clickedButton() == delete_btn:
            delete_book(book_id)
            self.recipe_list.filter_recipes()
            self.recipe_list.refresh_total_count()

    def _on_export_recipe(self, recipe_id):
        """Export a recipe to a zip file via save dialog."""
        rd = load_recipe_data(recipe_id)
        if not rd:
            return
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in rd.title
        ).strip()
        suggested = f"{safe_title}.fmr" if safe_title else "recipe.fmr"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "Export Recipe", suggested, "Foodie Moiety Recipe (*.fmr)"
        )
        if not zip_path:
            return

        title = rd.title
        progress = self._create_styled_progress(f"Exporting '{title}'...", "Export")

        worker = _IOWorker(export_recipe_to_zip, recipe_id, zip_path)
        worker.finished.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.finished.connect(lambda _: self._show_toast(f"Exported '{title}'"))
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_styled_warning("Export Failed", f"Could not export recipe:\n\n{msg}")
        )
        self._io_worker = worker  # prevent garbage collection
        worker.start()

    def _on_export_book(self):
        """Export the current book to a zip file via save dialog."""
        data = self._current_book_data
        if not data or data.book_id is None:
            self._show_toast("Save the book before exporting")
            return
        if data.community_origin_id and data.community_price_type == "paid":
            self._show_toast("Purchased books cannot be exported")
            return
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in data.title
        ).strip()
        suggested = f"{safe_title}.fmb" if safe_title else "book.fmb"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "Export Book", suggested, "Foodie Moiety Book (*.fmb)"
        )
        if not zip_path:
            return

        title = data.title
        progress = self._create_styled_progress(f"Exporting '{title}'...", "Export")

        worker = _IOWorker(export_book_to_zip, data.book_id, zip_path)
        worker.finished.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.finished.connect(lambda _: self._show_toast(f"Exported '{title}'"))
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_styled_warning("Export Failed", f"Could not export book:\n\n{msg}")
        )
        self._io_worker = worker
        worker.start()

    def _on_export_book_by_id(self, book_id):
        """Export a book by ID (from recipe list card export button)."""
        bd = load_book_data(book_id)
        if not bd:
            return
        if bd.community_origin_id and bd.community_price_type == "paid":
            self._show_toast("Purchased books cannot be exported")
            return
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in bd.title
        ).strip()
        suggested = f"{safe_title}.fmb" if safe_title else "book.fmb"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "Export Book", suggested, "Foodie Moiety Book (*.fmb)"
        )
        if not zip_path:
            return

        title = bd.title
        progress = self._create_styled_progress(f"Exporting '{title}'...", "Export")

        worker = _IOWorker(export_book_to_zip, book_id, zip_path)
        worker.finished.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.finished.connect(lambda _: self._show_toast(f"Exported '{title}'"))
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_styled_warning("Export Failed", f"Could not export book:\n\n{msg}")
        )
        self._io_worker = worker
        worker.start()

    def _on_import_recipe(self):
        """Import a recipe or book from a zip file via open dialog.

        Auto-detects whether the zip contains a book (book.json) or a recipe
        (recipe.json) and routes to the appropriate import flow.
        """
        zip_path, _ = QFileDialog.getOpenFileName(
            self, "Import", "",
            "Foodie Moiety Files (*.fmr *.fmb *.zip)"
        )
        if not zip_path:
            return

        # Auto-detect: book zip or recipe zip?
        try:
            book_info = peek_book_zip(zip_path)
            self._import_book(zip_path, book_info)
            return
        except ValueError:
            pass  # Not a book zip — try recipe

        # Check for duplicate before importing
        try:
            info = peek_recipe_zip(zip_path)
        except (ValueError, Exception) as e:
            self._show_styled_warning("Import Failed", f"Could not read zip:\n\n{e}")
            return

        existing = find_recipe_by_title_producer(info["title"], info["producer"])
        if existing:
            if info["producer"]:
                desc = f"There is already a \"{info['title']}\" recipe by {info['producer']}."
            else:
                desc = f"A recipe called \"{info['title']}\" already exists."
            msg = QMessageBox(self)
            msg.setWindowTitle("Duplicate Recipe")
            msg.setText(desc)
            replace_btn = msg.addButton("Replace", QMessageBox.DestructiveRole)
            keep_btn = msg.addButton("Keep Both", QMessageBox.AcceptRole)
            msg.addButton("Cancel", QMessageBox.RejectRole)
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == replace_btn:
                delete_recipe(existing["id"])
                # Clean up old media folder
                old_media = os.path.join(
                    str(DATA_DIR),
                    "media", "recipes", str(existing["id"]),
                )
                if os.path.isdir(old_media):
                    shutil.rmtree(old_media, ignore_errors=True)
            elif clicked != keep_btn:
                return  # Cancel

        self._run_import(zip_path)

    def _import_book(self, zip_path, info, **kwargs):
        """Handle book import with duplicate detection."""
        existing = find_book_by_title_producer(info["title"], info["producer"])
        if existing:
            if info["producer"]:
                desc = f'There is already a "{info["title"]}" book by {info["producer"]}.'
            else:
                desc = f'A book called "{info["title"]}" already exists.'
            msg = QMessageBox(self)
            msg.setWindowTitle("Duplicate Book")
            msg.setText(desc)
            replace_btn = msg.addButton("Replace", QMessageBox.DestructiveRole)
            keep_btn = msg.addButton("Keep Both", QMessageBox.AcceptRole)
            msg.addButton("Cancel", QMessageBox.RejectRole)
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == replace_btn:
                delete_book(existing["id"])
                old_media = os.path.join(
                    str(DATA_DIR),
                    "media", "books", str(existing["id"]),
                )
                if os.path.isdir(old_media):
                    shutil.rmtree(old_media, ignore_errors=True)
            elif clicked != keep_btn:
                return  # Cancel

        self._run_book_import(zip_path, **kwargs)

    def _run_book_import(self, zip_path, _delete_zip=False, **kwargs):
        """Run the actual book import in a background thread."""
        progress = self._create_styled_progress("Importing book...", "Import")

        def _on_import_finished(new_book_id):
            progress.close()
            progress.deleteLater()
            if _delete_zip:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            self.recipe_list.filter_recipes()
            self.recipe_list.refresh_total_count()
            bd = load_book_data(new_book_id)
            title = bd.title if bd else "book"
            if getattr(self, "_open_after_import", False):
                self._open_after_import = False
                self.show_book_view(book_id=new_book_id)
                self._show_toast(f"Opened '{title}'")
            else:
                self._show_toast(f"Imported '{title}'")

        worker = _IOWorker(import_book_from_zip, zip_path, **kwargs)
        worker.finished.connect(_on_import_finished)
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_styled_warning("Import Failed", f"Could not import book:\n\n{msg}")
        )
        self._io_worker = worker
        worker.start()

    def _run_import(self, zip_path, _delete_zip=False, **kwargs):
        """Run the actual import in a background thread with progress dialog."""
        progress = self._create_styled_progress("Importing recipe...", "Import")

        def _on_import_finished(new_id):
            progress.close()
            progress.deleteLater()
            if _delete_zip:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            self.recipe_list.filter_recipes()
            self.recipe_list.refresh_total_count()
            rd = load_recipe_data(new_id)
            title = rd.title if rd else "recipe"
            if getattr(self, "_open_after_import", False):
                self._open_after_import = False
                self.show_recipe_detail(new_id)
                self._show_toast(f"Opened '{title}'")
            else:
                self._show_toast(f"Imported '{title}'")

        worker = _IOWorker(import_recipe_from_zip, zip_path, **kwargs)
        worker.finished.connect(_on_import_finished)
        worker.error.connect(lambda _: (progress.close(), progress.deleteLater()))
        worker.error.connect(
            lambda msg: self._show_styled_warning("Import Failed", f"Could not import recipe:\n\n{msg}")
        )
        self._io_worker = worker  # prevent garbage collection
        worker.start()

    def _show_clipboard(self, preview_only=False, preview_data=None):
        """Show clipboard contents in the recipe detail view (read-only).

        Args:
            preview_only: If True, hides Clear/Create buttons (moiety preview mode).
            preview_data: Optional RecipeData to display instead of clipboard_data.
        """
        display_data = preview_data or self._clipboard_data
        if not display_data:
            return

        self._preview_only = preview_only

        # Remember where we came from and preserve edit state
        current = self.stacked_widget.currentWidget()
        if current is self.recipe_detail:
            # Save current edits before switching away
            self.recipe_detail._save_current_step()
            rd = self.recipe_detail._recipe_data
            if rd:
                self._pre_clipboard_view = {
                    "type": "recipe_detail",
                    "recipe_data": rd,
                    "step_index": self.recipe_detail._current_step_index,
                    "editing": self.recipe_detail._editing,
                    "intro_ingredients": self.recipe_detail._intro_ingredients,
                }
            else:
                self._pre_clipboard_view = "recipe_list"
        elif current is self.community_home:
            self._pre_clipboard_view = "community_home"
        else:
            self._pre_clipboard_view = "recipe_list"

        self._viewing_clipboard = True
        self._mic_saved_for_nav = self._is_voice_listening
        if self._is_voice_listening:
            self._voice.stop_listening()
        self._stop_fade_animations()
        self.stacked_widget.setCurrentWidget(self.recipe_detail)
        self._cb_opacity.setOpacity(1.0)
        self._sn_opacity.setOpacity(1.0)
        # Move command bar back to central widget as overlay (may have been embedded in recipe list)
        self.command_bar.setParent(self.centralWidget())
        self.command_bar.show()
        self.command_bar.raise_()
        self.step_navigator.show()

        self._configure_clipboard_commands()

        self.recipe_detail.load_recipe(display_data)
        self.recipe_detail.set_editing(False)
        # Skip the intro step for clipboard — show only actual copied steps
        self.recipe_detail.load_step(1)

        num_nav_steps = len(display_data.steps)
        self.step_navigator.load_steps(
            recipe_id=None, num_steps=num_nav_steps, show_intro=False
        )

        # Force layout recalculation so sizeHint() reflects the new buttons
        self.command_bar.updateGeometry()
        QApplication.processEvents()
        self._position_overlays()
        self._autohide_timer.stop()
        # Show overlay if the first clipboard step has a video
        self._update_play_video_state(1)

    def _configure_clipboard_commands(self):
        """Configure command bar for clipboard viewing mode (read-only)."""
        self.play_video_toggle = None
        self.command_bar.clear()
        back_btn = self.command_bar.add_button("", self._on_clipboard_back, tooltip="Back",
                                              icon=platform_icon("arrow.left", weight="regular", point_size=48, color="white", windows_name="ArrowLeft8"))
        back_btn.setFixedSize(32, 32)
        back_btn.setStyleSheet("""
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 20px;
            }
        """)

        if self._preview_only:
            # Moiety preview mode — purple banner, no Clear/Create buttons
            banner = self.command_bar.add_button("Moiety Preview", lambda: None)
            banner.setEnabled(False)
            banner.setStyleSheet("""
                QPushButton {
                    min-width: 140px;
                    background-color: #2a2a55;
                    color: #9999ff;
                    border: 1px solid #9999ff;
                    border-radius: 6px;
                    padding: 4px 12px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:disabled {
                    background-color: #2a2a55;
                    color: #9999ff;
                }
            """)
        else:
            banner = self.command_bar.add_button("Clipboard", lambda: None)
            banner.setEnabled(False)
            banner.setStyleSheet("""
                QPushButton {
                    min-width: 120px;
                    background-color: #665500;
                    color: #ffcc00;
                    border: 1px solid #ffcc00;
                    border-radius: 6px;
                    padding: 4px 12px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:disabled {
                    background-color: #665500;
                    color: #ffcc00;
                }
            """)
            clear_btn = self.command_bar.add_button("Clear", self._on_clear_clipboard, tooltip="Clear clipboard")
            clear_btn.setStyleSheet("""
                QPushButton {
                    min-width: 60px;
                    background-color: #553333;
                    color: #ff6666;
                    border: 1px solid #ff6666;
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #664444;
                    color: #ff8888;
                }
            """)
            create_btn = self.command_bar.add_button(
                "Create Recipe",
                self._on_create_recipe_from_clipboard,
                tooltip="Create a new recipe from clipboard steps",
            )
            create_btn.setStyleSheet("""
                QPushButton {
                    min-width: 100px;
                    background-color: #335533;
                    color: #66ff66;
                    border: 1px solid #66ff66;
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #446644;
                    color: #88ff88;
                }
            """)
        # Clipboard view doesn't need Tags option (read-only view)
        self.command_bar.add_menu_button(
            [
                ("Ingredients && Directions", "both"),
                ("Ingredients", "ingredients"),
                ("Directions", "directions"),
                ("Image", "image"),
            ],
            self._on_layout_mode_changed,
        )
        icon_btn_style = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 14px;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
        """
        font_down_btn = self.command_bar.add_button(
            "A-", lambda: self._adjust_font_size(-1)
        )
        font_down_btn.setFixedSize(32, 32)
        font_down_btn.setStyleSheet(icon_btn_style)
        font_up_btn = self.command_bar.add_button(
            "A+", lambda: self._adjust_font_size(1)
        )
        font_up_btn.setFixedSize(32, 32)
        font_up_btn.setStyleSheet(icon_btn_style)
        if platform.system() == "Windows":
            fs_btn = self.command_bar.add_button("", self._toggle_fullscreen, tooltip="Fullscreen",
                                                 icon=winui_icon("Fullscreen", point_size=48, color="white"))
            fs_btn.setFixedSize(32, 32)
            fs_btn.setStyleSheet(icon_btn_style)

    def _on_clipboard_back(self):
        """Return from clipboard view to the previous view."""
        self._viewing_clipboard = False
        was_preview = self._preview_only
        self._preview_only = False
        prev = self._pre_clipboard_view
        self._pre_clipboard_view = None
        saved_mic = self._mic_saved_for_nav
        self._mic_saved_for_nav = None

        if isinstance(prev, dict) and prev["type"] == "recipe_detail":
            # Restore the recipe detail view with its saved state
            self._stop_fade_animations()
            self.stacked_widget.setCurrentWidget(self.recipe_detail)
            self._cb_opacity.setOpacity(1.0)
            self._sn_opacity.setOpacity(1.0)
            self.command_bar.show()
            self.step_navigator.show()
            self._configure_recipe_detail_commands()

            rd = prev["recipe_data"]
            self.recipe_detail._recipe_data = rd
            self.recipe_detail._intro_ingredients = prev["intro_ingredients"]
            self.recipe_detail.set_editing(prev["editing"])
            if prev["editing"]:
                self._back_btn.hide()
                self._grocery_list_btn.hide()
                self._save_btn.show()
                self._cancel_btn.show()
                self._edit_separator.show()
                self._image_btn.show()
                self._video_btn.show()
                self._update_video_button_state(prev["step_index"])  # Disable if on intro step
                self._insert_step_btn.show()
                self._append_step_btn.show()
                self._delete_step_btn.show()
                self._paste_clipboard_btn.show()
                self._moiety_btn.show()
                self.step_navigator.set_drag_enabled(True)

            step_idx = prev["step_index"]
            self.recipe_detail._current_step_index = step_idx
            self.recipe_detail._populate_step(step_idx)

            num_nav_steps = len(rd.steps) + 1
            self.step_navigator.load_steps(recipe_id=rd.recipe_id, num_steps=num_nav_steps)
            self.step_navigator.current_step = step_idx
            self.step_navigator._update_active_step()
            self._update_play_video_state(step_idx)
            self._update_tips_buttons_visibility()

            # Force layout recalculation so sizeHint() reflects the new buttons
            self.command_bar.updateGeometry()
            QApplication.processEvents()
            self._position_overlays()
            if prev["editing"]:
                self._autohide_timer.stop()
            else:
                self._autohide_timer.start(self._autohide_timeout_ms)

            # Restore moiety panel if we came from a moiety preview
            if was_preview and prev["editing"]:
                self._moiety_panel.show()
                self._moiety_panel.raise_()
                self._position_moiety_panel()

            # Restore mic state if it was on before entering clipboard
            if saved_mic and not self._is_voice_listening and not self.recipe_detail._editing:
                self._voice.start_listening()
        elif prev == "community_home":
            self._return_to_home()
        else:
            self.show_recipe_list()

    def _on_clear_clipboard(self):
        """Clear clipboard data, persist the deletion, and navigate back."""
        self._clipboard_data = None
        clear_clipboard()
        self._on_clipboard_back()
        self._update_clipboard_button_states()

    def _on_create_recipe_from_clipboard(self):
        """Create a new recipe from the clipboard steps."""
        if not self._clipboard_data or not self._clipboard_data.steps:
            return

        from widgets.create_recipe_dialog import CreateRecipeDialog

        dialog = CreateRecipeDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        title = dialog.get_title()
        description = dialog.get_description()

        # Deep copy steps from clipboard
        steps = copy.deepcopy(self._clipboard_data.steps)
        for step in steps:
            step.step_id = None
            for ing in step.ingredients:
                ing.ingredient_id = None

        # Create recipe data (propagate community origin from clipboard source)
        new_recipe = RecipeData(
            recipe_id=None,
            title=title,
            description=description,
            prep_time_min=None,
            cook_time_min=None,
            cuisine_type=None,
            difficulty=None,
            main_image_path="media/default.jpg",
            intro_video_path=None,
            community_origin_id=self._clipboard_data.community_origin_id,
            community_origin_uploader=self._clipboard_data.community_origin_uploader,
            steps=steps,
            intro_ingredients=[],
            tags=[],
        )

        # Aggregate ingredients for intro
        new_recipe.intro_ingredients = new_recipe.aggregate_ingredients()

        # Save to database
        new_id = insert_recipe_data(new_recipe)

        # Update viewed timestamp so it appears at top of list
        mark_recipe_viewed(new_id)

        self._show_toast(f"Recipe '{title}' created")

    def _on_selection_changed(self, selected_indices):
        """Enable/disable the copy-to-clipboard button based on selection."""
        if hasattr(self, "_copy_steps_btn"):
            self._copy_steps_btn.setEnabled(len(selected_indices) > 0)

    def _update_clipboard_button_states(self):
        """Update enabled state of clipboard-related buttons."""
        has_clipboard = self._clipboard_data is not None
        if hasattr(self, "_view_clipboard_btn"):
            self._view_clipboard_btn.setEnabled(has_clipboard)

    # ------------------------------------------------------------------
    # AI agent
    # ------------------------------------------------------------------

    def _build_app_context(self):
        """Build a read-only snapshot of current app state for workflows."""
        rd = self.recipe_detail._recipe_data
        step_idx = self.recipe_detail._current_step_index
        total = (len(rd.steps) + 1) if rd else 0

        current = self.stacked_widget.currentWidget()
        if current is self.recipe_list:
            view = "recipe_list"
        elif current is self.recipe_detail:
            view = "recipe_detail"
        else:
            view = "video_player"

        return AppContext(
            recipe_data=rd,
            current_step_index=step_idx,
            total_steps=total,
            active_view=view,
            layout_mode=getattr(self.recipe_detail, "_layout_mode", "both"),
            visible_recipes=self.recipe_list.get_visible_recipes(),
        )

    def _on_help_toggled(self):
        """Toggle the voice command help overlay."""
        self._info_panel.toggle_help()
        if self._info_panel.isVisible():
            self._position_info_panel()

    def _info_panel_target_rect(self):
        """Compute the target geometry for the info panel (full window width)."""
        cw = self.centralWidget()
        if not cw:
            return QRect()
        w, h = cw.width(), cw.height()
        cb_h = self.command_bar.sizeHint().height() if self.command_bar.isVisible() else 0
        sn_h = self.step_navigator.height() if self.step_navigator.isVisible() else 0
        # In video player view, leave room for the transport controls bar
        vp_h = self.video_player.control_height if self.stacked_widget.currentWidget() is self.video_player else 0
        px = 0
        pw = w
        py = cb_h
        ph = h - cb_h - sn_h - vp_h

        # When promoted to a top-level window (video player view),
        # convert local coordinates to global screen coordinates.
        if self._info_panel.windowFlags() & Qt.Tool:
            from PySide6.QtCore import QPoint
            origin = cw.mapToGlobal(QPoint(px, py))
            return QRect(origin.x(), origin.y(), pw, max(ph, 0))

        return QRect(px, py, pw, max(ph, 0))

    def _position_info_panel(self, animate=False):
        """Size and position the info panel to fill the full window."""
        target = self._info_panel_target_rect()
        if target.isEmpty():
            return

        if animate and self._info_panel.geometry() != target:
            if self._info_panel_anim is not None:
                self._info_panel_anim.stop()
            self._info_panel_anim = QPropertyAnimation(self._info_panel, b"geometry")
            self._info_panel_anim.setDuration(300)
            self._info_panel_anim.setStartValue(self._info_panel.geometry())
            self._info_panel_anim.setEndValue(target)
            self._info_panel_anim.setEasingCurve(QEasingCurve.InOutQuad)
            self._info_panel_anim.start()
        else:
            if self._info_panel_anim is not None:
                self._info_panel_anim.stop()
                self._info_panel_anim = None
            self._info_panel.setGeometry(target)

        self._info_panel.raise_()

    def _article_tip_target_rect(self):
        """Compute the target geometry for the article tip panel (right half)."""
        cw = self.centralWidget()
        if not cw:
            return QRect()
        w, h = cw.width(), cw.height()
        cb_h = self.command_bar.sizeHint().height() if self.command_bar.isVisible() else 0
        sn_h = self.step_navigator.height() if self.step_navigator.isVisible() else 0
        return QRect(w // 2, cb_h, w // 2, max(h - cb_h - sn_h, 0))

    def _position_article_tip_panel(self, animate=False):
        """Size and position the article tip panel on the right half."""
        target = self._article_tip_target_rect()
        if target.isEmpty():
            return

        if animate and self._article_tip_panel.geometry() != target:
            if self._article_tip_anim is not None:
                self._article_tip_anim.stop()
            self._article_tip_anim = QPropertyAnimation(self._article_tip_panel, b"geometry")
            self._article_tip_anim.setDuration(300)
            self._article_tip_anim.setStartValue(self._article_tip_panel.geometry())
            self._article_tip_anim.setEndValue(target)
            self._article_tip_anim.setEasingCurve(QEasingCurve.InOutQuad)
            self._article_tip_anim.start()
        else:
            if self._article_tip_anim is not None:
                self._article_tip_anim.stop()
                self._article_tip_anim = None
            self._article_tip_panel.setGeometry(target)

        self._article_tip_panel.raise_()

    def _position_moiety_tip_panel(self, animate=False):
        """Size and position the moiety tip panel on the right half."""
        target = self._article_tip_target_rect()
        if target.isEmpty():
            return

        if animate and self._moiety_tip_panel.geometry() != target:
            if self._moiety_tip_anim is not None:
                self._moiety_tip_anim.stop()
            self._moiety_tip_anim = QPropertyAnimation(self._moiety_tip_panel, b"geometry")
            self._moiety_tip_anim.setDuration(300)
            self._moiety_tip_anim.setStartValue(self._moiety_tip_panel.geometry())
            self._moiety_tip_anim.setEndValue(target)
            self._moiety_tip_anim.setEasingCurve(QEasingCurve.InOutQuad)
            self._moiety_tip_anim.start()
        else:
            if self._moiety_tip_anim is not None:
                self._moiety_tip_anim.stop()
                self._moiety_tip_anim = None
            self._moiety_tip_panel.setGeometry(target)

        self._moiety_tip_panel.raise_()

    def _position_moiety_panel(self, animate=False):
        """Size and position the moiety panel on the right half."""
        target = self._article_tip_target_rect()
        if target.isEmpty():
            return

        if animate and self._moiety_panel.geometry() != target:
            if self._moiety_panel_anim is not None:
                self._moiety_panel_anim.stop()
            self._moiety_panel_anim = QPropertyAnimation(self._moiety_panel, b"geometry")
            self._moiety_panel_anim.setDuration(300)
            self._moiety_panel_anim.setStartValue(self._moiety_panel.geometry())
            self._moiety_panel_anim.setEndValue(target)
            self._moiety_panel_anim.setEasingCurve(QEasingCurve.InOutQuad)
            self._moiety_panel_anim.start()
        else:
            if self._moiety_panel_anim is not None:
                self._moiety_panel_anim.stop()
                self._moiety_panel_anim = None
            self._moiety_panel.setGeometry(target)

        self._moiety_panel.raise_()

    def _set_view_overlay_visible(self, visible: bool) -> None:
        """Show or hide the current view's frosted overlay.

        Called automatically when the info panel shows/hides so it can
        use the full window width without the overlay underneath.
        """
        current = self.stacked_widget.currentWidget()
        if current is self.recipe_detail:
            # "image" layout mode keeps overlay hidden — don't restore it
            if visible and getattr(self.recipe_detail, "_layout_mode", "both") == "image":
                return
            self.recipe_detail.overlay.setVisible(visible)
        elif current is self.book_view:
            if visible and getattr(self.book_view, "_layout_mode", "both") == "image":
                return
            self.book_view.overlay.setVisible(visible)

    def _on_agent_processing(self):
        """Show thinking indicator."""
        if self._last_command_was_voice:
            self._position_voice_indicator()
            self.voice_indicator.show_processing()

    def _on_agent_result(self, result):
        """Handle workflow result — display message and execute UI actions."""
        action = result.data.get("action") if result.success and result.data else None
        log.info("Agent result: success=%s action=%s data=%s msg=%r",
                 result.success, action, result.data, result.message[:80] if result.message else "")

        # Actions that should never be spoken
        skip_tts = action in ("play_video", "video_control",
                              "disable_tts", "enable_tts",
                              "show_help", "scale_recipe", "dismiss")

        if self._last_command_was_voice:
            self._position_voice_indicator()
            if result.success:
                self.voice_indicator.show_success()
            else:
                self.voice_indicator.show_error()
            # Speak the response aloud for hands-free use
            if result.message and not skip_tts and self._tts_enabled:
                self._tts.speak(result.message)

        if action:
            if action == "navigate_step":
                if self.stacked_widget.currentWidget() is self.video_player:
                    self.video_player._on_stop()
                step_index = result.data["step_index"]
                self.step_navigator._on_step_clicked(step_index)
                self.step_navigator.scroll_to_step(step_index)

            elif action == "adjust_font_size":
                self._adjust_font_size(result.data["delta"])

            elif action == "change_view":
                mode = result.data["view_mode"]
                if mode == "tags" and self.stacked_widget.currentWidget() is self.recipe_list:
                    self.recipe_list.show_tag_side_panel()
                else:
                    self._on_layout_mode_changed(mode)

            elif action == "scroll_pane":
                if self._info_panel.isVisible():
                    self._info_panel.scroll_by_page(result.data["direction"])
                else:
                    self.recipe_detail.scroll_pane(
                        result.data["pane"], result.data["direction"],
                    )

            elif action == "play_video":
                if self._vpo_has_video:
                    self._tts.cancel()
                    self._play_current_step_video()
                else:
                    if self._last_command_was_voice and self._tts_enabled:
                        self._tts.speak("No video for this step.")

            elif action == "video_control":
                va = result.data["video_action"]
                log.info("Video control: action=%s, ducked_volume=%s, current_volume=%.2f",
                         va, self._ducked_volume,
                         self.video_player.audio_output.volume())
                if va == "stop":
                    self.video_player._on_stop()
                elif va in ("play", "resume"):
                    self.video_player.media_player.play()
                elif va == "pause":
                    self.video_player.media_player.pause()
                elif va == "skip_back":
                    self.video_player.skip_video(-1)
                elif va == "skip_forward":
                    self.video_player.skip_video(1)
                elif va == "mute":
                    self.video_player._user_muted = True
                    self.video_player.audio_output.setMuted(True)
                    self.video_player.update_vol_icon()
                    log.info("Mute: muted=True, ducked_volume=%s", self._ducked_volume)
                elif va == "unmute":
                    self.video_player._user_muted = False
                    self.video_player.audio_output.setMuted(False)
                    self.video_player.update_vol_icon()
                    log.info("Unmute: muted=False, ducked_volume=%s", self._ducked_volume)

            elif action == "pause_listening":
                self._listening_paused = True
                self._update_mic_button_style()

            elif action == "resume_listening":
                self._listening_paused = False
                self._update_mic_button_style()

            elif action == "disable_tts":
                self._tts_enabled = False
                self._settings.setValue("tts_enabled", False)
                self._sync_tts_toggle()
                self._tts.speak("Voice responses disabled.")

            elif action == "enable_tts":
                self._tts_enabled = True
                self._settings.setValue("tts_enabled", True)
                self._sync_tts_toggle()
                self._tts.speak("Voice responses enabled.")

            elif action == "convert_unit":
                self._info_panel.show_conversion(result.message)
                self._position_info_panel()

            elif action == "show_help":
                # Position before showing — top-level tool windows need
                # geometry set before show() to render correctly.
                self._position_info_panel()
                self._info_panel.toggle_help()
                if self._info_panel.isVisible():
                    self._position_info_panel()

            elif action == "scale_recipe":
                self._info_panel.show_scale(result.message)
                self._position_info_panel()

            elif action == "dismiss":
                self._info_panel.hide()

            elif action == "open_book":
                book_id = result.data["book_id"]
                self.show_book_view(book_id=book_id)

        log.info("Agent result done — calling unduck (ducked_volume=%s)", self._ducked_volume)
        self._unduck_video_volume()
        self._maybe_start_followup()

    def _maybe_start_followup(self):
        """Start follow-up listening if the last command was voice-triggered."""
        # Disabled — follow-up timeout was more annoying than helpful.
        # User prefers to just say the wake word again.
        self._last_command_was_voice = False

    # ------------------------------------------------------------------
    # Voice Control
    # ------------------------------------------------------------------

    def _toggle_voice_recording(self):
        """Toggle push-to-talk voice recording."""
        if self._is_voice_recording:
            self._voice.stop_recording()
        else:
            self._voice.start_recording()

    def _on_voice_transcription(self, text):
        """Handle transcribed voice input — send to agent."""
        self._wake_word_active = False  # Recording cycle complete
        log.info("Voice transcription received: %r (paused=%s, view=%s)",
                 text, self._listening_paused,
                 "video_player" if self.stacked_widget.currentWidget() is self.video_player else "other")
        self._last_command_was_voice = True
        # When paused, only accept "resume" / "unmute" commands
        if self._listening_paused:
            lower = text.lower()
            if "resume" not in lower and "unmute" not in lower:
                log.info("Dropping command — listening is paused and text is not resume/unmute")
                self.voice_indicator.dismiss()
                return
        context = self._build_app_context()
        log.info("Sending to agent: text=%r, active_view=%s", text, context.active_view)
        self._agent.process_input(text, context)

    def _on_voice_recording_started(self):
        """Show voice indicator for recording."""
        if not self._is_voice_listening:
            return  # Mic was turned off — ignore queued signal
        self._is_voice_recording = True
        self._last_command_was_voice = True
        self._duck_video_volume()
        # In hands-free mode, suppress the listening indicator — it fires on
        # every ambient noise spike and is distracting.  Only show the
        # processing/result indicators (triggered by recording_stopped).
        # Wake-word recordings always show the indicator regardless.
        if self._hands_free and not self._wake_word_active:
            return
        self._position_voice_indicator()
        self.voice_indicator.show_listening()

    def _on_voice_recording_stopped(self):
        """Recording done — transition to processing."""
        self._is_voice_recording = False
        if not self._is_voice_listening:
            return  # Mic was turned off — ignore queued signal
        if self._hands_free and not self._wake_word_active:
            return  # Defer overlay until agent actually processes text
        self._position_voice_indicator()
        self.voice_indicator.show_processing()

    def _on_voice_error(self, error_msg):
        """Handle voice service errors."""
        self._is_voice_recording = False
        self._unduck_video_volume()
        wake = self._wake_word_active
        self._wake_word_active = False
        if not self._is_voice_listening or (self._hands_free and not wake):
            return  # Suppress errors from ambient hands-free recordings only
        if not error_msg:
            self.voice_indicator.dismiss()
        else:
            self._position_voice_indicator()
            self.voice_indicator.show_error()

    def _update_mic_button_style(self):
        """Update mic button stylesheet based on paused state."""
        if self._listening_paused:
            bg, border, color = "#5a5a2d", "#8a8a4a", "#ffff66"
        else:
            bg, border, color = "#2d5a2d", "#4a8a4a", "#66ff66"
        font_family = getattr(self, "_mic_font_family", "")
        # Update SF Symbol icon color on macOS when checked state changes
        if platform.system() == "Darwin" and self._mic_toggle.isChecked():
            icon_color = color
            self._mic_toggle.setIcon(sf_symbol("microphone", point_size=16, color=icon_color))
        elif platform.system() == "Darwin":
            self._mic_toggle.setIcon(sf_symbol("microphone", point_size=16, color="#cccccc"))
        self._mic_toggle.setStyleSheet(f"""
            QPushButton {{
                min-width: 38px;
                max-width: 38px;
                font-size: 16px;
                font-family: {font_family};
                color: #cccccc;
                padding: 0px;
            }}
            QPushButton:checked {{
                background-color: {bg};
                border: 1px solid {border};
                color: {color};
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                color: white;
            }}
        """)

    def _create_tts_toggle(self):
        """Create a TTS toggle button and add it to the command bar."""
        if platform.system() == "Windows":
            tts_icon = "\uE767"
        else:
            tts_icon = ""
        btn = self.command_bar.add_toggle_button(
            tts_icon, self._on_tts_toggled, size=32,
            tooltip="Toggle voice responses",
        )
        if platform.system() == "Darwin":
            btn.setIconSize(QSize(20, 20))
        btn.setChecked(self._tts_enabled)
        # Apply icon/stylesheet directly on the local btn — self._tts_toggle
        # isn't assigned yet so _update_tts_button_style() would bail out.
        self._apply_tts_style(btn)
        btn.setVisible(self._is_voice_listening)
        return btn

    def _on_tts_toggled(self, checked):
        """Handle TTS toggle button click."""
        self._tts_enabled = checked
        self._settings.setValue("tts_enabled", checked)
        self._update_tts_button_style()

    def _sync_tts_toggle(self):
        """Update TTS toggle button state without triggering _on_tts_toggled."""
        if hasattr(self, "_tts_toggle") and self._tts_toggle:
            self._tts_toggle.blockSignals(True)
            self._tts_toggle.setChecked(self._tts_enabled)
            self._tts_toggle.blockSignals(False)
        self._update_tts_button_style()

    def _update_tts_button_style(self):
        """Update TTS button icon and stylesheet based on enabled state."""
        if not hasattr(self, "_tts_toggle") or not self._tts_toggle:
            return
        self._apply_tts_style(self._tts_toggle)

    def _apply_tts_style(self, btn):
        """Apply icon and stylesheet to a TTS toggle button."""
        is_on = self._tts_enabled
        if platform.system() == "Darwin":
            icon_color = "#66ccff" if is_on else "#ffffff"
            btn.setIcon(sf_symbol("waveform", point_size=16, color=icon_color))
        btn.setStyleSheet("""
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets";
                background-color: transparent;
                border: 1px solid transparent;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #2d4a5a;
                border: 1px solid #4a7a8a;
                color: #66ccff;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
        """)

    def _create_hands_free_toggle(self):
        """Create a hands-free mode toggle button and add it to the command bar."""
        if platform.system() == "Windows":
            hf_icon = "\uF270"  # WinUI 3: Hands-free
        else:
            hf_icon = ""
        tooltip = ("No wake word mode" if self._headset_active
                    else "Connect a headset to enable no-wake-word mode")
        btn = self.command_bar.add_toggle_button(
            hf_icon, self._on_hands_free_toggled, size=32,
            tooltip=tooltip,
        )
        if platform.system() == "Darwin":
            btn.setIconSize(QSize(20, 20))
        btn.setChecked(self._hands_free)
        btn.setEnabled(self._headset_active)
        self._apply_hands_free_style(btn)
        btn.setVisible(self._is_voice_listening)
        return btn

    def _on_hands_free_toggled(self, checked):
        """Handle hands-free toggle button click."""
        if checked and not self._headset_active:
            # Safety net — shouldn't happen if button is disabled
            if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
                self._hands_free_toggle.blockSignals(True)
                self._hands_free_toggle.setChecked(False)
                self._hands_free_toggle.blockSignals(False)
            return
        self._hands_free = checked
        self._voice.set_hands_free(checked)
        self._update_hands_free_button_style()

    def _on_hands_free_changed(self, enabled):
        """Update UI when hands-free mode changes programmatically."""
        self._hands_free = enabled
        if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
            self._hands_free_toggle.blockSignals(True)
            self._hands_free_toggle.setChecked(enabled)
            self._hands_free_toggle.blockSignals(False)
        self._update_hands_free_button_style()

    def _update_hands_free_button_style(self):
        """Update hands-free button icon and stylesheet based on state."""
        if not hasattr(self, "_hands_free_toggle") or not self._hands_free_toggle:
            return
        self._apply_hands_free_style(self._hands_free_toggle)

    def _apply_hands_free_style(self, btn):
        """Apply icon and stylesheet to a hands-free toggle button."""
        is_on = self._hands_free
        if platform.system() == "Darwin":
            icon_color = "#ff9933" if is_on else "#ffffff"
            btn.setIcon(sf_symbol("ear.badge.waveform", point_size=16, color=icon_color))
        btn.setStyleSheet(f"""
            QPushButton {{
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets";
                background-color: transparent;
                border: 1px solid transparent;
                color: #ffffff;
            }}
            QPushButton:checked {{
                background-color: #5a3d1a;
                border: 1px solid #8a6a3a;
                color: #ff9933;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                color: white;
            }}
        """)

    def _create_headset_toggle(self):
        """Create a headset mode toggle button and add it to the command bar."""
        if platform.system() == "Windows":
            hs_icon = "\uE7F6"  # Segoe Fluent Icons: Headphone
        else:
            hs_icon = ""
        btn = self.command_bar.add_toggle_button(
            hs_icon, self._on_headset_toggled, size=32,
            tooltip="Headset override (for non-Bluetooth headsets)",
        )
        if platform.system() == "Darwin":
            btn.setIconSize(QSize(20, 20))
        btn.setChecked(self._headset_override)
        self._apply_headset_style(btn)
        btn.setVisible(self._is_voice_listening)
        return btn

    def _on_headset_toggled(self, checked):
        """Handle headset toggle button click (manual headset override)."""
        self._headset_override = checked
        self._settings.setValue("headset_mode", checked)
        self._update_headset_button_style()

        # Update hands-free toggle enabled state
        if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
            self._hands_free_toggle.setEnabled(self._headset_active)
            if self._headset_active:
                self._hands_free_toggle.setToolTip("No wake word mode")
            else:
                self._hands_free_toggle.setToolTip(
                    "Connect a headset to enable no-wake-word mode"
                )

        # If override turned off and no auto-detection, disable no-wake-word
        if not checked and not self._headset_detected and self._hands_free:
            self._hands_free = False
            self._voice.set_hands_free(False)
            if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
                self._hands_free_toggle.blockSignals(True)
                self._hands_free_toggle.setChecked(False)
                self._hands_free_toggle.blockSignals(False)
            self._update_hands_free_button_style()
            self._show_toast("Switched to wake word mode")

    def _update_headset_button_style(self):
        """Update headset button icon and stylesheet based on state."""
        if not hasattr(self, "_headset_toggle") or not self._headset_toggle:
            return
        self._apply_headset_style(self._headset_toggle)

    def _apply_headset_style(self, btn):
        """Apply icon and stylesheet to a headset toggle button."""
        is_on = self._headset_override
        if platform.system() == "Darwin":
            icon_color = "#66ccff" if is_on else "#ffffff"
            btn.setIcon(sf_symbol("headphones", point_size=16, color=icon_color))
        btn.setStyleSheet("""
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                font-size: 16px;
                font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets";
                background-color: transparent;
                border: 1px solid transparent;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #2d4a5a;
                border: 1px solid #4a7a8a;
                color: #66ccff;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
        """)

    def _create_refresh_audio_button(self):
        """Create a refresh-audio button and add it to the command bar."""
        if platform.system() == "Windows":
            icon_text = "\uE72C"  # Segoe Fluent Icons: Refresh
        else:
            icon_text = ""
        btn = self.command_bar.add_button(
            icon_text, self._on_refresh_audio,
            tooltip="Refresh audio device",
        )
        if platform.system() == "Darwin":
            btn.setIcon(sf_symbol("arrow.triangle.2.circlepath", point_size=14, color="#cccccc"))
            btn.setIconSize(QSize(18, 18))
        btn.setStyleSheet("""
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px;
                background-color: transparent;
                border: 1px solid transparent;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
        """)
        return btn

    @property
    def _headset_active(self) -> bool:
        """True when headset is confirmed (auto-detected OR manual override)."""
        return self._headset_detected or self._headset_override

    def _update_headset_detection(self):
        """Re-run Bluetooth headset auto-detection for the current input device."""
        dev = QMediaDevices.defaultAudioInput()
        device_id = bytes(dev.id()).decode("utf-8", errors="replace")
        was_active = self._headset_active
        self._headset_detected = is_bluetooth_headset(device_id)
        now_active = self._headset_active

        # Headset lost while no-wake-word was active → enforce wake word
        if was_active and not now_active and self._hands_free:
            self._hands_free = False
            self._voice.set_hands_free(False)
            if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
                self._hands_free_toggle.blockSignals(True)
                self._hands_free_toggle.setChecked(False)
                self._hands_free_toggle.blockSignals(False)
            self._update_hands_free_button_style()
            self._show_toast("Headset disconnected — switched to wake word mode")

        # Update hands-free toggle enabled state
        if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
            self._hands_free_toggle.setEnabled(now_active)
            if now_active:
                self._hands_free_toggle.setToolTip("No wake word mode")
            else:
                self._hands_free_toggle.setToolTip(
                    "Connect a headset to enable no-wake-word mode"
                )

        # Toast on new headset detection (only when state actually changes)
        if not was_active and now_active and self._headset_detected:
            self._show_toast("Headset detected")

    def _check_audio_devices(self):
        """Poll for OS audio device changes and auto-refresh if needed."""
        output_dev = QMediaDevices.defaultAudioOutput()
        input_dev = QMediaDevices.defaultAudioInput()
        changed = False
        if output_dev.id() != self._last_output_device_id:
            self._last_output_device_id = output_dev.id()
            self.video_player.audio_output.setDevice(output_dev)
            changed = True
        if input_dev.id() != self._last_input_device_id:
            self._last_input_device_id = input_dev.id()
            if self._is_voice_listening:
                self._voice.restart_stream()
            self._update_headset_detection()
            changed = True
        if changed:
            self._show_toast("Audio device updated")

    def _on_refresh_audio(self):
        """Manual refresh — force all audio streams to pick up current devices."""
        self.video_player.audio_output.setDevice(
            QMediaDevices.defaultAudioOutput()
        )
        self._last_output_device_id = QMediaDevices.defaultAudioOutput().id()
        self._last_input_device_id = QMediaDevices.defaultAudioInput().id()
        if self._is_voice_listening:
            self._voice.restart_stream()
        self._update_headset_detection()
        self._show_toast("Audio device refreshed")

    def _deferred_mic_restore(self):
        """Restore mic listening from saved state after the UI repaints."""
        should_restore = self._mic_saved_for_nav and not self._is_voice_listening
        self._mic_saved_for_nav = None
        if should_restore:
            QTimer.singleShot(20, lambda: self._voice.start_listening()
                              if not self._is_voice_listening else None)

    def _on_mic_toggled(self, checked):
        """Handle mic toggle button click — start/stop always-on listening."""
        log.info("Mic toggled: checked=%s, wakeword_avail=%s",
                 checked, self._voice.is_wakeword_available())
        # User explicitly toggled mic — cancel any video auto-pause
        self._voice_paused_for_video = False
        self._listening_paused = False
        if checked:
            self._voice.start_listening()
        else:
            self._voice.stop_listening()
            # Immediately dismiss voice indicator and cancel any TTS.
            # Reset _last_command_was_voice so in-flight transcription
            # results don't re-show the indicator after dismiss.
            self._last_command_was_voice = False
            self.voice_indicator.dismiss()
            self._tts.cancel()
            self._unduck_video_volume()

    def _on_wake_word_detected(self):
        """Wake word heard — duck video volume and show voice indicator."""
        self._wake_word_active = True
        self._last_command_was_voice = True
        self._position_voice_indicator()
        if self._listening_paused:
            # Flash yellow mic to indicate listening is paused
            self.voice_indicator.show_paused()
            return
        self._duck_video_volume()
        self.voice_indicator.show_listening()

    def _on_followup_started(self):
        """Follow-up listening started — show listening indicator."""
        self._last_command_was_voice = True
        self._position_voice_indicator()
        self.voice_indicator.show_listening()

    def _on_followup_expired(self):
        """Follow-up listening timed out — dismiss voice indicator."""
        self.voice_indicator.dismiss()

    def _on_listening_started(self):
        """Always-on listening is now active."""
        self._is_voice_listening = True
        self._listening_paused = False
        # Update mic toggle if it exists (may not during startup)
        if hasattr(self, "_mic_toggle") and self._mic_toggle:
            self._mic_toggle.setChecked(True)
            self._update_mic_button_style()
        # Only show voice-related toggles in views that support voice commands
        current = self.stacked_widget.currentWidget()
        voice_view = current is self.recipe_detail or current is self.video_player
        if voice_view and hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
            self._hands_free_toggle.setEnabled(self._headset_active)
            self._hands_free_toggle.show()
        if voice_view and hasattr(self, "_headset_toggle") and self._headset_toggle:
            self._headset_toggle.show()

    def _on_listening_stopped(self):
        """Always-on listening has stopped."""
        self._is_voice_listening = False
        self._listening_paused = False
        # If listening stopped while a TTS warning was playing, clean up
        if self._pending_video_path:
            self._tts.cancel()
            try:
                self._tts.speech_finished.disconnect(self._play_pending_video_after_warning)
            except RuntimeError:
                pass
            path = self._pending_video_path
            self._pending_video_path = None
            if path:
                self.video_player.load_video(path)
                self.show_video_player()
                self.video_player.media_player.play()
        # Reset hands-free mode when listening stops
        self._hands_free_paused_for_video = False
        if self._hands_free:
            self._hands_free = False
            self._voice.set_hands_free(False)
        # When auto-paused for video, keep the mic button checked in yellow
        # "paused" state so the user knows it will resume.
        if self._voice_paused_for_video:
            if hasattr(self, "_mic_toggle") and self._mic_toggle:
                self._listening_paused = True
                self._update_mic_button_style()
            return
        # Update mic toggle if it exists
        if hasattr(self, "_mic_toggle") and self._mic_toggle:
            self._mic_toggle.setChecked(False)
            self._update_mic_button_style()
        if hasattr(self, "_tts_toggle") and self._tts_toggle:
            self._tts_toggle.hide()
        if hasattr(self, "_hands_free_toggle") and self._hands_free_toggle:
            self._hands_free_toggle.setChecked(False)
            self._hands_free_toggle.hide()
        if hasattr(self, "_headset_toggle") and self._headset_toggle:
            self._headset_toggle.hide()

    def _duck_video_volume(self) -> None:
        """Mute video while recording a voice command.

        With a headset, even 5 % volume leaks from earpiece to mic and can
        keep the RMS above SILENCE_THRESHOLD, preventing silence detection
        from triggering.  The recording then runs to MAX_RECORDING_TIME (15 s)
        producing a huge, garbled audio file that stalls whisper.
        Muting eliminates the leakage entirely — the duck only lasts 2-3 s
        while the user speaks, so it's barely noticeable.
        """
        if self.stacked_widget.currentWidget() is not self.video_player:
            return
        ao = self.video_player.audio_output
        if self._ducked_volume is None:
            self._ducked_volume = ao.volume()
            ao.setVolume(0.0)
            log.info("Duck: saved volume=%.2f → 0.0", self._ducked_volume)

    def _unduck_video_volume(self) -> None:
        """Restore video volume after voice command processing."""
        if self._ducked_volume is not None:
            log.info("Unduck: restoring volume to %.2f", self._ducked_volume)
            self.video_player.audio_output.setVolume(self._ducked_volume)
            self._ducked_volume = None

    def closeEvent(self, event):
        """Signal threads to stop without blocking — OS cleans up on exit."""
        self._tts.stop()
        self._voice.stop_listening_nonblocking()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Auto-hide bars
    # ------------------------------------------------------------------

    def _mouse_over_bars(self):
        """Check if the mouse cursor is over the command bar or step navigator."""
        pos = QCursor.pos()
        for bar in (self.command_bar, self.step_navigator):
            if bar.isVisible():
                local = bar.mapFromGlobal(pos)
                if bar.rect().contains(local):
                    return True
        return False

    def _autohide_bars(self):
        """Hide command bar and step navigator after inactivity."""
        if self.stacked_widget.currentWidget() is not self.recipe_detail:
            return
        # Never auto-hide while editing or viewing clipboard
        if self.recipe_detail._editing or self._viewing_clipboard:
            return
        # Don't hide if mouse is hovering over the bars
        if self._mouse_over_bars():
            self._autohide_timer.start(self._autohide_timeout_ms)
            return
        self._fade_bars(visible=False)

    def _show_bars(self):
        """Show command bar and step navigator and restart the hide timer."""
        if self.stacked_widget.currentWidget() is self.recipe_detail:
            self._fade_bars(visible=True)
            # Don't start the hide timer while editing or viewing clipboard
            if not self.recipe_detail._editing and not self._viewing_clipboard:
                self._autohide_timer.start(self._autohide_timeout_ms)

    # ------------------------------------------------------------------
    # Fade animation helpers
    # ------------------------------------------------------------------

    def _fade_bars(self, visible):
        """Show bars instantly or fade them out."""
        if visible:
            # Stop any running fade-out animations
            for attr in ("_fade_anim_cb", "_fade_anim_sn"):
                old = getattr(self, attr)
                if old is not None:
                    old.stop()
                    setattr(self, attr, None)

            # Show instantly
            self._cb_opacity.setOpacity(1.0)
            self._sn_opacity.setOpacity(1.0)
            needs_inset_update = False
            if not self.command_bar.isVisible():
                self.command_bar.show()
                needs_inset_update = True
            if not self.step_navigator.isVisible():
                self.step_navigator.show()
                needs_inset_update = True
            # Hide step indicator when bars are visible
            self._step_indicator.hide()
            if needs_inset_update:
                self.command_bar.updateGeometry()
                QApplication.processEvents()
                self._update_detail_insets()
                if self._info_panel.isVisible():
                    self._position_info_panel(animate=True)
                if self._article_tip_panel.isVisible():
                    self._position_article_tip_panel(animate=True)
                if self._moiety_tip_panel.isVisible():
                    self._position_moiety_tip_panel(animate=True)
                if self._moiety_panel.isVisible():
                    self._position_moiety_panel(animate=True)
        else:
            # Fade out
            duration = 300
            for effect, attr in (
                (self._cb_opacity, "_fade_anim_cb"),
                (self._sn_opacity, "_fade_anim_sn"),
            ):
                old = getattr(self, attr)
                if old is not None:
                    old.stop()

                current = effect.opacity()
                scaled_duration = max(int(duration * current), 50)

                anim = QPropertyAnimation(effect, b"opacity")
                anim.setDuration(scaled_duration)
                anim.setStartValue(current)
                anim.setEndValue(0.0)
                anim.setEasingCurve(QEasingCurve.InOutQuad)
                setattr(self, attr, anim)
                anim.start()

            self._fade_anim_sn.finished.connect(self._on_fade_out_finished)

    def _update_detail_insets(self, animate=True):
        """Tell the recipe detail view how much space the bars occupy."""
        if self.stacked_widget.currentWidget() is not self.recipe_detail:
            return
        top = self.command_bar.sizeHint().height() if self.command_bar.isVisible() else 0
        bottom = self.step_navigator.height() if self.step_navigator.isVisible() else 0
        self.recipe_detail.set_bar_insets(top, bottom, animate=animate)

    def _update_book_insets(self, animate=True):
        """Tell the book view how much space the command bar occupies."""
        if self.stacked_widget.currentWidget() is not self.book_view:
            return
        top = self.command_bar.sizeHint().height() if self.command_bar.isVisible() else 0
        self.book_view.set_bar_insets(top, 0, animate=animate)

    def _fade_video_overlay(self, visible):
        """Fade the video play overlay in or out."""
        target = 1.0 if visible else 0.0
        duration = 500 if visible else 200  # fade-in, quick fade-out

        if visible and not self.video_play_overlay.isVisible():
            self._vpo_opacity.setOpacity(0.0)
            self.video_play_overlay.show()

        old = self._fade_anim_vpo
        if old is not None:
            old.stop()

        current = self._vpo_opacity.opacity()
        remaining = abs(target - current)
        scaled = max(int(duration * remaining), 50)

        anim = QPropertyAnimation(self._vpo_opacity, b"opacity")
        anim.setDuration(scaled)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._fade_anim_vpo = anim

        if not visible:
            anim.finished.connect(self._on_vpo_fade_out_finished)

        anim.start()

    def _on_vpo_fade_out_finished(self):
        """Hide the overlay after it fades out."""
        if self._vpo_opacity.opacity() < 0.1:
            self.video_play_overlay.hide()

    def _on_fade_out_finished(self):
        """Called when fade-out animation completes — hide widgets fully."""
        # Only hide if opacity actually reached 0 (wasn't interrupted by fade-in)
        if self._cb_opacity.opacity() < 0.1:
            self.command_bar.hide()
        if self._sn_opacity.opacity() < 0.1:
            self.step_navigator.hide()
            # Show step indicator when bars are hidden (recipe detail only)
            if self.stacked_widget.currentWidget() is self.recipe_detail:
                self._position_step_indicator()
                self._step_indicator.show()
                self._step_indicator.raise_()
        self._update_detail_insets()
        if self._info_panel.isVisible():
            self._position_info_panel(animate=True)
        if self._article_tip_panel.isVisible():
            self._position_article_tip_panel(animate=True)
        if self._moiety_tip_panel.isVisible():
            self._position_moiety_tip_panel(animate=True)
        if self._moiety_panel.isVisible():
            self._position_moiety_panel(animate=True)

    def mouseMoveEvent(self, event):
        self._show_bars()
        super().mouseMoveEvent(event)

    def eventFilter(self, obj, event):
        """Catch mouse moves and info panel show/hide events."""
        if event.type() == QEvent.MouseMove:
            self._show_bars()
        elif obj is self._info_panel:
            if event.type() == QEvent.Show:
                self._set_view_overlay_visible(False)
            elif event.type() == QEvent.Hide:
                self._set_view_overlay_visible(True)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self._toggle_fullscreen()
        elif event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif event.key() == Qt.Key_V and not event.isAutoRepeat():
            # V key toggles voice recording (push-to-talk)
            self._toggle_voice_recording()
        super().keyPressEvent(event)

    def _is_image_mode(self) -> bool:
        """Return True if the current view is showing full image (no left overlay)."""
        current = self.stacked_widget.currentWidget()
        if current is self.recipe_detail:
            return self.recipe_detail._layout_mode == "image"
        if current is self.book_view:
            return self.book_view._layout_mode == "image"
        return False

    def _overlay_region(self, w: int) -> tuple[int, int]:
        """Return (left_x, width) of the region overlays should center in.

        In image mode the full window is available; otherwise overlays
        center in the right half (the left half is the frosted overlay).
        """
        if self._is_image_mode():
            return 0, w
        return w // 2, w - w // 2

    def _on_layout_mode_changed(self, mode: str) -> None:
        """Handle layout mode change from dropdown or voice command."""
        self.recipe_detail.set_layout_mode(mode)
        # In article mode, ensure ingredients stay hidden after layout switch
        if self._article_mode:
            self.recipe_detail.ingredients_editor.hide()
            self.recipe_detail.aggregate_warning.hide()
        # Sync the dropdown button label
        for act in self._layout_mode_btn._menu.actions():
            if act.data() == mode:
                self._layout_mode_btn.setText(f"{act.text()}  ▾")
                break
        # Reposition overlays since the available region changed
        self._position_overlays()
        if self._info_panel.isVisible():
            self._position_info_panel()

    def _promote_info_panel(self):
        """Promote info panel to a top-level tool window for video player."""
        ip = self._info_panel
        if ip.windowFlags() & Qt.Tool:
            return
        was_visible = ip.isVisible()
        ip.setParent(self, Qt.Tool | Qt.FramelessWindowHint)
        # No WA_TranslucentBackground — the panel is opaque dark, not
        # translucent.  Translucent backing store on macOS Tool windows
        # causes the stylesheet background to not render.
        ip.setAttribute(Qt.WA_StyledBackground, True)
        ip.setAttribute(Qt.WA_ShowWithoutActivating, True)
        ip.setStyleSheet(ip.styleSheet())
        if was_visible:
            self._position_info_panel()
            ip.show()

    def _demote_info_panel(self):
        """Restore info panel as a child of central_widget."""
        ip = self._info_panel
        if not (ip.windowFlags() & Qt.Tool):
            return
        was_visible = ip.isVisible()
        ip.setParent(self.centralWidget())
        ip.setAttribute(Qt.WA_StyledBackground, True)
        ip.setStyleSheet(ip.styleSheet())
        if was_visible:
            self._position_info_panel()
            ip.show()
            ip.raise_()

    def _promote_voice_indicator(self):
        """Make the voice indicator a top-level tool window.

        QVideoWidget uses a native macOS rendering surface that sits above
        all sibling Qt widgets.  Promoting the indicator to a separate
        top-level window lets it render above the video.
        """
        vi = self.voice_indicator
        if vi.windowFlags() & Qt.Tool:
            return  # Already promoted
        was_visible = vi.isVisible()
        vi.setParent(self, Qt.Tool | Qt.FramelessWindowHint)
        vi.setAttribute(Qt.WA_TranslucentBackground, True)
        vi.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # Reposition immediately so the indicator appears centered in
        # global coordinates, not at the stale child-widget position.
        self._position_voice_indicator()
        if was_visible:
            vi.show()

    def _demote_voice_indicator(self):
        """Restore the voice indicator as a child of central_widget."""
        vi = self.voice_indicator
        if not (vi.windowFlags() & Qt.Tool):
            return  # Already a child widget
        was_visible = vi.isVisible()
        vi.setParent(self.centralWidget())
        vi.setAttribute(Qt.WA_TranslucentBackground, True)
        # Reposition immediately after re-parenting to avoid a frame at
        # the stale top-level (global) coordinates.
        self._position_voice_indicator()
        if was_visible:
            vi.show()
            vi.raise_()

    def _position_voice_indicator(self):
        """Position the voice indicator.

        In the video player view the indicator is a top-level tool window
        positioned using global screen coordinates (above the native video
        surface).  In recipe detail it's a child widget positioned locally.
        """
        cw = self.centralWidget()
        if not cw:
            return
        w, h = cw.width(), cw.height()
        vi = self.voice_indicator

        if self.stacked_widget.currentWidget() is self.video_player:
            # Top-level window — use global coordinates
            from PySide6.QtCore import QPoint
            center = cw.mapToGlobal(QPoint(w // 2, h // 2))
            vi.move(center.x() - vi.width() // 2,
                    center.y() - vi.height() // 2)
        elif self.stacked_widget.currentWidget() is self.recipe_list:
            # Recipe list — center in window
            vi.move(w // 2 - vi.width() // 2,
                    h // 2 - vi.height() // 2)
        else:
            # Recipe detail — center in overlay region (right half, or
            # full window in image mode).  When the info panel is visible
            # it covers the right half, so center in the left half instead.
            region_x, region_w = self._overlay_region(w)
            cb_h = self.command_bar.sizeHint().height()
            sn_h = self.step_navigator.height()
            avail_h = h - cb_h - sn_h
            if self._info_panel.isVisible():
                region_center_x = w // 4
            else:
                region_center_x = region_x + region_w // 2
            center_y = cb_h + avail_h // 2
            vi.move(region_center_x - vi.width() // 2,
                    center_y - vi.height() // 2)

        vi.raise_()

    def _position_overlays(self):
        """Position the stacked widget, command bar, and step navigator."""
        cw = self.centralWidget()
        if not cw:
            return
        w, h = cw.width(), cw.height()

        # Check if command bar is embedded in recipe list (not an overlay)
        command_bar_is_overlay = self.command_bar.parent() is cw

        # Command bar positioning only when it's an overlay
        cb_h = self.command_bar.sizeHint().height()
        if command_bar_is_overlay:
            self.command_bar.setGeometry(0, 0, w, cb_h)

        # Step navigator at bottom, full width
        sn_h = self.step_navigator.height()  # Fixed at 65px
        self.step_navigator.setGeometry(0, h - sn_h, w, sn_h)

        # Video play overlay: centered in the overlay region
        region_x, region_w = self._overlay_region(w)
        avail_h = h - cb_h - sn_h
        vpo_size = min(region_w, avail_h) // 6  # ~1/6 of available space
        vpo_size = max(vpo_size, 50)
        self.video_play_overlay.setFixedSize(vpo_size, vpo_size)
        region_center_x = region_x + region_w // 2
        center_y = cb_h + avail_h // 2
        self.video_play_overlay.move(
            region_center_x - vpo_size // 2,
            center_y - vpo_size // 2,
        )

        # Stacked widget positioning
        if self.stacked_widget.currentWidget() in (self.recipe_detail, self.book_view):
            # Recipe detail / book view: fills entire area (bars overlay)
            self.stacked_widget.setGeometry(0, 0, w, h)
        elif command_bar_is_overlay:
            # Other views with overlay command bar: push content below
            top = cb_h if self.command_bar.isVisible() else 0
            self.stacked_widget.setGeometry(0, top, w, h - top)
        else:
            # Recipe list view with embedded command bar: fill entire area
            self.stacked_widget.setGeometry(0, 0, w, h)

        # Ensure overlays stay above the stacked widget
        if command_bar_is_overlay:
            self.command_bar.raise_()
        self.video_play_overlay.raise_()
        self.step_navigator.raise_()

        # Reposition step indicator if visible
        if self._step_indicator.isVisible():
            self._position_step_indicator()

        # Reposition info panel if visible
        if self._info_panel.isVisible():
            self._position_info_panel()

        # Reposition article tip panel if visible
        if self._article_tip_panel.isVisible():
            self._position_article_tip_panel()

        # Reposition moiety tip panel if visible
        if self._moiety_tip_panel.isVisible():
            self._position_moiety_tip_panel()

        # Reposition moiety panel if visible
        if self._moiety_panel.isVisible():
            self._position_moiety_panel()

        # Reposition voice indicator if visible
        if self.voice_indicator.isVisible():
            self._position_voice_indicator()

        # Update recipe detail insets so overlay avoids bars (snap, no animation)
        self._update_detail_insets(animate=False)
        self._update_book_insets(animate=False)

    def moveEvent(self, event):
        """Reposition top-level overlays when the window moves."""
        super().moveEvent(event)
        if self.stacked_widget.currentWidget() is self.video_player:
            if self.voice_indicator.isVisible():
                self._position_voice_indicator()
            if self._info_panel.isVisible():
                self._position_info_panel()

    def resizeEvent(self, event):
        """Maintain 16:9 aspect ratio and reposition overlays."""
        super().resizeEvent(event)
        self._position_overlays()
        # Keep bars visible during resize by restarting the hide timer
        if self.stacked_widget.currentWidget() is self.recipe_detail:
            if self.command_bar.isVisible():
                self._autohide_timer.start(self._autohide_timeout_ms)

        if not self.isFullScreen() and not self.is_resizing:
            # Skip geometric maximize detection during fullscreen exit —
            # the animation passes through full-width which would falsely
            # set _is_maximized.  changeEvent handles the state change;
            # _on_fullscreen_exit_done re-checks after animation settles.
            if self._fullscreen_exiting:
                self._enforce_aspect_ratio()
                return
            # Detect maximize/zoom geometrically — works on both platforms
            # regardless of whether isMaximized() reports correctly.
            screen = self.screen()
            if screen and self.width() >= screen.availableGeometry().width():
                self._is_maximized = True
                # Clear any leftover height constraints from the WM
                self.setMinimumHeight(0)
                self.setMaximumHeight(16777215)
                if not self.is_resizing:
                    self._fix_maximize_height()
                self._check_large_mode()
                return
            self._is_maximized = False
            self._check_large_mode()
            self._enforce_aspect_ratio()

    def _fix_maximize_height(self):
        """Correct a too-short maximized window by resizing to fill available height."""
        screen = self.screen()
        if not screen or not self._is_maximized:
            return
        avail = screen.availableGeometry()
        # Compute exact target client height: available height minus the
        # window frame (title bar + borders).  frameGeometry() includes the
        # frame; the difference gives the frame overhead.
        frame_h = self.frameGeometry().height() - self.height()
        target_h = avail.height() - frame_h
        if abs(self.height() - target_h) <= 2:
            return
        self.is_resizing = True
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.resize(self.width(), target_h)
        self.is_resizing = False

    def _enforce_aspect_ratio(self):
        """Apply the 16:9 aspect ratio by adjusting window height.

        Uses setFixedHeight (constraint-based) rather than self.resize()
        so the macOS window server can override during zoom animations.
        Constraints are released after 10ms to allow subsequent resizes.
        """
        if self._is_maximized or self.isFullScreen():
            return
        screen = self.screen()
        if screen and self.width() >= screen.availableGeometry().width():
            self._is_maximized = True
            return
        self.is_resizing = True
        target_height = int(self.width() * self.aspect_ratio)
        if abs(self.height() - target_height) > 2:
            self.setFixedHeight(target_height)
            QTimer.singleShot(10, lambda: self.setMinimumHeight(0))
            QTimer.singleShot(10, lambda: self.setMaximumHeight(16777215))
        self.is_resizing = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            # Detect fullscreen exit — the animation passes through full-width
            # geometry which falsely triggers _is_maximized in resizeEvent.
            old_state = event.oldState()
            if old_state & Qt.WindowFullScreen and not self.isFullScreen():
                self._fullscreen_exiting = True
                self._is_maximized = False
                QTimer.singleShot(600, self._on_fullscreen_exit_done)
            self._check_large_mode()
            current_widget = self.stacked_widget.currentWidget()
            if current_widget and hasattr(current_widget, "on_window_state_change"):
                current_widget.on_window_state_change()

    def _on_fullscreen_exit_done(self):
        """Re-check large mode after fullscreen exit animation settles."""
        self._fullscreen_exiting = False
        self._check_large_mode()

    def _check_large_mode(self) -> None:
        """Detect fullscreen/maximized and apply font + info panel changes.

        Called from resizeEvent (geometric detection), changeEvent (Qt state),
        and after fullscreen exit animation settles.  Deduplicates via
        _large_mode_active so repeated calls with the same state are no-ops.
        """
        large = self.isFullScreen() or self.isMaximized() or self._is_maximized
        if large == self._large_mode_active:
            return
        self._large_mode_active = large
        self._info_panel.set_large_mode(large)
        # Set max font in fullscreen/maximized, restore saved size otherwise
        current = self.recipe_detail.directions_editor._font_size
        if large:
            delta = 24 - current
        else:
            saved_delta = self._settings.value("font_size_delta", 0, type=int)
            target = 14 + saved_delta
            delta = target - current
        if delta != 0:
            self.recipe_detail.adjust_font_size(delta)
            self.book_view.toc_widget.adjust_font_size(delta)
            self.book_view.description_editor.adjust_font_size(delta)
            self._article_tip_panel.adjust_font_size(delta)
            self._moiety_tip_panel.adjust_font_size(delta)


def main():
    """Initialize and run the application."""
    if _is_frozen():
        import multiprocessing
        multiprocessing.freeze_support()
        # Multiprocessing spawns the app executable as a child process
        # (e.g. resource_tracker). Detect and exit early — don't create a GUI.
        if len(sys.argv) > 1 and sys.argv[1] == '-c':
            return

    import logging
    from pathlib import Path

    log_path = LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # On first frozen launch, seed DATA_DIR with the bundled database.
    # Also replace a corrupt/incomplete DB left by a previous failed launch.
    if _is_frozen():
        need_seed = not DB_PATH.exists()
        if not need_seed:
            import sqlite3
            try:
                with sqlite3.connect(str(DB_PATH)) as _con:
                    _con.execute("SELECT 1 FROM recipes LIMIT 1")
            except sqlite3.OperationalError:
                need_seed = True
        if need_seed:
            bundled_db = BUNDLE_DIR / "foodie_moiety.db"
            if bundled_db.exists():
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundled_db, DB_PATH)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, mode="a", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    # Quiet noisy third-party loggers
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("openwakeword").setLevel(logging.WARNING)

    log_root = logging.getLogger(__name__)
    log_root.info("=" * 60)
    log_root.info("App starting (pid=%d, argv=%s, frozen=%s)",
                  os.getpid(), sys.argv, _is_frozen())

    app = FoodieApp(sys.argv)

    # Set app icon (dock icon on macOS, taskbar on Windows)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media", "app_icon.icns")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Set a dark palette so Qt renders context menu icons in white
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#1e1e1e"))
    palette.setColor(QPalette.WindowText, QColor("white"))
    palette.setColor(QPalette.Base, QColor("#1e1e1e"))
    palette.setColor(QPalette.Text, QColor("white"))
    palette.setColor(QPalette.Button, QColor("#2a2a2a"))
    palette.setColor(QPalette.ButtonText, QColor("white"))
    palette.setColor(QPalette.Highlight, QColor("#0078d4"))
    palette.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(palette)

    # Apply schema migrations (adds is_canonical column, etc.)
    ensure_schema_migrations()
    # Seed default tags on first run (idempotent)
    seed_default_tags()

    # Video player initialized empty; videos loaded per-recipe/book
    video_path = None
    window = MainWindow(video_path)
    window.show()

    # Connect deep link handler (macOS: QFileOpenEvent, Windows: argv)
    app.deep_link_received.connect(window._handle_deep_link)

    # Windows: URL scheme passes the URL as a command-line argument
    for arg in sys.argv[1:]:
        if arg.startswith("foodiemoiety://"):
            QTimer.singleShot(0, lambda url=arg: window._handle_deep_link(url))
            break

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
