
import os, sys, json, time, argparse, subprocess
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSettings
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QStyle, QFrame, QSizePolicy, QAction, QActionGroup
)

try:
    import vlc
except ImportError:
    print("pip install python-vlc"); sys.exit(1)
except OSError as e:
    if getattr(e, "winerror", None) == 193:
        print("Architecture mismatch: Python and VLC must both be 32-bit or both 64-bit.")
    raise

PROGRESS_FILENAME = "watch_progress.json"

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
              '.m4v', '.mpg', '.mpeg', '.ts', '.m2ts', '.ogv', '.3gp'}
SUBTITLE_FILTER = "Subtitles (*.srt *.ass *.ssa *.vtt *.sub);;All files (*)"
SAVE_EVERY = 5

SCALE_MODES = ("fit", "fill_width", "fill_screen")
SCALE_MODE_LABELS = {
    "fit":         "Fit (letterbox)",
    "fill_width":  "Fill Width",
    "fill_screen": "Fill Screen (stretch)",
}

SETTINGS_ORG = "ReMeMVideoPlayer"
SETTINGS_APP = "ReMeMVideoPlayer"

# ═══════════════════════════════════════════════════════════════════════
# IMAGE ENHANCEMENT PRESET PROFILES
# ═══════════════════════════════════════════════════════════════════════
# Applied as VLC instance arguments at startup. Changing a preset relaunches
# the process (same mechanism as hardware acceleration), so filters are never
# applied to a live Media object mid-playback.
#
# Option-name rules learned the hard way:
#   * Filters in --video-filter are separated by COMMAS: "sharpen,hqdn3d".
#   * The adjust filter's parameters use UNPREFIXED names:
#       --contrast, --brightness, --hue, --saturation, --gamma
#     NOT --adjust-contrast, --adjust-brightness, etc.
#   * Deinterlace uses its own options: --deinterlace=1 and
#     --deinterlace-mode=...  It does NOT go in the --video-filter chain.
# ═══════════════════════════════════════════════════════════════════════

PRESET_PROFILES = {
    "none": {
        "label": "Original (no filters)",
        "description": "No image processing. Fastest, least CPU.",
        "filters": {},
    },
    "light_sharpen": {
        "label": "Light Sharpening",
        "description": "Subtle edge enhancement for slightly soft video.",
        "filters": {
            "sharpen_sigma": 0.08,
        },
    },
    "sd_enhance": {
        "label": "SD Content Enhancement",
        "description": "Sharpening + mild denoise. Best for 480p/576p video.",
        "filters": {
            "sharpen_sigma": 0.16,
            "hqdn3d_luma_spat": 3.0,
            "hqdn3d_chroma_spat": 2.0,
            "hqdn3d_luma_tmp": 3.0,
            "hqdn3d_chroma_tmp": 2.0,
        },
    },
    "heavy_denoise": {
        "label": "Heavy Denoise (Blocky Video)",
        "description": "Aggressive noise reduction + sharpening. For low-bitrate video.",
        "filters": {
            "sharpen_sigma": 0.12,
            "hqdn3d_luma_spat": 8.0,
            "hqdn3d_chroma_spat": 6.0,
            "hqdn3d_luma_tmp": 10.0,
            "hqdn3d_chroma_tmp": 8.0,
        },
    },
    "dvd_interlaced": {
        "label": "DVD / Interlaced",
        "description": "Deinterlaces + sharpens. For DVD rips and TV recordings.",
        "filters": {
            "deinterlace": True,
            "deinterlace_mode": "blend",
            "sharpen_sigma": 0.10,
        },
    },
    "dark_boost": {
        "label": "Dark Video Boost",
        "description": "Raises brightness and gamma for dark or low-contrast video.",
        "filters": {
            "sharpen_sigma": 0.10,
            "adjust": True,
            "adjust_brightness": 1.20,
            "adjust_gamma": 1.30,
            "adjust_contrast": 1.10,
        },
    },
    "vivid_colors": {
        "label": "Vivid Colors",
        "description": "Boosts saturation and contrast for a punchier, TV-like look.",
        "filters": {
            "sharpen_sigma": 0.10,
            "adjust": True,
            "adjust_saturation": 1.30,
            "adjust_contrast": 1.15,
        },
    },
    "cinematic": {
        "label": "Cinematic",
        "description": "Slight contrast and saturation lift with subtle sharpening.",
        "filters": {
            "sharpen_sigma": 0.06,
            "adjust": True,
            "adjust_contrast": 1.08,
            "adjust_saturation": 1.10,
        },
    },
    "vhs_archive": {
        "label": "VHS / Archive",
        "description": "Heavy denoise + sharpen + brightness. For VHS captures.",
        "filters": {
            "sharpen_sigma": 0.14,
            "hqdn3d_luma_spat": 10.0,
            "hqdn3d_chroma_spat": 8.0,
            "hqdn3d_luma_tmp": 12.0,
            "hqdn3d_chroma_tmp": 10.0,
            "adjust": True,
            "adjust_brightness": 1.10,
            "adjust_gamma": 1.15,
        },
    },
    "anime_cartoon": {
        "label": "Anime / Cartoon",
        "description": "Sharp edges + saturation boost. For flat, line-art content.",
        "filters": {
            "sharpen_sigma": 0.20,
            "adjust": True,
            "adjust_saturation": 1.15,
        },
    },
    "cpu_saver": {
        "label": "CPU Saver",
        "description": "Minimal processing. Only mild sharpening.",
        "filters": {
            "sharpen_sigma": 0.06,
        },
    },
}

DEFAULT_PRESET = "none"

DEFAULTS = {
    "scale_mode":          "fit",
    "hw_accel":            "auto",
    "preset":              DEFAULT_PRESET,
    "custom_filters":      "{}",
    "autohide_controls":   True,
    "hide_delay_ms":       2500,
    "skip_interval_ms":    10000,
    "remember_progress":   True,
    "verbose":             False,
}

HW_ACCEL_LABELS = {
    "auto":     "Auto (recommended)",
    "disabled": "Disabled (software decoding)",
    "force":    "Force enable",
}

SKIP_CHOICES_MS  = (5000, 10000, 30000)
HIDE_CHOICES_MS  = (1000, 2500, 5000, 0)

DEBUG = False


def log(*a):
    if DEBUG:
        print("[player]", *a)


def tname(v):
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return str(v)


def fmt(ms):
    ms = max(0, int(ms))
    s = ms // 1000
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"


def settings_obj():
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def get_setting(key):
    default = DEFAULTS.get(key)
    if default is None:
        return None
    s = settings_obj()
    if isinstance(default, bool):
        return s.value(key, default, type=bool)
    if isinstance(default, int):
        return s.value(key, default, type=int)
    return s.value(key, default)


def set_setting(key, value):
    s = settings_obj()
    s.setValue(key, value)
    s.sync()


def save_pending_restore(folder, current_file, fullscreen):
    if not folder:
        return
    data = {
        "folder": folder,
        "current_file": current_file,
        "fullscreen": bool(fullscreen),
    }
    s = settings_obj()
    s.setValue("pending_restore", json.dumps(data))
    s.sync()


def load_pending_restore():
    s = settings_obj()
    raw = s.value("pending_restore", "")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        data = None
    s.remove("pending_restore")
    s.sync()
    return data if isinstance(data, dict) else None


def launch_replacement_process():
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        args = [sys.executable, os.path.abspath(__file__)]
    log("relaunching:", args)
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            args, close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
    else:
        subprocess.Popen(args, close_fds=True, start_new_session=True)


DARK_QSS = """
QMainWindow, QWidget {
    background: #1c1c1e;
    color: #e5e5ea;
    font-family: -apple-system, "SF Pro Text", "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#Transport {
    background: #252528;
    border-top: 1px solid #333;
}
QLabel { background: transparent; }
QLabel#TimeLabel {
    color: #aeaeb2;
    font-variant-numeric: tabular-nums;
    min-width: 110px;
}
QLabel#Header {
    color: #8e8e93;
    padding: 6px 10px;
    font-weight: 600;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    font-size: 11px;
}
QPushButton {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 5px 12px;
    color: #e5e5ea;
}
QPushButton:hover  { background: #3a3a3c; }
QPushButton:pressed{ background: #48484a; }
QPushButton:disabled{ color: #636366; border-color: #2c2c2e; }

QPushButton#IconButton {
    padding: 6px;
    min-width: 30px; max-width: 30px;
    min-height: 30px; max-height: 30px;
    border-radius: 15px;
}
QPushButton#PlayButton {
    background: #0a84ff;
    border: none;
    min-width: 38px; max-width: 38px;
    min-height: 38px; max-height: 38px;
    border-radius: 19px;
    padding: 0;
}
QPushButton#PlayButton:hover   { background: #2b95ff; }
QPushButton#PlayButton:pressed { background: #0066cc; }

QSlider::groove:horizontal {
    height: 4px;
    background: #3a3a3c;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #0a84ff;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #f2f2f7; }

QListWidget {
    background: #1c1c1e;
    border: none;
    outline: none;
    padding: 4px 8px;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 6px;
    color: #d1d1d6;
}
QListWidget::item:hover     { background: #2c2c2e; }
QListWidget::item:selected  { background: #0a84ff; color: #ffffff; }

QMenuBar { background: #1c1c1e; }
QMenuBar::item:selected { background: #0a84ff; border-radius: 5px; }
QMenu {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item            { padding: 5px 22px 5px 22px; border-radius: 5px; }
QMenu::item:selected   { background: #0a84ff; color: #ffffff; }
QMenu::separator       { height: 1px; background: #3a3a3c; margin: 4px 8px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical {
    background: #48484a; border-radius: 5px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #636366; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class VideoWidget(QWidget):
    resized = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background: #000;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class PlayerWindow(QMainWindow):
    def __init__(self, folder=None, autoplay=None, fullscreen=False,
                 scale_mode=None, resume=None):
        super().__init__()
        self.setWindowTitle("Video Player")
        self.resize(1180, 760)
        self.setMinimumSize(720, 480)

        # ─── Persisted options → instance state ───
        self.settings = settings_obj()
        self.scale_mode = get_setting("scale_mode")
        if self.scale_mode not in SCALE_MODES:
            self.scale_mode = "fit"
        self.hw_accel = get_setting("hw_accel")
        if self.hw_accel not in HW_ACCEL_LABELS:
            self.hw_accel = "auto"
        self.preset = get_setting("preset")
        if self.preset not in PRESET_PROFILES and self.preset != "custom":
            self.preset = DEFAULT_PRESET
        try:
            self.custom_filters = json.loads(get_setting("custom_filters"))
            if not isinstance(self.custom_filters, dict):
                self.custom_filters = {}
        except Exception:
            self.custom_filters = {}
        self.autohide_controls = bool(get_setting("autohide_controls"))
        self.hide_delay_ms = int(get_setting("hide_delay_ms"))
        self.skip_interval_ms = int(get_setting("skip_interval_ms"))
        self.resume_enabled = bool(get_setting("remember_progress"))
        if scale_mode in SCALE_MODES:
            self.scale_mode = scale_mode
        if resume is not None:
            self.resume_enabled = bool(resume)

        # ─── VLC engine (built once per process, filter args included) ───
        self.instance, self.player = self._make_vlc_engine()

        # ─── Auto-hide timers ───
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.hide_delay_ms or 1)
        self._hide_timer.timeout.connect(self._hide_controls)

        self._mouse_watch = QTimer(self)
        self._mouse_watch.setInterval(120)
        self._mouse_watch.timeout.connect(self._poll_cursor)
        self._last_cursor_pos = QCursor.pos()
        self._cursor_hidden = False

        self._scale_reapply_timer = QTimer(self)
        self._scale_reapply_timer.setSingleShot(True)
        self._scale_reapply_timer.setInterval(120)
        self._scale_reapply_timer.timeout.connect(self.apply_scale_mode)

        self._last_video_size = (0, 0)

        # ─── Layout ───
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.video = VideoWidget()
        self.video.resized.connect(self._schedule_scale_reapply)
        root.addWidget(self.video, stretch=1)

        transport = QFrame()
        transport.setObjectName("Transport")
        self.transport = transport
        root.addWidget(transport)
        t = QHBoxLayout(transport)
        t.setContentsMargins(12, 8, 12, 8)
        t.setSpacing(8)

        style = self.style()

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setIcon(style.standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.clicked.connect(self.toggle_play)
        t.addWidget(self.play_btn)

        self.back_btn = QPushButton()
        self.back_btn.setObjectName("IconButton")
        self.back_btn.setIcon(style.standardIcon(QStyle.SP_MediaSkipBackward))
        self.back_btn.clicked.connect(lambda: self.seek_relative(-self.skip_interval_ms))
        t.addWidget(self.back_btn)

        self.fwd_btn = QPushButton()
        self.fwd_btn.setObjectName("IconButton")
        self.fwd_btn.setIcon(style.standardIcon(QStyle.SP_MediaSkipForward))
        self.fwd_btn.clicked.connect(lambda: self.seek_relative(+self.skip_interval_ms))
        t.addWidget(self.fwd_btn)

        self.time_label = QLabel("0:00:00 / 0:00:00")
        self.time_label.setObjectName("TimeLabel")
        self.time_label.setAlignment(Qt.AlignCenter)
        t.addWidget(self.time_label)

        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 1000)
        self.progress.sliderMoved.connect(self.on_seek)
        t.addWidget(self.progress, stretch=1)

        self.scale_btn = QPushButton()
        self.scale_btn.setObjectName("IconButton")
        self.scale_btn.setToolTip("Cycle scaling mode (V)")
        self.scale_btn.setText("⤢")
        self.scale_btn.clicked.connect(self.cycle_scale_mode)
        t.addWidget(self.scale_btn)

        self.fullscreen_btn = QPushButton()
        self.fullscreen_btn.setObjectName("IconButton")
        self.fullscreen_btn.setText("⛶")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        t.addWidget(self.fullscreen_btn)

        self.header = QLabel("PLAYLIST")
        self.header.setObjectName("Header")
        root.addWidget(self.header)

        self.playlist = QListWidget()
        self.playlist.setFixedHeight(180)
        self.playlist.itemDoubleClicked.connect(self.on_playlist_click)
        root.addWidget(self.playlist)

        # ─── Menus ───
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction("Open Folder…", self.open_folder, "Ctrl+O")
        file_menu.addAction("Load Subtitle File…", self.load_subtitle_file, "Ctrl+Shift+S")
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close, "Ctrl+Q")

        play_menu = mb.addMenu("&Playback")
        play_menu.addAction("Play / Pause", self.toggle_play, "Space")
        self._back_action = play_menu.addAction(
            f"Back {self.skip_interval_ms // 1000}s",
            lambda: self.seek_relative(-self.skip_interval_ms), "Left")
        self._fwd_action = play_menu.addAction(
            f"Fwd {self.skip_interval_ms // 1000}s",
            lambda: self.seek_relative(+self.skip_interval_ms), "Right")
        play_menu.addSeparator()
        play_menu.addAction("Fullscreen", self.toggle_fullscreen, "F")

        # ─── Video menu ───
        self.video_menu = mb.addMenu("&Video")
        self._scale_group = QActionGroup(self)
        self._scale_group.setExclusive(True)
        self._scale_actions = {}
        for mode in SCALE_MODES:
            act = QAction(SCALE_MODE_LABELS[mode], self)
            act.setCheckable(True)
            act.setChecked(mode == self.scale_mode)
            act.triggered.connect(lambda _=False, m=mode: self.set_scale_mode(m))
            self._scale_group.addAction(act)
            self.video_menu.addAction(act)
            self._scale_actions[mode] = act
        self.video_menu.addSeparator()
        self.video_menu.addAction("Cycle Scaling Mode", self.cycle_scale_mode, "V")

        self.audio_menu = mb.addMenu("&Audio")
        self.sub_menu = mb.addMenu("&Subtitles")

        # ─── Image menu ───
        self.image_menu = mb.addMenu("&Image")
        self._build_image_menu()

        # ─── Tools menu ───
        self._build_tools_menu(mb)

        # ─── Playback state ───
        self.folder = None
        self.file_list = []
        self.current_file = None
        self.progress_data = {}
        self._is_seeking = False
        self._seconds_since_save = 0
        self._fullscreen = False
        self._last_a_count = -1
        self._last_s_count = -1
        self._restoring_tracks = False
        self._polling_for = None
        self._pending_autoplay = None
        self._restart_in_progress = False

        if folder:
            self.folder = folder
            if self.resume_enabled:
                self.load_progress()
            self.refresh_playlist()
        if autoplay:
            self._pending_autoplay = autoplay

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        self.refresh_track_menus()

    # ══════════════════════════════════════════════════════════════════
    # VLC ENGINE — filter args baked in at construction
    # ══════════════════════════════════════════════════════════════════

    def get_active_filters(self):
        if self.preset == "custom":
            return dict(self.custom_filters)
        profile = PRESET_PROFILES.get(self.preset)
        if not profile:
            return {}
        return dict(profile.get("filters", {}))

    def _build_filter_args(self):
        """Convert the active filter dict into VLC CLI arguments.

        Correctness rules:
          * Filters in --video-filter are separated by COMMAS.
          * The adjust filter's parameters use UNPREFIXED names:
              --contrast, --brightness, --hue, --saturation, --gamma
            NOT --adjust-contrast, --adjust-brightness, etc.
          * Deinterlace uses its own dedicated options, not the chain.
        """
        f = self.get_active_filters()
        if not f:
            return []

        args = []
        chain = []

        if f.get("sharpen_sigma"):
            chain.append("sharpen")
        if any(k in f for k in ("hqdn3d_luma_spat", "hqdn3d_chroma_spat",
                                 "hqdn3d_luma_tmp", "hqdn3d_chroma_tmp")):
            chain.append("hqdn3d")
        if f.get("adjust"):
            chain.append("adjust")

        if chain:
            args.append("--video-filter=" + ",".join(chain))

        if f.get("sharpen_sigma"):
            args.append(f"--sharpen-sigma={float(f['sharpen_sigma'])}")

        if "hqdn3d" in chain:
            args.append(f"--hqdn3d-luma-spat={float(f.get('hqdn3d_luma_spat', 4.0))}")
            args.append(f"--hqdn3d-chroma-spat={float(f.get('hqdn3d_chroma_spat', 3.0))}")
            args.append(f"--hqdn3d-luma-temp={float(f.get('hqdn3d_luma_tmp', 6.0))}")
            args.append(f"--hqdn3d-chroma-temp={float(f.get('hqdn3d_chroma_tmp', 4.5))}")

        # Deinterlace: dedicated options, NOT part of --video-filter.
        if f.get("deinterlace"):
            args.append("--deinterlace=1")
            args.append(f"--deinterlace-mode={f.get('deinterlace_mode', 'blend')}")

        if f.get("adjust"):
            # Unprefixed option names. --adjust-contrast does not exist.
            args.append(f"--contrast={float(f.get('adjust_contrast', 1.0))}")
            args.append(f"--brightness={float(f.get('adjust_brightness', 1.0))}")
            args.append(f"--hue={int(f.get('adjust_hue', 0))}")
            args.append(f"--saturation={float(f.get('adjust_saturation', 1.0))}")
            args.append(f"--gamma={float(f.get('adjust_gamma', 1.0))}")

        return args

    def _make_vlc_engine(self):
        """Build the libvlc engine once per process."""
        args = ["--no-video-title-show", "--no-sub-autodetect-file"]
        if self.hw_accel == "disabled":
            args.append("--avcodec-hw=none")
        elif self.hw_accel == "force":
            args.append("--avcodec-hw=any")
        args.extend(self._build_filter_args())
        log("VLC args:", args)

        inst = vlc.Instance(*args)
        if inst is None:
            # libvlc rejected the argument list. Fall back to a bare instance
            # so the app still launches, and print which args were rejected.
            print("warning: VLC rejected the configured arguments; "
                  "launching without them.", file=sys.stderr)
            for a in args:
                print(f"  rejected: {a}", file=sys.stderr)
            # Fall back: try each argument individually to find the culprit
            # by exclusion.
            safe = ["--no-video-title-show", "--no-sub-autodetect-file"]
            inst = vlc.Instance(*safe)
            if inst is None:
                print("error: libvlc could not be initialised at all. "
                      "Check your VLC installation.", file=sys.stderr)
                sys.exit(1)

        plyr = inst.media_player_new()
        if plyr is None:
            print("error: VLC could not create a media player. "
                  "Check your VLC installation.", file=sys.stderr)
            sys.exit(1)
        return inst, plyr

    # ══════════════════════════════════════════════════════════════════
    # IMAGE MENU
    # ══════════════════════════════════════════════════════════════════

    def _build_image_menu(self):
        menu = self.image_menu
        menu.clear()
        self._preset_group = QActionGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_actions = {}

        for key, profile in PRESET_PROFILES.items():
            act = QAction(profile["label"], self)
            act.setCheckable(True)
            act.setChecked(key == self.preset)
            act.setToolTip(profile.get("description", ""))
            act.triggered.connect(lambda _=False, k=key: self.set_preset(k))
            self._preset_group.addAction(act)
            menu.addAction(act)
            self._preset_actions[key] = act

        menu.addSeparator()

        custom_act = QAction("Custom (individual overrides)", self)
        custom_act.setCheckable(True)
        custom_act.setChecked(self.preset == "custom")
        custom_act.triggered.connect(lambda: self.set_preset("custom"))
        self._preset_group.addAction(custom_act)
        menu.addAction(custom_act)
        self._preset_actions["custom"] = custom_act

        menu.addSeparator()
        menu.addAction("Clear Custom Overrides", self.clear_custom_filters)

        menu.addSeparator()
        sub_sharpen = menu.addMenu("Sharpen strength")
        for sigma, label in [(0.0, "Off"), (0.06, "Subtle (0.06)"),
                             (0.10, "Light (0.10)"), (0.16, "Medium (0.16)"),
                             (0.22, "Strong (0.22)")]:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(abs(self.custom_filters.get("sharpen_sigma", -1) - sigma) < 0.001)
            a.triggered.connect(lambda _=False, s=sigma: self.set_custom_filter("sharpen_sigma", s))
            sub_sharpen.addAction(a)

        sub_denoise = menu.addMenu("Denoise strength")
        for label, vals in [("Off", (0, 0, 0, 0)),
                            ("Mild", (3, 2, 3, 2)),
                            ("Medium", (6, 4, 8, 6)),
                            ("Strong", (10, 8, 12, 10))]:
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(
                lambda _=False, v=vals: self._set_denoise_level(v))
            sub_denoise.addAction(a)

    # ══════════════════════════════════════════════════════════════════
    # SETTING CHANGERS THAT TRIGGER A RESTART
    # ══════════════════════════════════════════════════════════════════

    def _restart_to_apply(self):
        """Persist the current session and relaunch the process. Used by any
        setting that maps to a libvlc instance argument."""
        if self._restart_in_progress:
            return
        self.save_current_progress()
        save_pending_restore(self.folder, self.current_file, self._fullscreen)
        self._restart_in_progress = True
        try:
            launch_replacement_process()
        except Exception as e:
            log("relaunch failed:", e)
            self._restart_in_progress = False
            return
        QTimer.singleShot(50, self.close)

    def set_hw_accel(self, key):
        if key not in HW_ACCEL_LABELS:
            return
        if key == self.hw_accel:
            return
        if self._restart_in_progress:
            return
        set_setting("hw_accel", key)
        self.hw_accel = key
        self._hw_actions[key].setChecked(True)
        log(f"hw_accel -> {key}; restarting")
        self._restart_to_apply()

    def set_preset(self, key):
        if key not in PRESET_PROFILES and key != "custom":
            return
        if key == self.preset:
            return
        if self._restart_in_progress:
            return
        self.preset = key
        set_setting("preset", key)
        if key in self._preset_actions:
            self._preset_actions[key].setChecked(True)
        log(f"preset -> {key}; restarting")
        self._restart_to_apply()

    def set_custom_filter(self, filter_key, value):
        if self._restart_in_progress:
            return
        self.custom_filters[filter_key] = value
        set_setting("custom_filters", json.dumps(self.custom_filters))
        self.preset = "custom"
        set_setting("preset", "custom")
        if "custom" in self._preset_actions:
            self._preset_actions["custom"].setChecked(True)
        log(f"custom filter {filter_key}={value}; restarting")
        self._restart_to_apply()

    def _set_denoise_level(self, vals):
        if self._restart_in_progress:
            return
        ls, cs, lt, ct = vals
        if ls == 0:
            for k in ("hqdn3d_luma_spat", "hqdn3d_chroma_spat",
                      "hqdn3d_luma_tmp", "hqdn3d_chroma_tmp"):
                self.custom_filters.pop(k, None)
        else:
            self.custom_filters["hqdn3d_luma_spat"] = ls
            self.custom_filters["hqdn3d_chroma_spat"] = cs
            self.custom_filters["hqdn3d_luma_tmp"] = lt
            self.custom_filters["hqdn3d_chroma_tmp"] = ct
        set_setting("custom_filters", json.dumps(self.custom_filters))
        self.preset = "custom"
        set_setting("preset", "custom")
        if "custom" in self._preset_actions:
            self._preset_actions["custom"].setChecked(True)
        log(f"custom denoise -> {vals}; restarting")
        self._restart_to_apply()

    def clear_custom_filters(self):
        if self._restart_in_progress:
            return
        self.custom_filters = {}
        set_setting("custom_filters", "{}")
        self.preset = DEFAULT_PRESET
        set_setting("preset", DEFAULT_PRESET)
        if DEFAULT_PRESET in self._preset_actions:
            self._preset_actions[DEFAULT_PRESET].setChecked(True)
        log("custom filters cleared; restarting")
        self._restart_to_apply()

    # ══════════════════════════════════════════════════════════════════
    # TOOLS MENU
    # ══════════════════════════════════════════════════════════════════

    def _build_tools_menu(self, menu_bar):
        tools = menu_bar.addMenu("&Tools")

        hw_menu = tools.addMenu("Hardware Acceleration")
        self._hw_group = QActionGroup(self)
        self._hw_group.setExclusive(True)
        self._hw_actions = {}
        for key in ("auto", "disabled", "force"):
            act = QAction(HW_ACCEL_LABELS[key], self)
            act.setCheckable(True)
            act.setChecked(key == self.hw_accel)
            act.triggered.connect(lambda _=False, k=key: self.set_hw_accel(k))
            self._hw_group.addAction(act)
            hw_menu.addAction(act)
            self._hw_actions[key] = act
        tools.addSeparator()

        fs_menu = tools.addMenu("Fullscreen")
        self._autohide_action = QAction("Auto-hide controls", self)
        self._autohide_action.setCheckable(True)
        self._autohide_action.setChecked(self.autohide_controls)
        self._autohide_action.toggled.connect(self.set_autohide)
        fs_menu.addAction(self._autohide_action)

        delay_menu = fs_menu.addMenu("Hide delay")
        self._delay_group = QActionGroup(self)
        self._delay_group.setExclusive(True)
        self._delay_actions = {}
        for ms in HIDE_CHOICES_MS:
            label = "Never" if ms == 0 else f"{ms/1000:g} seconds"
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(ms == self.hide_delay_ms)
            act.triggered.connect(lambda _=False, v=ms: self.set_hide_delay(v))
            self._delay_group.addAction(act)
            delay_menu.addAction(act)
            self._delay_actions[ms] = act
        tools.addSeparator()

        skip_menu = tools.addMenu("Skip interval")
        self._skip_group = QActionGroup(self)
        self._skip_group.setExclusive(True)
        self._skip_actions = {}
        for ms in SKIP_CHOICES_MS:
            act = QAction(f"{ms//1000} seconds", self)
            act.setCheckable(True)
            act.setChecked(ms == self.skip_interval_ms)
            act.triggered.connect(lambda _=False, v=ms: self.set_skip_interval(v))
            self._skip_group.addAction(act)
            skip_menu.addAction(act)
            self._skip_actions[ms] = act
        tools.addSeparator()

        self._remember_action = QAction("Remember playback progress", self)
        self._remember_action.setCheckable(True)
        self._remember_action.setChecked(self.resume_enabled)
        self._remember_action.toggled.connect(self.set_remember_progress)
        tools.addAction(self._remember_action)

        self._verbose_action = QAction("Verbose logging", self)
        self._verbose_action.setCheckable(True)
        self._verbose_action.setChecked(bool(get_setting("verbose")))
        self._verbose_action.toggled.connect(self.set_verbose)
        tools.addAction(self._verbose_action)

        tools.addSeparator()
        tools.addAction("Reset to Defaults", self.reset_to_defaults)

    def set_autohide(self, enabled):
        self.autohide_controls = bool(enabled)
        set_setting("autohide_controls", self.autohide_controls)
        if not self.autohide_controls:
            self._hide_timer.stop()
            if self._fullscreen:
                self.transport.setVisible(True)
                self._show_cursor()
        elif self._fullscreen:
            self._show_controls_temporarily()

    def set_hide_delay(self, ms):
        self.hide_delay_ms = int(ms)
        set_setting("hide_delay_ms", self.hide_delay_ms)
        self._hide_timer.setInterval(self.hide_delay_ms or 1)
        if ms in self._delay_actions:
            self._delay_actions[ms].setChecked(True)
        if self._fullscreen and self.autohide_controls:
            self._show_controls_temporarily()

    def set_skip_interval(self, ms):
        self.skip_interval_ms = int(ms)
        set_setting("skip_interval_ms", self.skip_interval_ms)
        if ms in self._skip_actions:
            self._skip_actions[ms].setChecked(True)
        secs = self.skip_interval_ms // 1000
        self._back_action.setText(f"Back {secs}s")
        self._fwd_action.setText(f"Fwd {secs}s")

    def set_remember_progress(self, enabled):
        self.resume_enabled = bool(enabled)
        set_setting("remember_progress", self.resume_enabled)
        if self.resume_enabled and self.folder:
            self.load_progress()
            self.refresh_playlist()

    def set_verbose(self, enabled):
        global DEBUG
        DEBUG = bool(enabled)
        set_setting("verbose", DEBUG)
        if DEBUG:
            log("verbose logging enabled")

    def reset_to_defaults(self):
        if self._restart_in_progress:
            return
        for k, v in DEFAULTS.items():
            set_setting(k, v)
        self._restart_to_apply()

    # ══════════════════════════════════════════════════════════════════
    # POST-SHOW BOOTSTRAP
    # ══════════════════════════════════════════════════════════════════

    def begin(self, fullscreen=False):
        if fullscreen and not self._fullscreen:
            self.toggle_fullscreen()
        if self._pending_autoplay:
            name = self._pending_autoplay
            self._pending_autoplay = None
            path = os.path.join(self.folder, name) if self.folder else name
            if os.path.isfile(path):
                self.play_video(path, name)
            else:
                log(f"autoplay target not found: {path}")

    # ══════════════════════════════════════════════════════════════════
    # SCALING MODES (live; not an instance arg)
    # ══════════════════════════════════════════════════════════════════

    def set_scale_mode(self, mode):
        if mode not in SCALE_MODES:
            return
        self.scale_mode = mode
        if mode in self._scale_actions:
            self._scale_actions[mode].setChecked(True)
        set_setting("scale_mode", mode)
        self.apply_scale_mode()

    def cycle_scale_mode(self):
        idx = SCALE_MODES.index(self.scale_mode)
        self.set_scale_mode(SCALE_MODES[(idx + 1) % len(SCALE_MODES)])

    def _schedule_scale_reapply(self):
        self._scale_reapply_timer.start()

    def apply_scale_mode(self):
        if not self.current_file:
            return
        try:
            vw, vh = self.player.video_get_size()
        except Exception:
            vw, vh = 0, 0
        if vw <= 0 or vh <= 0:
            return
        ww, wh = self.video.width(), self.video.height()
        if ww <= 0 or wh <= 0:
            return
        try:
            if self.scale_mode == "fit":
                self.player.video_set_crop_geometry("")
                self.player.video_set_aspect_ratio("")
            elif self.scale_mode == "fill_screen":
                self.player.video_set_crop_geometry("")
                self.player.video_set_aspect_ratio(f"{ww}:{wh}")
            elif self.scale_mode == "fill_width":
                av = vw / vh
                aw = ww / wh
                if av < aw:
                    crop_h = int(round(vw * wh / ww))
                    crop_h = max(1, min(crop_h, vh))
                    y = (vh - crop_h) // 2
                    self.player.video_set_crop_geometry(f"{vw}x{crop_h}+0+{y}")
                    self.player.video_set_aspect_ratio(f"{vw}:{crop_h}")
                else:
                    self.player.video_set_crop_geometry("")
                    self.player.video_set_aspect_ratio("")
        except Exception as e:
            log("apply_scale_mode failed:", e)

    # ══════════════════════════════════════════════════════════════════
    # FULLSCREEN AUTO-HIDE
    # ══════════════════════════════════════════════════════════════════

    def _poll_cursor(self):
        pos = QCursor.pos()
        if pos != self._last_cursor_pos:
            self._last_cursor_pos = pos
            if self._fullscreen and self.autohide_controls:
                self._show_controls_temporarily()

    def _show_controls_temporarily(self):
        if not self._fullscreen or not self.autohide_controls:
            return
        if not self.transport.isVisible():
            self.transport.setVisible(True)
        self._show_cursor()
        self._hide_timer.start(self.hide_delay_ms or 1)

    def _hide_controls(self):
        if not self._fullscreen or not self.autohide_controls:
            return
        self.transport.setVisible(False)
        self._hide_cursor()

    def _hide_cursor(self):
        if not self._cursor_hidden:
            QApplication.setOverrideCursor(Qt.BlankCursor)
            self._cursor_hidden = True

    def _show_cursor(self):
        if self._cursor_hidden:
            QApplication.restoreOverrideCursor()
            self._cursor_hidden = False

    def toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self.menuBar().setVisible(False)
            self.header.setVisible(False)
            self.playlist.setVisible(False)
            self.showFullScreen()
            self._last_cursor_pos = QCursor.pos()
            self._mouse_watch.start()
            if self.autohide_controls:
                self._show_controls_temporarily()
            else:
                self.transport.setVisible(True)
            QTimer.singleShot(120, self.apply_scale_mode)
        else:
            self.menuBar().setVisible(True)
            self.header.setVisible(True)
            self.playlist.setVisible(True)
            self.transport.setVisible(True)
            self._hide_timer.stop()
            self._mouse_watch.stop()
            self._show_cursor()
            self.showNormal()
            QTimer.singleShot(120, self.apply_scale_mode)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self._fullscreen:
            self.toggle_fullscreen()
        else:
            super().keyPressEvent(e)

    # ══════════════════════════════════════════════════════════════════
    # PROGRESS FILE
    # ══════════════════════════════════════════════════════════════════

    def progress_path(self):
        return os.path.join(self.folder, PROGRESS_FILENAME)

    def load_progress(self):
        self.progress_data = {}
        if not self.resume_enabled:
            return
        if self.folder and os.path.exists(self.progress_path()):
            try:
                with open(self.progress_path(), "r", encoding="utf-8") as f:
                    self.progress_data = json.load(f)
            except Exception as e:
                log("Could not read progress:", e)

    def save_progress(self):
        if not self.folder or not self.resume_enabled:
            return
        tmp = self.progress_path() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.progress_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.progress_path())
        except Exception as e:
            log("Could not save progress:", e)

    # ══════════════════════════════════════════════════════════════════
    # FOLDER / PLAYLIST
    # ══════════════════════════════════════════════════════════════════

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pick a folder of videos")
        if not folder:
            return
        self.save_current_progress()
        self.folder = folder
        self.load_progress()
        self.refresh_playlist()

    def refresh_playlist(self):
        self.playlist.clear()
        self.file_list = []
        if not self.folder:
            return
        for f in sorted(os.listdir(self.folder)):
            if os.path.splitext(f)[1].lower() not in VIDEO_EXTS:
                continue
            self.file_list.append(f)
            entry = self.progress_data.get(f, {})
            pos, dur = entry.get("position_ms", 0), entry.get("duration_ms", 0)
            tag = ""
            if dur > 0:
                pct = int(pos / dur * 100)
                tag = "   ✔" if pct >= 97 else f"   {pct}%"
            QListWidgetItem(f + tag, self.playlist)

    def on_playlist_click(self, item):
        idx = self.playlist.row(item)
        name = self.file_list[idx]
        self.play_video(os.path.join(self.folder, name), name)

    # ══════════════════════════════════════════════════════════════════
    # PLAYBACK
    # ══════════════════════════════════════════════════════════════════

    def play_video(self, path, name):
        self.save_current_progress()
        self.current_file = name
        self._last_a_count = self._last_s_count = -1
        self._last_video_size = (0, 0)
        self._polling_for = name
        log(f"play_video: {name}")

        media = self.instance.media_new(path)
        self.player.set_media(media)
        self.attach_video()
        self.player.play()
        self._set_play_icon(True)

        self._restoring_tracks = True
        QTimer.singleShot(200, lambda: self._poll_for_tracks(name, attempts=40))

    def _poll_for_tracks(self, name, attempts):
        if self._polling_for != name:
            return
        try:
            a_descs = self.player.audio_get_track_description() or []
            s_descs = self.player.video_get_spu_description() or []
        except Exception as e:
            log("poll exception:", e)
            a_descs, s_descs = [], []

        has_real_a = any(tid != -1 for tid, _ in a_descs)
        has_real_s = any(sid != -1 for sid, _ in s_descs)

        log(f"poll [{name}] attempt={attempts} "
            f"a={[(t, tname(n)) for t, n in a_descs]} "
            f"s={[(t, tname(n)) for t, n in s_descs]}")

        if has_real_a or has_real_s or attempts <= 0:
            self.refresh_track_menus()
            self.restore_position()
            QTimer.singleShot(100, self.restore_tracks)
            QTimer.singleShot(150, self.apply_scale_mode)
            return
        QTimer.singleShot(250, lambda: self._poll_for_tracks(name, attempts - 1))

    def attach_video(self):
        handle = int(self.video.winId())
        if sys.platform.startswith("win"):
            self.player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self.player.set_nsobject(handle)
        else:
            self.player.set_xwindow(handle)

    def restore_position(self, attempts=15):
        if not self.current_file:
            return
        dur = self.player.get_length()
        if dur <= 0 and attempts > 0:
            QTimer.singleShot(200, lambda: self.restore_position(attempts - 1))
            return
        entry = self.progress_data.get(self.current_file)
        if not entry:
            return
        pos = entry.get("position_ms", 0)
        if 0 < pos < dur - 5000:
            log(f"restore_position: seeking to {pos}")
            self.player.set_time(int(pos))

    def restore_tracks(self):
        if not self.current_file:
            self._restoring_tracks = False
            return
        entry = self.progress_data.get(self.current_file, {})
        saved_a_id, saved_a_name = entry.get("audio_track_id"), entry.get("audio_track_name")
        saved_s_id, saved_s_name = entry.get("sub_track_id"), entry.get("sub_track_name")
        log(f"restore_tracks: audio=({saved_a_id},{saved_a_name!r}) "
            f"sub=({saved_s_id},{saved_s_name!r})")
        if saved_a_id is None and saved_s_id is None:
            self._restoring_tracks = False
            self.refresh_track_menus()
            return
        a_descs = self.player.audio_get_track_description() or []
        s_descs = self.player.video_get_spu_description() or []
        if saved_a_id is not None:
            tid = self._match_track(a_descs, saved_a_id, saved_a_name)
            if tid is not None:
                log(f"  → audio_set_track({tid})")
                self.player.audio_set_track(tid)
        if saved_s_id is not None:
            sid = self._match_track(s_descs, saved_s_id, saved_s_name)
            if sid is not None:
                log(f"  → video_set_spu({sid})")
                self.player.video_set_spu(sid)
        self._restoring_tracks = False
        self.refresh_track_menus()

    @staticmethod
    def _match_track(descriptions, saved_id, saved_name):
        if saved_name:
            for tid, raw in descriptions:
                if tname(raw) == saved_name:
                    return tid
        if saved_id is not None:
            for tid, _ in descriptions:
                if tid == saved_id:
                    return tid
        return None

    def save_current_progress(self):
        if not self.current_file:
            return
        if not self.resume_enabled:
            return
        try:
            pos, dur = self.player.get_time(), self.player.get_length()
        except Exception:
            return
        if dur <= 0:
            return
        entry = self.progress_data.setdefault(self.current_file, {})
        entry.update({
            "position_ms": pos,
            "duration_ms": dur,
            "last_played": time.time(),
            "completed": pos >= dur - 5000,
        })
        self.save_progress()

    def toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self._set_play_icon(False)
            self.save_current_progress()
        else:
            self.player.play()
            self._set_play_icon(True)

    def _set_play_icon(self, playing):
        icon = (QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay)
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def seek_relative(self, delta_ms):
        if not self.current_file:
            return
        t, dur = self.player.get_time(), self.player.get_length()
        if dur > 0:
            self.player.set_time(max(0, min(dur, t + delta_ms)))

    def on_seek(self, value):
        if self._is_seeking or not self.current_file:
            return
        dur = self.player.get_length()
        if dur > 0:
            self.player.set_time(int(value / 1000 * dur))

    # ══════════════════════════════════════════════════════════════════
    # TRACK MENUS
    # ══════════════════════════════════════════════════════════════════

    def refresh_track_menus(self):
        self.audio_menu.clear()
        try:
            current_a = self.player.audio_get_track()
            descs = self.player.audio_get_track_description() or []
        except Exception:
            descs, current_a = [], -1
        real_a = [(tid, tname(name)) for tid, name in descs if tid != -1]
        if real_a:
            grp = QActionGroup(self); grp.setExclusive(True)
            for tid, raw in descs:
                name = tname(raw)
                label = "Disable" if tid == -1 else (name or f"Track {tid}")
                act = QAction(label, self)
                act.setCheckable(True)
                act.setChecked(tid == current_a)
                act.triggered.connect(
                    lambda _=False, t=tid, n=name: self.set_audio_track(t, n))
                grp.addAction(act)
                self.audio_menu.addAction(act)
        else:
            a = QAction("(no audio tracks)", self); a.setEnabled(False)
            self.audio_menu.addAction(a)
        self.audio_menu.addSeparator()
        self.audio_menu.addAction("Refresh Tracks", self.refresh_track_menus)

        self.sub_menu.clear()
        try:
            current_s = self.player.video_get_spu()
            subs = self.player.video_get_spu_description() or []
        except Exception:
            subs, current_s = [], -1
        real_s = [(sid, tname(name)) for sid, name in subs if sid != -1]
        if real_s:
            grp = QActionGroup(self); grp.setExclusive(True)
            for sid, raw in subs:
                name = tname(raw)
                label = "Disable" if sid == -1 else (name or f"Subtitle {sid}")
                act = QAction(label, self)
                act.setCheckable(True)
                act.setChecked(sid == current_s)
                act.triggered.connect(
                    lambda _=False, s=sid, n=name: self.set_sub_track(s, n))
                grp.addAction(act)
                self.sub_menu.addAction(act)
        else:
            a = QAction("(no embedded subtitles)", self); a.setEnabled(False)
            self.sub_menu.addAction(a)
        self.sub_menu.addSeparator()
        self.sub_menu.addAction("Load Subtitle File…", self.load_subtitle_file)
        self.sub_menu.addAction("Refresh Tracks", self.refresh_track_menus)

    def set_audio_track(self, tid, name):
        log(f"set_audio_track({tid}, {name!r})")
        self.player.audio_set_track(tid)
        if self.current_file:
            entry = self.progress_data.setdefault(self.current_file, {})
            entry["audio_track_id"] = tid
            entry["audio_track_name"] = tname(name)
            self.save_progress()

    def set_sub_track(self, sid, name):
        log(f"set_sub_track({sid}, {name!r})")
        self.player.video_set_spu(sid)
        if self.current_file:
            entry = self.progress_data.setdefault(self.current_file, {})
            entry["sub_track_id"] = sid
            entry["sub_track_name"] = tname(name)
            self.save_progress()

    def load_subtitle_file(self):
        if not self.current_file:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a subtitle file", self.folder or "", SUBTITLE_FILTER)
        if not path:
            return
        ok = self.player.video_set_subtitle_file(path)
        if not ok:
            media = self.instance.media_new(
                os.path.join(self.folder, self.current_file))
            media.add_option(f":sub-file={path}")
            pos = self.player.get_time()
            self.player.set_media(media)
            self.player.play()
            QTimer.singleShot(300, lambda p=pos: self.player.set_time(p))
        entry = self.progress_data.setdefault(self.current_file, {})
        entry["external_sub_file"] = os.path.basename(path)
        self.save_progress()
        QTimer.singleShot(500, self.refresh_track_menus)

    # ══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ══════════════════════════════════════════════════════════════════

    def tick(self):
        if self._restart_in_progress:
            return
        if self.current_file:
            try:
                pos, dur = self.player.get_time(), self.player.get_length()
            except Exception:
                pos, dur = 0, 0
            if dur > 0:
                self._is_seeking = True
                self.progress.setValue(int(pos / dur * 1000))
                self._is_seeking = False
            self.time_label.setText(f"{fmt(pos)} / {fmt(dur)}")
            try:
                vsize = self.player.video_get_size()
            except Exception:
                vsize = (0, 0)
            if vsize != self._last_video_size:
                self._last_video_size = vsize
                if vsize[0] > 0 and vsize[1] > 0:
                    self.apply_scale_mode()
            if self.player.is_playing():
                self._seconds_since_save += 1
                if self._seconds_since_save >= SAVE_EVERY:
                    self._seconds_since_save = 0
                    self.save_current_progress()
            if not self._restoring_tracks:
                try:
                    a_count = self.player.audio_get_track_count()
                    s_count = self.player.video_get_spu_count()
                except Exception:
                    a_count = s_count = -1
                if a_count != self._last_a_count or s_count != self._last_s_count:
                    self._last_a_count, self._last_s_count = a_count, s_count
                    log(f"count-watcher: a={a_count} s={s_count}")
                    self.refresh_track_menus()

    def closeEvent(self, e):
        self.save_current_progress()
        self._show_cursor()
        try:
            if sys.platform.startswith("win"):
                self.player.set_hwnd(0)
            elif sys.platform == "darwin":
                pass
            else:
                self.player.set_xwindow(0)
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.player.release()
        except Exception:
            pass
        try:
            self.instance.release()
        except Exception:
            pass
        super().closeEvent(e)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="ReMeMVideoPlayer",
        description="A VLC-backed video player with per-folder resume "
                    "and image-enhancement presets.")
    p.add_argument("path", nargs="?", default=None,
                   help="Folder of videos, or a single video file to play.")
    p.add_argument("-f", "--fullscreen", action="store_true",
                   help="Start in fullscreen mode.")
    p.add_argument("-p", "--play", default=None,
                   help="Filename to auto-play (relative to folder, or absolute).")
    p.add_argument("--scale", choices=SCALE_MODES, default=None,
                   help="Initial video scaling mode.")
    p.add_argument("--preset", choices=list(PRESET_PROFILES.keys()), default=None,
                   help="Image-enhancement preset to apply on start.")
    p.add_argument("--no-resume", action="store_true",
                   help="Ignore saved progress; neither read nor write it.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose logging (also persists the setting).")
    p.add_argument("--reset-settings", action="store_true",
                   help="Wipe all saved preferences and exit. Use this if a "
                        "bad filter combination prevents normal startup.")
    p.add_argument("--version", action="version",
                   version="ReMeMVideoPlayer 3.2 (PyQt5 / 32-bit capable)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.reset_settings:
        s = settings_obj()
        s.clear()
        s.sync()
        print("Settings cleared.")
        return

    if args.verbose:
        set_setting("verbose", True)

    global DEBUG
    DEBUG = bool(get_setting("verbose"))

    if args.preset:
        set_setting("preset", args.preset)

    pending = load_pending_restore()
    if pending and pending.get("folder"):
        folder = pending["folder"]
        autoplay = pending.get("current_file")
        if pending.get("fullscreen"):
            args.fullscreen = True
        log("resuming after restart:", folder, autoplay)
    else:
        folder = None
        autoplay = None
        if args.path:
            path = os.path.normpath(os.path.abspath(os.path.expanduser(args.path)))
            if os.path.isdir(path):
                folder = path
            elif os.path.isfile(path):
                if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                    folder = os.path.dirname(path)
                    autoplay = os.path.basename(path)
                else:
                    print(f"warning: {path!r} is not a recognised video file; "
                          f"opening its folder instead.", file=sys.stderr)
                    folder = os.path.dirname(path)
            else:
                print(f"error: {path}: no such file or directory", file=sys.stderr)
                sys.exit(2)
        if args.play:
            if not folder:
                print("error: --play requires a folder path", file=sys.stderr)
                sys.exit(2)
            candidate = args.play
            if not os.path.isabs(candidate):
                candidate = os.path.join(folder, candidate)
            if os.path.isfile(candidate):
                autoplay = os.path.basename(candidate)
            else:
                print(f"warning: --play target not found: {args.play}",
                      file=sys.stderr)

    # PyQt5 is Qt 5 — high-DPI scaling must be requested explicitly, and the
    # attributes must be set before the QApplication is constructed.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)

    win = PlayerWindow(
        folder=folder,
        autoplay=autoplay,
        fullscreen=args.fullscreen,
        scale_mode=args.scale,
        resume=False if args.no_resume else None,
    )
    win.show()
    QTimer.singleShot(50, lambda: win.begin(fullscreen=args.fullscreen))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()