from __future__ import annotations

from typing import List

import numpy as np
from OpenGL.GL import (
    glBegin,
    glClear,
    glClearColor,
    glColor3f,
    glEnable,
    glEnd,
    glLineWidth,
    glLoadIdentity,
    glMatrixMode,
    glOrtho,
    glRotatef,
    glScalef,
    glTranslatef,
    glVertex3f,
    glViewport,
    GL_COLOR_BUFFER_BIT,
    GL_DEPTH_BUFFER_BIT,
    GL_DEPTH_TEST,
    GL_LINES,
    GL_LINE_STRIP,
    GL_MODELVIEW,
    GL_PROJECTION,
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from waveslicer.core.slicer import LayerToolpaths


class ToolpathGLView(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layers: List[LayerToolpaths] = []
        self.current_layer = 0
        self.bed_x = 220.0
        self.bed_y = 220.0
        self.show_perimeters = True
        self.show_infill = True
        self.show_support = True

        self.rot_x = 55.0
        self.rot_z = -45.0
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._last_pos = QPoint()

    def set_bed(self, bed_x: float, bed_y: float):
        self.bed_x = bed_x
        self.bed_y = bed_y
        self.update()

    def set_layers(self, layers: List[LayerToolpaths]):
        self.layers = layers
        self.current_layer = 0 if not layers else min(self.current_layer, len(layers) - 1)
        self.update()

    def set_layer_index(self, idx: int):
        if not self.layers:
            self.current_layer = 0
        else:
            self.current_layer = max(0, min(idx, len(self.layers) - 1))
        self.update()

    def set_visibility(self, *, perimeters: bool | None = None, infill: bool | None = None, support: bool | None = None):
        if perimeters is not None:
            self.show_perimeters = perimeters
        if infill is not None:
            self.show_infill = infill
        if support is not None:
            self.show_support = support
        self.update()

    def initializeGL(self):
        glClearColor(0.08, 0.09, 0.11, 1.0)
        glEnable(GL_DEPTH_TEST)

    def resizeGL(self, w: int, h: int):
        glViewport(0, 0, w, max(1, h))

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        w = max(1, self.width())
        h = max(1, self.height())
        aspect = w / h

        scene_size = max(self.bed_x, self.bed_y, 200.0)

        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(-scene_size * aspect, scene_size * aspect, -scene_size, scene_size, -4000.0, 4000.0)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glScalef(self.zoom, self.zoom, self.zoom)
        glTranslatef(self.pan_x, self.pan_y, 0.0)
        glRotatef(self.rot_x, 1.0, 0.0, 0.0)
        glRotatef(self.rot_z, 0.0, 0.0, 1.0)
        glTranslatef(-self.bed_x / 2.0, -self.bed_y / 2.0, 0.0)

        self._draw_bed()
        self._draw_layers()

    def _draw_bed(self):
        glLineWidth(1.0)
        glColor3f(0.35, 0.35, 0.38)
        glBegin(GL_LINE_STRIP)
        glVertex3f(0.0, 0.0, 0.0)
        glVertex3f(self.bed_x, 0.0, 0.0)
        glVertex3f(self.bed_x, self.bed_y, 0.0)
        glVertex3f(0.0, self.bed_y, 0.0)
        glVertex3f(0.0, 0.0, 0.0)
        glEnd()

        glColor3f(0.20, 0.20, 0.23)
        glBegin(GL_LINES)
        step = 20.0
        x = 0.0
        while x <= self.bed_x + 1e-6:
            glVertex3f(x, 0.0, 0.0)
            glVertex3f(x, self.bed_y, 0.0)
            x += step
        y = 0.0
        while y <= self.bed_y + 1e-6:
            glVertex3f(0.0, y, 0.0)
            glVertex3f(self.bed_x, y, 0.0)
            y += step
        glEnd()

    def _draw_loop(self, loop: np.ndarray, z: float, close: bool = True):
        if loop is None or len(loop) < 2:
            return
        glBegin(GL_LINE_STRIP)
        for p in loop:
            glVertex3f(float(p[0]), float(p[1]), float(z))
        if close:
            glVertex3f(float(loop[0, 0]), float(loop[0, 1]), float(z))
        glEnd()

    def _draw_seg(self, seg: np.ndarray, z: float):
        if seg is None or len(seg) < 2:
            return
        glBegin(GL_LINES)
        glVertex3f(float(seg[0, 0]), float(seg[0, 1]), float(z))
        glVertex3f(float(seg[1, 0]), float(seg[1, 1]), float(z))
        glEnd()

    def _draw_layers(self):
        if not self.layers:
            return

        last = self.current_layer
        for i, layer in enumerate(self.layers[: last + 1]):
            fade = 0.25 + 0.75 * (i / max(1, last + 1))

            if self.show_perimeters:
                glLineWidth(1.8 if i == last else 1.0)
                glColor3f(0.10 * fade, 0.85 * fade, 1.00 * fade)  # cyan
                for loop in layer.perimeters:
                    self._draw_loop(loop, layer.z, close=True)

            if self.show_infill:
                glLineWidth(1.0)
                glColor3f(0.70 * fade, 0.70 * fade, 0.70 * fade)  # gray
                for seg in layer.infill:
                    self._draw_seg(seg, layer.z)

            if self.show_support:
                glLineWidth(2.0 if i == last else 1.2)
                glColor3f(1.00 * fade, 0.20 * fade, 0.75 * fade)  # magenta
                for seg in layer.support:
                    self._draw_seg(seg, layer.z)

    def mousePressEvent(self, event):
        self._last_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        p = event.position().toPoint()
        dx = p.x() - self._last_pos.x()
        dy = p.y() - self._last_pos.y()
        self._last_pos = p

        if event.buttons() & Qt.LeftButton:
            self.rot_z += dx * 0.5
            self.rot_x += dy * 0.5
            self.update()
        elif event.buttons() & Qt.RightButton:
            self.pan_x += dx * 0.5
            self.pan_y -= dy * 0.5
            self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom *= 1.1
        else:
            self.zoom /= 1.1
        self.zoom = max(0.1, min(10.0, self.zoom))
        self.update()
