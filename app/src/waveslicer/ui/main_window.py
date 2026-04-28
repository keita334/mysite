from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

from waveslicer.core.slicer import SliceSettings, WaveSlicer, LayerToolpaths
from waveslicer.ui.gl_view import ToolpathGLView


class MainWindow(QMainWindow):
    UI_TEXT = {
        "ja": {
            "open_stl": "STLを開く",
            "slice": "スライス",
            "export": "G-code保存",
            "file_none": "STL: 未選択",
            "status_idle": "状態: 待機",
            "legend": "表示色: 外周=シアン / インフィル=グレー / サポート=マゼンタ",
            "auto_reslice": "設定変更時に自動再スライス",
            "show_perimeters": "外周表示",
            "show_infill": "インフィル表示",
            "show_support": "サポート表示",
            "language": "言語",
            "profiles": "Profiles",
            "printer_load": "Printer読込",
            "printer_save": "Printer保存",
            "filament_load": "Filament読込",
            "filament_save": "Filament保存",
            "settings": "Settings",
            "preview_layer": "Preview Layer",
        },
        "en": {
            "open_stl": "Open STL",
            "slice": "Slice",
            "export": "Export G-code",
            "file_none": "STL: Not selected",
            "status_idle": "Status: Idle",
            "legend": "Colors: Perimeter=Cyan / Infill=Gray / Support=Magenta",
            "auto_reslice": "Auto re-slice on setting change",
            "show_perimeters": "Show Perimeters",
            "show_infill": "Show Infill",
            "show_support": "Show Support",
            "language": "Language",
            "profiles": "Profiles",
            "printer_load": "Load Printer",
            "printer_save": "Save Printer",
            "filament_load": "Load Filament",
            "filament_save": "Save Filament",
            "settings": "Settings",
            "preview_layer": "Preview Layer",
        },
    }

    FIELD_LABELS = {
        "bed_x": {"ja": "ベッドX", "en": "Bed X"},
        "bed_y": {"ja": "ベッドY", "en": "Bed Y"},
        "center_on_bed": {"ja": "ベッド中央配置", "en": "Center On Bed"},
        "model_offset_x": {"ja": "モデルXオフセット", "en": "Model Offset X"},
        "model_offset_y": {"ja": "モデルYオフセット", "en": "Model Offset Y"},
        "layer_height": {"ja": "積層ピッチ", "en": "Layer Height"},
        "initial_layer_height": {"ja": "1層目ピッチ", "en": "Initial Layer Height"},
        "sample_step": {"ja": "輪郭サンプル間隔", "en": "Sample Step"},
        "base_offset": {"ja": "基本オフセット", "en": "Base Offset"},
        "amplitude": {"ja": "振幅", "en": "Amplitude"},
        "waves": {"ja": "波の数", "en": "Waves"},
        "phase_start": {"ja": "開始位相", "en": "Phase Start"},
        "twist_per_layer_deg": {"ja": "層ごと位相回転", "en": "Twist / Layer (deg)"},
        "perimeter_count": {"ja": "周回数", "en": "Perimeter Count"},
        "infill_density": {"ja": "インフィル密度", "en": "Infill Density"},
        "infill_angle_deg": {"ja": "インフィル角度", "en": "Infill Angle"},
        "support_enable": {"ja": "サポート有効", "en": "Enable Support"},
        "support_density": {"ja": "サポート密度", "en": "Support Density"},
        "support_xy_gap": {"ja": "サポートXYギャップ", "en": "Support XY Gap"},
        "line_width": {"ja": "線幅", "en": "Line Width"},
        "extrusion_gain": {"ja": "押出ゲイン", "en": "Extrusion Gain"},
        "nozzle_temp": {"ja": "ノズル温度", "en": "Nozzle Temp"},
        "bed_temp": {"ja": "ベッド温度", "en": "Bed Temp"},
        "fan_speed": {"ja": "ファン速度", "en": "Fan Speed"},
        "travel_feed": {"ja": "移動速度", "en": "Travel Feed"},
        "print_feed": {"ja": "造形速度", "en": "Print Feed"},
        "support_feed": {"ja": "サポート速度", "en": "Support Feed"},
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WaveSlicer App")
        self.resize(1480, 920)
        self.lang = "ja"

        self.slicer: WaveSlicer | None = None
        self.layers: List[LayerToolpaths] = []
        self.stl_path: Path | None = None

        self.inputs: Dict[str, QWidget] = {}
        self.printer_keys = {
            "bed_x", "bed_y", "layer_height", "initial_layer_height",
            "line_width", "travel_feed", "print_feed", "support_feed",
            "perimeter_count", "infill_density", "infill_angle_deg",
            "support_enable", "support_density", "support_xy_gap",
            "center_on_bed",
        }
        self.filament_keys = {
            "nozzle_temp", "bed_temp", "fan_speed", "extrusion_gain",
        }

        root = QWidget()
        self.setCentralWidget(root)
        main = QHBoxLayout(root)

        left_panel = QWidget()
        left_wrap = QVBoxLayout(left_panel)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_panel)
        left_scroll.setMinimumWidth(460)
        main.addWidget(left_scroll, 0)

        right_wrap = QVBoxLayout()
        main.addLayout(right_wrap, 1)

        top_actions = QHBoxLayout()
        self.btn_open = QPushButton()
        self.btn_slice = QPushButton()
        self.btn_export = QPushButton()
        self.btn_slice.setEnabled(False)
        self.btn_export.setEnabled(False)
        top_actions.addWidget(self.btn_open)
        top_actions.addWidget(self.btn_slice)
        top_actions.addWidget(self.btn_export)
        left_wrap.addLayout(top_actions)

        self.lbl_file = QLabel()
        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        left_wrap.addWidget(self.lbl_file)
        left_wrap.addWidget(self.lbl_info)
        self.lbl_legend = QLabel()
        left_wrap.addWidget(self.lbl_legend)
        self.chk_auto_reslice = QCheckBox()
        self.chk_auto_reslice.setChecked(True)
        left_wrap.addWidget(self.chk_auto_reslice)
        lang_row = QHBoxLayout()
        self.lbl_language = QLabel()
        self.cmb_language = QComboBox()
        self.cmb_language.addItem("日本語", "ja")
        self.cmb_language.addItem("English", "en")
        lang_row.addWidget(self.lbl_language)
        lang_row.addWidget(self.cmb_language)
        left_wrap.addLayout(lang_row)
        vis_row = QHBoxLayout()
        self.chk_show_perimeters = QCheckBox()
        self.chk_show_infill = QCheckBox()
        self.chk_show_support = QCheckBox()
        self.chk_show_perimeters.setChecked(True)
        self.chk_show_infill.setChecked(True)
        self.chk_show_support.setChecked(True)
        vis_row.addWidget(self.chk_show_perimeters)
        vis_row.addWidget(self.chk_show_infill)
        vis_row.addWidget(self.chk_show_support)
        left_wrap.addLayout(vis_row)

        self._build_profile_box(left_wrap)
        self._build_settings_box(left_wrap)
        self._build_layer_box(left_wrap)

        self.gl = ToolpathGLView()
        right_wrap.addWidget(self.gl)

        self.btn_open.clicked.connect(self.open_stl)
        self.btn_slice.clicked.connect(self.run_slice)
        self.btn_export.clicked.connect(self.export_gcode)
        self.layer_slider.valueChanged.connect(self.layer_spin.setValue)
        self.layer_spin.valueChanged.connect(self.layer_slider.setValue)
        self.layer_spin.valueChanged.connect(self.on_layer_changed)
        self.chk_show_perimeters.stateChanged.connect(self.on_visibility_changed)
        self.chk_show_infill.stateChanged.connect(self.on_visibility_changed)
        self.chk_show_support.stateChanged.connect(self.on_visibility_changed)
        self.cmb_language.currentIndexChanged.connect(self.on_language_changed)

        self._ensure_profile_dirs()
        self.refresh_profile_lists()
        self.apply_language()

    def _build_profile_box(self, parent_layout: QVBoxLayout):
        box = QGroupBox()
        self.box_profiles = box
        layout = QVBoxLayout(box)

        r1 = QHBoxLayout()
        self.cmb_printer = QComboBox()
        self.btn_load_printer = QPushButton()
        self.btn_save_printer = QPushButton()
        r1.addWidget(self.cmb_printer)
        r1.addWidget(self.btn_load_printer)
        r1.addWidget(self.btn_save_printer)
        layout.addLayout(r1)

        r2 = QHBoxLayout()
        self.cmb_filament = QComboBox()
        self.btn_load_filament = QPushButton()
        self.btn_save_filament = QPushButton()
        r2.addWidget(self.cmb_filament)
        r2.addWidget(self.btn_load_filament)
        r2.addWidget(self.btn_save_filament)
        layout.addLayout(r2)

        self.btn_load_printer.clicked.connect(self.load_printer_profile)
        self.btn_save_printer.clicked.connect(self.save_printer_profile)
        self.btn_load_filament.clicked.connect(self.load_filament_profile)
        self.btn_save_filament.clicked.connect(self.save_filament_profile)

        parent_layout.addWidget(box)

    def _build_settings_box(self, parent_layout: QVBoxLayout):
        box = QGroupBox()
        self.box_settings = box
        form = QFormLayout(box)
        self.form = form
        self.form_labels: Dict[str, QLabel] = {}

        def dspin(name: str, value: float, min_v: float, max_v: float, step: float = 0.1, dec: int = 3):
            w = QDoubleSpinBox()
            w.setDecimals(dec)
            w.setRange(min_v, max_v)
            w.setSingleStep(step)
            w.setValue(value)
            lbl = QLabel(name)
            form.addRow(lbl, w)
            self.form_labels[name] = lbl
            self.inputs[name] = w
            w.valueChanged.connect(self.on_settings_changed)

        def ispin(name: str, value: int, min_v: int, max_v: int, step: int = 1):
            w = QSpinBox()
            w.setRange(min_v, max_v)
            w.setSingleStep(step)
            w.setValue(value)
            lbl = QLabel(name)
            form.addRow(lbl, w)
            self.form_labels[name] = lbl
            self.inputs[name] = w
            w.valueChanged.connect(self.on_settings_changed)

        def cbox(name: str, value: bool):
            w = QCheckBox()
            w.setChecked(value)
            lbl = QLabel(name)
            form.addRow(lbl, w)
            self.form_labels[name] = lbl
            self.inputs[name] = w
            w.stateChanged.connect(self.on_settings_changed)

        dspin("bed_x", 220.0, 1.0, 1000.0, 1.0, 2)
        dspin("bed_y", 220.0, 1.0, 1000.0, 1.0, 2)
        cbox("center_on_bed", True)
        dspin("model_offset_x", 0.0, -500.0, 500.0, 0.1, 2)
        dspin("model_offset_y", 0.0, -500.0, 500.0, 0.1, 2)

        dspin("layer_height", 0.2, 0.01, 2.0, 0.01, 3)
        dspin("initial_layer_height", 0.3, 0.01, 2.0, 0.01, 3)
        dspin("sample_step", 0.6, 0.05, 5.0, 0.05, 3)

        dspin("base_offset", 0.8, 0.0, 10.0, 0.05, 3)
        dspin("amplitude", 0.5, 0.0, 10.0, 0.05, 3)
        dspin("waves", 18.0, 0.1, 300.0, 0.5, 2)
        dspin("phase_start", 0.0, -6.3, 6.3, 0.1, 3)
        dspin("twist_per_layer_deg", 2.0, -360.0, 360.0, 0.1, 2)

        ispin("perimeter_count", 2, 1, 10)
        dspin("infill_density", 0.15, 0.0, 1.0, 0.01, 3)
        dspin("infill_angle_deg", 45.0, 0.0, 180.0, 1.0, 1)
        self.inputs["infill_density"].setValue(0.0)
        self.inputs["infill_density"].setEnabled(False)
        self.inputs["infill_angle_deg"].setEnabled(False)

        cbox("support_enable", True)
        dspin("support_density", 0.2, 0.01, 1.0, 0.01, 3)
        dspin("support_xy_gap", 0.4, 0.0, 3.0, 0.05, 3)

        dspin("line_width", 0.45, 0.1, 2.0, 0.01, 3)
        dspin("extrusion_gain", 1.25, 0.1, 5.0, 0.05, 3)

        ispin("nozzle_temp", 200, 0, 350)
        ispin("bed_temp", 60, 0, 150)
        ispin("fan_speed", 100, 0, 100)

        dspin("travel_feed", 4200.0, 100.0, 20000.0, 50.0, 1)
        dspin("print_feed", 1800.0, 100.0, 20000.0, 50.0, 1)
        dspin("support_feed", 1200.0, 100.0, 20000.0, 50.0, 1)

        parent_layout.addWidget(box)

    def _build_layer_box(self, parent_layout: QVBoxLayout):
        layer_box = QGroupBox()
        self.box_layer = layer_box
        layer_layout = QVBoxLayout(layer_box)
        self.layer_slider = QSlider(Qt.Horizontal)
        self.layer_slider.setRange(0, 0)
        self.layer_spin = QSpinBox()
        self.layer_spin.setRange(0, 0)
        layer_layout.addWidget(self.layer_slider)
        layer_layout.addWidget(self.layer_spin)
        parent_layout.addWidget(layer_box)

    def _profile_root(self) -> Path:
        return Path(__file__).resolve().parents[4] / "profiles"

    def tr(self, key: str) -> str:
        return self.UI_TEXT.get(self.lang, self.UI_TEXT["en"]).get(key, key)

    def field_label(self, key: str) -> str:
        m = self.FIELD_LABELS.get(key)
        if not m:
            return key
        return m.get(self.lang, key)

    def on_language_changed(self, *_):
        self.lang = self.cmb_language.currentData() or "ja"
        self.apply_language()

    def apply_language(self):
        self.btn_open.setText(self.tr("open_stl"))
        self.btn_slice.setText(self.tr("slice"))
        self.btn_export.setText(self.tr("export"))
        if self.stl_path is None:
            self.lbl_file.setText(self.tr("file_none"))
        self.lbl_legend.setText(self.tr("legend"))
        self.chk_auto_reslice.setText(self.tr("auto_reslice"))
        self.chk_show_perimeters.setText(self.tr("show_perimeters"))
        self.chk_show_infill.setText(self.tr("show_infill"))
        self.chk_show_support.setText(self.tr("show_support"))
        self.lbl_language.setText(self.tr("language"))
        self.box_profiles.setTitle(self.tr("profiles"))
        self.btn_load_printer.setText(self.tr("printer_load"))
        self.btn_save_printer.setText(self.tr("printer_save"))
        self.btn_load_filament.setText(self.tr("filament_load"))
        self.btn_save_filament.setText(self.tr("filament_save"))
        self.box_settings.setTitle(self.tr("settings"))
        self.box_layer.setTitle(self.tr("preview_layer"))
        for k, lbl in self.form_labels.items():
            lbl.setText(self.field_label(k))
        if not self.lbl_info.text():
            self.lbl_info.setText(self.tr("status_idle"))

    def _ensure_profile_dirs(self):
        (self._profile_root() / "printers").mkdir(parents=True, exist_ok=True)
        (self._profile_root() / "filaments").mkdir(parents=True, exist_ok=True)

    def refresh_profile_lists(self):
        self.cmb_printer.clear()
        self.cmb_filament.clear()
        for p in sorted((self._profile_root() / "printers").glob("*.json")):
            self.cmb_printer.addItem(p.stem)
        for p in sorted((self._profile_root() / "filaments").glob("*.json")):
            self.cmb_filament.addItem(p.stem)

    def _value_of(self, w: QWidget):
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        if isinstance(w, QSpinBox):
            return int(w.value())
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        raise TypeError(type(w))

    def _set_value(self, w: QWidget, v):
        if isinstance(w, QDoubleSpinBox):
            w.setValue(float(v))
        elif isinstance(w, QSpinBox):
            w.setValue(int(v))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(v))

    def collect_settings(self) -> SliceSettings:
        s = SliceSettings()
        for k, w in self.inputs.items():
            setattr(s, k, self._value_of(w))
        return s

    def _dump_profile(self, keys: set[str]) -> dict:
        data = {}
        for k in keys:
            if k in self.inputs:
                data[k] = self._value_of(self.inputs[k])
        return data

    def _load_profile(self, data: dict):
        for k, v in data.items():
            if k in self.inputs:
                self._set_value(self.inputs[k], v)

    def save_printer_profile(self):
        name, ok = QFileDialog.getSaveFileName(self, "Printer Profile保存", str(self._profile_root() / "printers" / "new_printer.json"), "JSON (*.json)")
        if not name:
            return
        path = Path(name)
        path.write_text(json.dumps(self._dump_profile(self.printer_keys), ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_profile_lists()

    def load_printer_profile(self):
        name = self.cmb_printer.currentText()
        if not name:
            return
        path = self._profile_root() / "printers" / f"{name}.json"
        if path.exists():
            self._load_profile(json.loads(path.read_text(encoding="utf-8")))

    def save_filament_profile(self):
        name, ok = QFileDialog.getSaveFileName(self, "Filament Profile保存", str(self._profile_root() / "filaments" / "new_filament.json"), "JSON (*.json)")
        if not name:
            return
        path = Path(name)
        path.write_text(json.dumps(self._dump_profile(self.filament_keys), ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_profile_lists()

    def load_filament_profile(self):
        name = self.cmb_filament.currentText()
        if not name:
            return
        path = self._profile_root() / "filaments" / f"{name}.json"
        if path.exists():
            self._load_profile(json.loads(path.read_text(encoding="utf-8")))

    def open_stl(self):
        path, _ = QFileDialog.getOpenFileName(self, "STLを選択", "", "STL Files (*.stl)")
        if not path:
            return
        try:
            self.slicer = WaveSlicer.from_stl(path)
            self.stl_path = Path(path)
            self.lbl_file.setText(f"STL: {self.stl_path}")
            self.lbl_info.setText("状態: STL読込完了" if self.lang == "ja" else "Status: STL loaded")
            self.btn_slice.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "読込エラー" if self.lang == "ja" else "Load Error", str(e))

    def run_slice(self):
        if self.slicer is None:
            return
        try:
            settings = self.collect_settings()
            self.layers = self.slicer.slice_layers(settings)
            if not self.layers:
                self.lbl_info.setText("状態: パスが生成されませんでした" if self.lang == "ja" else "Status: no toolpath generated")
                return

            self.btn_export.setEnabled(True)
            n = len(self.layers)
            self.layer_slider.setRange(0, n - 1)
            self.layer_spin.setRange(0, n - 1)
            self.layer_spin.setValue(0)

            self.gl.set_bed(settings.bed_x, settings.bed_y)
            self.gl.set_layers(self.layers)
            self.gl.set_layer_index(0)
            self.on_visibility_changed()

            b = self.slicer.layers_bounds(self.layers)
            support_count = sum(len(l.support) for l in self.layers)
            infill_count = sum(len(l.infill) for l in self.layers)
            if self.lang == "ja":
                self.lbl_info.setText(f"状態: {n}層生成 / infill={infill_count} / support={support_count} / bounds={b}")
            else:
                self.lbl_info.setText(f"Status: {n} layers / infill={infill_count} / support={support_count} / bounds={b}")
        except Exception as e:
            QMessageBox.critical(self, "スライスエラー" if self.lang == "ja" else "Slice Error", str(e))

    def on_layer_changed(self, idx: int):
        self.gl.set_layer_index(idx)

    def on_visibility_changed(self, *_):
        self.gl.set_visibility(
            perimeters=self.chk_show_perimeters.isChecked(),
            infill=self.chk_show_infill.isChecked(),
            support=self.chk_show_support.isChecked(),
        )

    def on_settings_changed(self, *_):
        if self.slicer is None:
            return
        if self.chk_auto_reslice.isChecked():
            self.run_slice()
        else:
            txt = self.lbl_info.text()
            if "（設定変更未反映）" not in txt:
                self.lbl_info.setText(txt + " （設定変更未反映）")

    def export_gcode(self):
        if not self.layers or self.slicer is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "G-code保存", "output.gcode", "G-code (*.gcode)")
        if not path:
            return
        try:
            self.slicer.export_gcode(path, self.collect_settings(), self.layers)
            self.lbl_info.setText(f"状態: 保存完了 {path}" if self.lang == "ja" else f"Status: Saved {path}")
        except Exception as e:
            QMessageBox.critical(self, "保存エラー" if self.lang == "ja" else "Save Error", str(e))
