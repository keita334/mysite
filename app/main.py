from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import trimesh
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
from PySide6.QtWidgets import (
    QApplication,
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
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon


@dataclass
class SliceSettings:
    layer_height: float = 0.2
    initial_layer_height: float = 0.2
    adaptive_layer_enable: bool = False
    adaptive_layer_min_height: float = 0.08
    adaptive_layer_sensitivity: float = 1.0
    sample_step: float = 0.6

    base_offset: float = 0.8
    amplitude: float = 0.5
    waves: float = 18.0
    phase_start: float = 0.0
    twist_per_layer_deg: float = 2.0

    bed_x: float = 220.0
    bed_y: float = 220.0
    center_on_bed: bool = True
    flip_z: bool = False
    bottom_cut_enable: bool = False
    bottom_cut_diameter_mm: float = 20.0
    model_offset_x: float = 0.0
    model_offset_y: float = 0.0

    perimeter_count: int = 2
    close_bottom: bool = True
    bottom_solid_layers: int = 3
    bottom_solid_spacing_factor: float = 1.00
    bottom_transition_layers: int = 1
    bottom_wave_embed_mm: float = 0.25
    bottom_wave_embed_layers: int = 3
    infill_density: float = 0.0
    infill_angle_deg: float = 45.0

    support_enable: bool = False
    support_density: float = 0.2
    support_xy_gap: float = 0.4

    line_width: float = 0.42
    initial_layer_line_width: float = 0.50
    nozzle_diameter_mm: float = 0.4
    auto_extrusion: bool = True
    filament_diameter_mm: float = 1.75
    extrusion_gain: float = 1.00

    nozzle_temp: int = 220
    bed_temp: int = 55
    fan_speed: int = 100

    travel_feed: float = 9000.0
    print_feed: float = 2400.0
    support_feed: float = 2400.0


@dataclass
class LayerToolpaths:
    z: float
    h: float
    extrusion_width: float | None
    perimeters: list[np.ndarray]
    infill: list[np.ndarray]
    support: list[np.ndarray]


class WaveSlicer:
    def __init__(self, mesh: trimesh.Trimesh):
        if not isinstance(mesh, trimesh.Trimesh):
            raise TypeError("mesh must be trimesh.Trimesh")
        if mesh.is_empty:
            raise ValueError("mesh is empty")
        self.mesh = mesh
        self._verts = np.asarray(self.mesh.vertices, dtype=float)
        self._faces = np.asarray(self.mesh.faces, dtype=int)
        tri = self._verts[self._faces]
        self._tri_zmin = np.min(tri[:, :, 2], axis=1)
        self._tri_zmax = np.max(tri[:, :, 2], axis=1)

    @classmethod
    def from_stl(cls, path: str | Path) -> "WaveSlicer":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        loaded = trimesh.load_mesh(p, process=True)
        if isinstance(loaded, trimesh.Scene):
            parts = tuple(g for g in loaded.dump() if isinstance(g, trimesh.Trimesh))
            mesh = trimesh.util.concatenate(parts)
        else:
            mesh = loaded
        return cls(mesh)

    @staticmethod
    def _poly_area_xy(points: np.ndarray) -> float:
        x = points[:, 0]
        y = points[:, 1]
        return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))

    @staticmethod
    def _resample_closed(points: np.ndarray, step: float) -> np.ndarray:
        p = np.asarray(points, dtype=float)
        if np.linalg.norm(p[0] - p[-1]) < 1e-9:
            p = p[:-1]
        if len(p) < 3:
            return p

        p2 = np.vstack([p, p[0]])
        seg = np.diff(p2, axis=0)
        seg_len = np.linalg.norm(seg, axis=1)
        total = float(np.sum(seg_len))
        if total <= 1e-9:
            return p

        count = max(64, int(total / max(step, 1e-4)))
        d_query = np.linspace(0.0, total, count, endpoint=False)
        out = np.zeros((count, 2), dtype=float)

        acc = np.cumsum(seg_len)
        start = 0.0
        j = 0
        for i, dq in enumerate(d_query):
            while j < len(seg_len) - 1 and dq > acc[j]:
                start = acc[j]
                j += 1
            t = 0.0 if seg_len[j] <= 1e-12 else (dq - start) / seg_len[j]
            out[i] = p2[j] + t * seg[j]
        return out

    @staticmethod
    def _outward_normals(points: np.ndarray) -> np.ndarray:
        n = len(points)
        out = np.zeros((n, 2), dtype=float)
        ccw = WaveSlicer._poly_area_xy(points) > 0.0
        for i in range(n):
            p_prev = points[(i - 1) % n]
            p_next = points[(i + 1) % n]
            t = p_next - p_prev
            tl = np.linalg.norm(t)
            if tl <= 1e-12:
                out[i] = np.array([1.0, 0.0])
                continue
            t = t / tl
            nrm = np.array([t[1], -t[0]]) if ccw else np.array([-t[1], t[0]])
            nl = np.linalg.norm(nrm)
            out[i] = nrm / nl if nl > 1e-12 else np.array([1.0, 0.0])
        return out

    @staticmethod
    def _canonical_loop(points: np.ndarray) -> np.ndarray:
        p = points.copy()
        if WaveSlicer._poly_area_xy(p) < 0:
            p = p[::-1]
        idx = int(np.argmax(p[:, 0] - 1e-6 * p[:, 1]))
        return np.roll(p, -idx, axis=0)

    @staticmethod
    def _align_to_reference(reference: np.ndarray, loop: np.ndarray) -> np.ndarray:
        if len(reference) != len(loop):
            return loop
        tgt = reference[0]
        i0 = int(np.argmin(np.sum((loop - tgt) ** 2, axis=1)))
        a = np.roll(loop, -i0, axis=0)
        rb = loop[::-1]
        i1 = int(np.argmin(np.sum((rb - tgt) ** 2, axis=1)))
        b = np.roll(rb, -i1, axis=0)
        ca = float(np.sum((reference - a) ** 2))
        cb = float(np.sum((reference - b) ** 2))
        return a if ca <= cb else b

    @staticmethod
    def _edge_plane_intersection(p0: np.ndarray, p1: np.ndarray, z: float, eps: float = 1e-9) -> np.ndarray | None:
        d0 = p0[2] - z
        d1 = p1[2] - z
        if abs(d0) <= eps and abs(d1) <= eps:
            return None
        if d0 * d1 > 0.0:
            return None
        denom = d0 - d1
        if abs(denom) <= eps:
            return None
        t = d0 / denom
        if t < -eps or t > 1.0 + eps:
            return None
        return p0 + t * (p1 - p0)

    @staticmethod
    def _build_loops_from_segments(segments: list[tuple[np.ndarray, np.ndarray]], tol: float = 0.02) -> list[np.ndarray]:
        if not segments:
            return []
        nodes: dict[tuple[int, int], np.ndarray] = {}
        adj: dict[tuple[int, int], list[tuple[tuple[int, int], int]]] = {}
        edges: list[tuple[tuple[int, int], tuple[int, int]]] = []

        def key_of(v: np.ndarray) -> tuple[int, int]:
            return (int(round(v[0] / tol)), int(round(v[1] / tol)))

        def node_for(v: np.ndarray) -> tuple[int, int]:
            k = key_of(v)
            if k not in nodes:
                nodes[k] = np.array([v[0], v[1]], dtype=float)
                adj[k] = []
            return k

        for a, b in segments:
            ka = node_for(a)
            kb = node_for(b)
            if ka == kb:
                continue
            eid = len(edges)
            edges.append((ka, kb))
            adj[ka].append((kb, eid))
            adj[kb].append((ka, eid))

        visited: set[int] = set()
        loops: list[np.ndarray] = []

        def pick_next(prev_k, cur_k):
            prev_dir = nodes[cur_k] - nodes[prev_k]
            prev_norm = np.linalg.norm(prev_dir)
            if prev_norm <= 1e-12:
                prev_dir = np.array([1.0, 0.0], dtype=float)
            else:
                prev_dir /= prev_norm
            best = None
            best_score = -1e9
            for cand_k, cand_eid in adj[cur_k]:
                if cand_eid in visited:
                    continue
                cand_dir = nodes[cand_k] - nodes[cur_k]
                cn = np.linalg.norm(cand_dir)
                if cn <= 1e-12:
                    continue
                cand_dir /= cn
                score = float(np.dot(prev_dir, cand_dir))
                if score > best_score:
                    best_score = score
                    best = (cand_k, cand_eid)
            return best

        for sid, (ka, kb) in enumerate(edges):
            if sid in visited:
                continue
            visited.add(sid)
            chain = [ka, kb]
            while True:
                nxt = pick_next(chain[-2], chain[-1])
                if nxt is None:
                    break
                nxt_k, nxt_sid = nxt
                visited.add(nxt_sid)
                chain.append(nxt_k)
                if nxt_k == chain[0]:
                    break
            if len(chain) >= 4 and chain[0] == chain[-1]:
                pts = np.array([nodes[k] for k in chain[:-1]], dtype=float)
                loops.append(pts)
        return loops

    def _extract_outer_loop(self, z: float) -> np.ndarray | None:
        segments: list[tuple[np.ndarray, np.ndarray]] = []
        valid = np.where((self._tri_zmin - 1e-9 <= z) & (z <= self._tri_zmax + 1e-9))[0]
        for tidx in valid:
            i0, i1, i2 = self._faces[tidx]
            tri = [self._verts[i0], self._verts[i1], self._verts[i2]]
            hits: list[np.ndarray] = []
            for e0, e1 in ((0, 1), (1, 2), (2, 0)):
                hp = self._edge_plane_intersection(tri[e0], tri[e1], z)
                if hp is not None:
                    hits.append(hp)
            uniq: list[np.ndarray] = []
            for h in hits:
                if all(np.linalg.norm(h[:2] - u[:2]) > 1e-6 for u in uniq):
                    uniq.append(h)
            if len(uniq) == 2:
                segments.append((uniq[0], uniq[1]))

        loops = self._build_loops_from_segments(segments, tol=0.02)
        if not loops:
            return None
        return max(loops, key=lambda lp: abs(self._poly_area_xy(lp)))

    def _placement_shift(self, settings: SliceSettings) -> Tuple[float, float]:
        shift_x = settings.model_offset_x
        shift_y = settings.model_offset_y
        if settings.center_on_bed:
            min_b, max_b = self.mesh.bounds
            cx = 0.5 * (min_b[0] + max_b[0])
            cy = 0.5 * (min_b[1] + max_b[1])
            shift_x += 0.5 * settings.bed_x - cx
            shift_y += 0.5 * settings.bed_y - cy
        return shift_x, shift_y

    @staticmethod
    def _to_polygon(loop_xy: np.ndarray) -> Polygon | None:
        if loop_xy is None or len(loop_xy) < 3:
            return None
        poly = Polygon(loop_xy)
        if poly.is_empty:
            return None
        poly = poly.buffer(0)
        if poly.is_empty:
            return None
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        if not isinstance(poly, Polygon):
            return None
        return poly

    @staticmethod
    def _poly_exterior_coords(poly: Polygon) -> np.ndarray:
        return np.array(poly.exterior.coords[:-1], dtype=float)

    @staticmethod
    def _iter_polygons(geom):
        if geom is None or geom.is_empty:
            return
        if isinstance(geom, Polygon):
            yield geom
        elif isinstance(geom, MultiPolygon):
            for g in geom.geoms:
                if not g.is_empty:
                    yield g

    @staticmethod
    def _iter_segments(geom):
        if geom is None or geom.is_empty:
            return
        if isinstance(geom, LineString):
            coords = np.array(geom.coords, dtype=float)
            if len(coords) >= 2:
                yield np.array([coords[0], coords[-1]], dtype=float)
        elif isinstance(geom, MultiLineString):
            for g in geom.geoms:
                coords = np.array(g.coords, dtype=float)
                if len(coords) >= 2:
                    yield np.array([coords[0], coords[-1]], dtype=float)
        elif hasattr(geom, "geoms"):
            for g in geom.geoms:
                yield from WaveSlicer._iter_segments(g)

    @staticmethod
    def _make_hatch_segments(region, spacing: float, angle_deg: float) -> list[np.ndarray]:
        if region is None or region.is_empty:
            return []
        spacing = max(0.05, spacing)

        c = region.centroid
        rr = rotate(region, -angle_deg, origin=(c.x, c.y), use_radians=False)
        minx, miny, maxx, maxy = rr.bounds

        segs: list[np.ndarray] = []
        y = miny - spacing
        while y <= maxy + spacing:
            line = LineString([(minx - 10.0, y), (maxx + 10.0, y)])
            inter = rr.intersection(line)
            for s in WaveSlicer._iter_segments(inter):
                g = LineString([(float(s[0, 0]), float(s[0, 1])), (float(s[1, 0]), float(s[1, 1]))])
                g2 = rotate(g, angle_deg, origin=(c.x, c.y), use_radians=False)
                cc = np.array(g2.coords, dtype=float)
                if len(cc) >= 2:
                    segs.append(np.array([cc[0], cc[-1]], dtype=float))
            y += spacing
        return segs

    def _make_concentric_circles(
        self,
        poly: Polygon,
        line_width: float,
        layer_height: float,
        nozzle_diameter: float,
        first_layer: bool = False,
        min_rings: int = 1,
        spacing_factor: float = 0.90,
        max_rings: int = 0,
    ) -> tuple[list[np.ndarray], float]:
        # BambuStudio FillConcentric-style generation:
        # - clamp width to recommended range (0.75~1.5 * nozzle)
        # - use spacing = width - h * (1 - pi/4)
        # - generate inner loops by offset2-like step:
        #   shrink by (distance + min_spacing/2), then grow by min_spacing/2
        if poly is None or poly.is_empty:
            return [], line_width
        # Bottom solid must be fully closed, so ignore interior holes and fill from outer shell.
        if isinstance(poly, Polygon) and len(poly.interiors) > 0:
            poly = Polygon(poly.exterior)

        h = max(0.01, float(layer_height))
        nozzle = max(0.1, float(nozzle_diameter))
        lw_min = 0.75 * nozzle
        lw_max = 1.50 * nozzle
        lw = float(np.clip(max(0.05, line_width), lw_min, lw_max))

        # same formula used by slic3r/bambu flow model
        base_spacing = lw - h * (1.0 - 0.25 * math.pi)
        spacing = max(lw * 0.35, base_spacing * max(0.6, float(spacing_factor)))
        # Bambu first-layer behavior reference: prioritize stable, non-overlapping laid lines.
        if first_layer:
            spacing = max(spacing, lw * 0.95)
        min_spacing = lw

        loops: list[np.ndarray] = []
        # Start from the outer contour itself (outside-in chaining).
        outer0 = self._poly_exterior_coords(poly)
        if len(outer0) >= 3:
            loops.append(outer0)
        # Use geometric center from outer contour as a stable center reference.
        if len(outer0) >= 3:
            center_ref = np.mean(outer0, axis=0)
        else:
            c = poly.centroid
            center_ref = np.array([c.x, c.y], dtype=float)

        ring = poly
        count = 0
        minx, miny, maxx, maxy = poly.bounds
        auto_cap = max(32, int(max(maxx - minx, maxy - miny) / max(spacing, 1e-6)) + 8)
        hard_cap = auto_cap if max_rings <= 0 else min(auto_cap, max(1, int(max_rings)))

        while ring is not None and not ring.is_empty and count < hard_cap:
            # Equivalent to Bambu's offset2_ex(last, -(d + w/2), +w/2)
            ring_next = ring.buffer(-(spacing + 0.5 * min_spacing), join_style=2).buffer(0.5 * min_spacing, join_style=2)
            if ring_next is None or ring_next.is_empty:
                break
            added = False
            for g in self._iter_polygons(ring_next):
                coords = self._poly_exterior_coords(g)
                if len(coords) >= 3:
                    if loops:
                        c = center_ref
                        r_prev = float(np.mean(np.linalg.norm(loops[-1] - c, axis=1)))
                        r_now = float(np.mean(np.linalg.norm(coords - c, axis=1)))
                        if abs(r_prev - r_now) < lw * 0.72:
                            continue
                    loops.append(coords)
                    added = True
            if not added:
                break
            ring = ring_next
            count += 1

        if len(loops) < int(min_rings):
            # Fallback to a single contour if shape is tiny after offset.
            c0 = self._poly_exterior_coords(poly)
            if len(c0) >= 3:
                loops = [c0]
        elif loops:
            # Center completion: ensure there is no unfilled island at the core.
            center = center_ref
            # Use the innermost loop w.r.t. center reference (not simply last appended).
            last = min(loops, key=lambda arr: float(np.mean(np.linalg.norm(arr - center, axis=1))))
            if len(last) >= 3:
                min_d = float(np.min(np.linalg.norm(last - center, axis=1)))
                if min_d > 0.15 * lw:
                    r = min(0.35 * lw, min_d * 0.8)
                    if r > 1e-6:
                        ang = np.linspace(0.0, 2.0 * math.pi, 24, endpoint=False)
                        small = np.stack([center[0] + r * np.cos(ang), center[1] + r * np.sin(ang)], axis=1)
                        loops.append(small.astype(float))

        return loops, lw

    @staticmethod
    def _shape_metric(loop_xy: np.ndarray) -> tuple[float, float]:
        # Equivalent diameter + compactness to work on circles and complex contours.
        area = abs(WaveSlicer._poly_area_xy(loop_xy))
        if area <= 1e-12:
            return 0.0, 0.0
        d_eq = 2.0 * math.sqrt(area / math.pi)
        p = np.vstack([loop_xy, loop_xy[0]])
        perim = float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1)))
        compact = (4.0 * math.pi * area / (perim * perim)) if perim > 1e-12 else 0.0
        return d_eq, compact

    @staticmethod
    def _equivalent_diameter(loop_xy: np.ndarray) -> float:
        area = abs(WaveSlicer._poly_area_xy(loop_xy))
        if area <= 1e-12:
            return 0.0
        return 2.0 * math.sqrt(area / math.pi)

    def _find_cut_z_by_diameter(self, target_diameter: float, z_min: float, z_max: float, step: float, flip_z: bool) -> float | None:
        if target_diameter <= 0.0:
            return None
        step = max(0.02, step)
        z = z_min + step
        best_z = None
        best_err = float("inf")
        while z <= z_max + 1e-9:
            query_z = (z_max - (z - z_min)) if flip_z else z
            loop = self._extract_outer_loop(query_z)
            if loop is not None and len(loop) >= 3:
                d = self._equivalent_diameter(loop)
                err = abs(d - target_diameter)
                if err < best_err:
                    best_err = err
                    best_z = z
            z += step
        return best_z

    @staticmethod
    def _adaptive_height(base_h: float, min_h: float, sensitivity: float, prev_metric: tuple[float, float], cur_metric: tuple[float, float]) -> float:
        prev_d, prev_c = prev_metric
        cur_d, cur_c = cur_metric
        if prev_d <= 1e-12 or cur_d <= 1e-12:
            return base_h
        delta_d = abs(cur_d - prev_d) / max(prev_d, 1e-9)
        delta_c = abs(cur_c - prev_c)
        score = delta_d + 0.5 * delta_c
        h = base_h / (1.0 + max(0.0, sensitivity) * score * 8.0)
        return max(min_h, min(base_h, h))

    def slice_layers(self, settings: SliceSettings) -> list[LayerToolpaths]:
        min_b, max_b = self.mesh.bounds
        z_min = float(min_b[2])
        z_max = float(max_b[2])
        z_base_for_output = z_min

        if settings.bottom_cut_enable:
            cut_z = self._find_cut_z_by_diameter(
                target_diameter=max(0.0, settings.bottom_cut_diameter_mm),
                z_min=z_min,
                z_max=z_max,
                step=min(max(settings.layer_height * 0.5, 0.05), 0.5),
                flip_z=settings.flip_z,
            )
            if cut_z is not None:
                z_min = max(z_min, cut_z)
                # When bottom-cut is enabled, place the cut plane on bed (Z=0).
                z_base_for_output = z_min

        shift_x, shift_y = self._placement_shift(settings)

        raw: list[tuple[float, float, Polygon, Polygon]] = []
        prev = None
        z = z_min + settings.initial_layer_height
        h_cur = settings.initial_layer_height
        layer_idx = 0
        prev_metric: tuple[float, float] | None = None

        while z <= z_max + 1e-9:
            query_z = (z_max - (z - z_min)) if settings.flip_z else z
            outer = self._extract_outer_loop(query_z)
            if outer is not None:
                sampled = self._resample_closed(outer, settings.sample_step)
                sampled = self._canonical_loop(sampled)
                if prev is not None and len(prev) == len(sampled):
                    sampled = self._align_to_reference(prev, sampled)
                prev = sampled.copy()

                normals = self._outward_normals(sampled)
                n = len(sampled)
                phase = settings.phase_start + math.radians(settings.twist_per_layer_deg * layer_idx)

                wave = np.zeros_like(sampled)
                embed = 0.0
                if settings.close_bottom:
                    start = int(settings.bottom_solid_layers)
                    span = max(1, int(settings.bottom_wave_embed_layers))
                    if layer_idx >= start and layer_idx < start + span:
                        t = (layer_idx - start) / span
                        embed = max(0.0, settings.bottom_wave_embed_mm) * (1.0 - t)
                for i, (p, nrm) in enumerate(zip(sampled, normals)):
                    u = i / n
                    s = math.sin(2.0 * math.pi * settings.waves * u + phase)
                    offset = -settings.base_offset + settings.amplitude * s
                    offset = min(offset, 0.0)
                    offset -= embed
                    wave[i] = p + nrm * offset

                base_xy = sampled.copy()
                base_xy[:, 0] += shift_x
                base_xy[:, 1] += shift_y
                base_poly = self._to_polygon(base_xy)

                wave[:, 0] += shift_x
                wave[:, 1] += shift_y
                poly = self._to_polygon(wave)
                if poly is not None and poly.area > 1e-6 and base_poly is not None and base_poly.area > 1e-6:
                    raw.append((z, h_cur, poly, base_poly))

                cur_metric = self._shape_metric(sampled)
                if settings.adaptive_layer_enable and prev_metric is not None:
                    h_next = self._adaptive_height(
                        base_h=settings.layer_height,
                        min_h=max(0.01, settings.adaptive_layer_min_height),
                        sensitivity=settings.adaptive_layer_sensitivity,
                        prev_metric=prev_metric,
                        cur_metric=cur_metric,
                    )
                else:
                    h_next = settings.layer_height
                prev_metric = cur_metric
            else:
                h_next = settings.layer_height

            z += h_next
            h_cur = h_next
            layer_idx += 1

        layers: list[LayerToolpaths] = []
        solids: list[Polygon] = []
        for li, (z, h, poly, base_poly) in enumerate(raw):
            z_out = z - z_base_for_output if settings.bottom_cut_enable else z
            perimeters: list[np.ndarray] = []
            layer_extrusion_width: float | None = None
            base_loops = max(1, int(settings.perimeter_count))
            is_bottom_solid = settings.close_bottom and li < max(0, int(settings.bottom_solid_layers))
            transition_end = max(0, int(settings.bottom_solid_layers)) + max(0, int(settings.bottom_transition_layers))
            is_transition = settings.close_bottom and (li >= max(0, int(settings.bottom_solid_layers))) and (li < transition_end)

            if is_bottom_solid:
                # Bottom fill policy: use pure concentric circles (no sine-wave contour).
                lw_bottom = settings.initial_layer_line_width if li == 0 else settings.line_width
                perimeters, layer_extrusion_width = self._make_concentric_circles(
                    base_poly,
                    lw_bottom,
                    h,
                    settings.nozzle_diameter_mm,
                    first_layer=(li == 0),
                    min_rings=base_loops,
                    spacing_factor=settings.bottom_solid_spacing_factor,
                )
            else:
                ring = poly
                for _ in range(base_loops):
                    if ring is None or ring.is_empty:
                        break
                    for g in self._iter_polygons(ring):
                        coords = self._poly_exterior_coords(g)
                        if len(coords) >= 3:
                            perimeters.append(coords)
                    ring_next = ring.buffer(-settings.line_width, join_style=2)
                    if ring_next.is_empty:
                        break
                    if isinstance(ring_next, MultiPolygon):
                        ring_next = max(ring_next.geoms, key=lambda gg: gg.area)
                    if not isinstance(ring_next, Polygon):
                        break
                    ring = ring_next

                # Transition layers: add concentric rings as interface so upper wave lines can sit on top.
                if is_transition:
                    bridge_rings, bridge_width = self._make_concentric_circles(
                        base_poly,
                        settings.line_width,
                        h,
                        settings.nozzle_diameter_mm,
                        min_rings=max(2, base_loops),
                        spacing_factor=settings.bottom_solid_spacing_factor,
                    )
                    perimeters = bridge_rings + perimeters
                    layer_extrusion_width = bridge_width

            # infill disabled by policy
            infill: list[np.ndarray] = []

            layers.append(
                LayerToolpaths(
                    z=z_out,
                    h=h,
                    extrusion_width=layer_extrusion_width,
                    perimeters=perimeters,
                    infill=infill,
                    support=[],
                )
            )
            solids.append(poly)

        if settings.support_enable and len(layers) >= 2:
            for i in range(1, len(layers)):
                cur = solids[i]
                prev = solids[i - 1]
                unsupported = cur.difference(prev.buffer(settings.support_xy_gap, join_style=2))
                if not unsupported.is_empty:
                    spacing = settings.line_width / max(0.01, settings.support_density)
                    segs = self._make_hatch_segments(unsupported, spacing, settings.infill_angle_deg + 90.0)
                    layers[i].support = segs

        return layers

    @staticmethod
    def layers_bounds(layers: list[LayerToolpaths]) -> tuple[float, float, float, float] | None:
        pts = []
        for layer in layers:
            for loop in layer.perimeters:
                pts.append(loop)
            for seg in layer.infill:
                pts.append(seg)
            for seg in layer.support:
                pts.append(seg)
        if not pts:
            return None
        arr = np.vstack(pts)
        min_x, min_y = np.min(arr, axis=0)
        max_x, max_y = np.max(arr, axis=0)
        return float(min_x), float(min_y), float(max_x), float(max_y)

    def export_gcode(self, output_path: str | Path, settings: SliceSettings, layers: list[LayerToolpaths]) -> None:
        out = Path(output_path)
        e = 0.0
        filament_area = math.pi * (max(0.5, settings.filament_diameter_mm) * 0.5) ** 2

        with out.open("w", encoding="utf-8") as f:
            f.write("; --- WaveSlicer App G-code ---\n")
            f.write("G21\nG90\nM82\n")
            f.write(f"M140 S{int(settings.bed_temp)}\nM104 S{int(settings.nozzle_temp)}\n")
            f.write("G28\n")
            f.write(f"M190 S{int(settings.bed_temp)}\nM109 S{int(settings.nozzle_temp)}\n")
            f.write("\n; --- Side Purge ---\n")
            purge_x1 = 8.0
            purge_x2 = 8.6
            purge_y1 = 8.0
            purge_y2 = max(20.0, settings.bed_y - 8.0)
            purge_z = max(0.20, settings.initial_layer_height)
            purge_f_travel = max(300.0, settings.travel_feed)
            purge_f_print = max(300.0, settings.print_feed * 0.65)
            f.write("G92 E0\n")
            f.write(f"G1 Z2.0 F{purge_f_travel:.0f}\n")
            f.write(f"G0 X{purge_x1:.2f} Y{purge_y1:.2f} F{purge_f_travel:.0f}\n")
            f.write(f"G1 Z{purge_z:.3f} F{purge_f_travel:.0f}\n")
            f.write(f"G1 X{purge_x1:.2f} Y{purge_y2:.2f} E10.0 F{purge_f_print:.0f}\n")
            f.write(f"G0 X{purge_x2:.2f} Y{purge_y2:.2f} F{purge_f_travel:.0f}\n")
            f.write(f"G1 X{purge_x2:.2f} Y{purge_y1:.2f} E20.0 F{purge_f_print:.0f}\n")
            f.write(f"G1 Z2.0 F{purge_f_travel:.0f}\n")
            f.write("G92 E0\n")

            for li, layer in enumerate(layers):
                z = layer.z
                h = layer.h
                lw = layer.extrusion_width if layer.extrusion_width is not None else settings.line_width
                f.write(f"\n; LAYER:{li} Z:{z:.3f}\n")

                def extrude_path(points: np.ndarray, feed: float, close: bool):
                    nonlocal e
                    if len(points) < 2:
                        return
                    first = points[0]
                    f.write(f"G0 X{first[0]:.3f} Y{first[1]:.3f} Z{z:.3f} F{settings.travel_feed:.0f}\n")
                    seq = np.vstack([points[1:], points[0]]) if close else points[1:]
                    prev = first
                    for p in seq:
                        d = float(np.linalg.norm(p - prev))
                        if d > 1e-9:
                            if settings.auto_extrusion:
                                # Rounded-rectangle approximation used in slicer flow models.
                                line_area = h * (lw - h * (1.0 - 0.25 * math.pi))
                                if line_area <= 1e-9:
                                    line_area = h * max(0.05, lw * 0.5)
                                e += (d * line_area / filament_area) * settings.extrusion_gain
                            else:
                                e += d * lw * h * settings.extrusion_gain
                            f.write(f"G1 X{p[0]:.3f} Y{p[1]:.3f} E{e:.5f} F{feed:.0f}\n")
                        prev = p

                for loop in layer.perimeters:
                    extrude_path(loop, settings.print_feed, close=True)
                for seg in layer.infill:
                    extrude_path(seg, settings.print_feed, close=False)
                for seg in layer.support:
                    extrude_path(seg, settings.support_feed, close=False)

            f.write("\nM104 S0\nM140 S0\nM107\nM84\n")


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
                glColor3f(0.10 * fade, 0.85 * fade, 1.00 * fade)
                for loop in layer.perimeters:
                    self._draw_loop(loop, layer.z, close=True)

            if self.show_infill:
                glLineWidth(1.0)
                glColor3f(0.70 * fade, 0.70 * fade, 0.70 * fade)
                for seg in layer.infill:
                    self._draw_seg(seg, layer.z)

            if self.show_support:
                glLineWidth(2.0 if i == last else 1.2)
                glColor3f(1.00 * fade, 0.20 * fade, 0.75 * fade)
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
        self.zoom = self.zoom * 1.1 if delta > 0 else self.zoom / 1.1
        self.zoom = max(0.1, min(10.0, self.zoom))
        self.update()


class MainWindow(QMainWindow):
    BAMBU_BASE_DEFAULTS = {
        "nozzle_diameter_mm": 0.4,
        "layer_height": 0.2,
        "initial_layer_height": 0.2,
        "line_width": 0.42,
        "initial_layer_line_width": 0.50,
        "adaptive_layer_enable": False,
        "auto_extrusion": True,
        "filament_diameter_mm": 1.75,
        "extrusion_gain": 1.00,
        "nozzle_temp": 220,
        "bed_temp": 55,
        "fan_speed": 100,
        "travel_feed": 9000.0,
        "print_feed": 2400.0,
        "support_feed": 2400.0,
        "perimeter_count": 2,
        "bottom_solid_layers": 3,
        "bottom_transition_layers": 1,
        "support_enable": False,
    }

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
            "apply_bambu_defaults": "Bambu基本値を適用",
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
            "apply_bambu_defaults": "Apply Bambu Defaults",
        },
    }

    FIELD_LABELS = {
        "bed_x": {"ja": "ベッドX", "en": "Bed X"},
        "bed_y": {"ja": "ベッドY", "en": "Bed Y"},
        "center_on_bed": {"ja": "ベッド中央配置", "en": "Center On Bed"},
        "flip_z": {"ja": "底面を上下反転", "en": "Flip Bottom/Top"},
        "bottom_cut_enable": {"ja": "底面カット", "en": "Bottom Cut"},
        "bottom_cut_diameter_mm": {"ja": "底面カット直径(mm)", "en": "Bottom Cut Diameter (mm)"},
        "model_offset_x": {"ja": "モデルXオフセット", "en": "Model Offset X"},
        "model_offset_y": {"ja": "モデルYオフセット", "en": "Model Offset Y"},
        "layer_height": {"ja": "積層ピッチ", "en": "Layer Height"},
        "initial_layer_height": {"ja": "1層目ピッチ", "en": "Initial Layer Height"},
        "adaptive_layer_enable": {"ja": "可変積層ピッチ", "en": "Adaptive Layer Height"},
        "adaptive_layer_min_height": {"ja": "最小積層ピッチ", "en": "Min Layer Height"},
        "adaptive_layer_sensitivity": {"ja": "可変感度", "en": "Adaptive Sensitivity"},
        "sample_step": {"ja": "輪郭サンプル間隔", "en": "Sample Step"},
        "base_offset": {"ja": "基本オフセット", "en": "Base Offset"},
        "amplitude": {"ja": "振幅", "en": "Amplitude"},
        "waves": {"ja": "波の数", "en": "Waves"},
        "phase_start": {"ja": "開始位相", "en": "Phase Start"},
        "twist_per_layer_deg": {"ja": "層ごと位相回転", "en": "Twist / Layer (deg)"},
        "perimeter_count": {"ja": "周回数", "en": "Perimeter Count"},
        "close_bottom": {"ja": "底面を閉じる", "en": "Close Bottom"},
        "bottom_solid_layers": {"ja": "底面ソリッド層", "en": "Bottom Solid Layers"},
        "bottom_solid_spacing_factor": {"ja": "底面同心円間隔係数", "en": "Bottom Circle Spacing"},
        "bottom_transition_layers": {"ja": "底面遷移層", "en": "Bottom Transition Layers"},
        "bottom_wave_embed_mm": {"ja": "波めり込み量(mm)", "en": "Wave Embed (mm)"},
        "bottom_wave_embed_layers": {"ja": "めり込み層数", "en": "Embed Layers"},
        "infill_density": {"ja": "インフィル密度", "en": "Infill Density"},
        "infill_angle_deg": {"ja": "インフィル角度", "en": "Infill Angle"},
        "support_enable": {"ja": "サポート有効", "en": "Enable Support"},
        "support_density": {"ja": "サポート密度", "en": "Support Density"},
        "support_xy_gap": {"ja": "サポートXYギャップ", "en": "Support XY Gap"},
        "line_width": {"ja": "線幅", "en": "Line Width"},
        "initial_layer_line_width": {"ja": "1層目線幅", "en": "Initial Layer Line Width"},
        "nozzle_diameter_mm": {"ja": "ノズル径(mm)", "en": "Nozzle Diameter (mm)"},
        "auto_extrusion": {"ja": "押し出し量を自動計算", "en": "Auto Extrusion"},
        "filament_diameter_mm": {"ja": "フィラメント径(mm)", "en": "Filament Diameter (mm)"},
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
            "adaptive_layer_enable", "adaptive_layer_min_height", "adaptive_layer_sensitivity",
            "line_width", "travel_feed", "print_feed", "support_feed",
            "perimeter_count", "infill_density", "infill_angle_deg",
            "close_bottom", "bottom_solid_layers", "bottom_solid_spacing_factor", "bottom_transition_layers",
            "bottom_wave_embed_mm", "bottom_wave_embed_layers",
            "support_enable", "support_density", "support_xy_gap",
            "nozzle_diameter_mm", "initial_layer_line_width",
            "center_on_bed", "flip_z", "bottom_cut_enable", "bottom_cut_diameter_mm",
        }
        self.filament_keys = {"nozzle_temp", "bed_temp", "fan_speed", "auto_extrusion", "filament_diameter_mm", "extrusion_gain"}

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
        self.btn_bambu_defaults = QPushButton()
        self.btn_slice.setEnabled(False)
        self.btn_export.setEnabled(False)
        top_actions.addWidget(self.btn_open)
        top_actions.addWidget(self.btn_slice)
        top_actions.addWidget(self.btn_export)
        top_actions.addWidget(self.btn_bambu_defaults)
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
        self.btn_bambu_defaults.clicked.connect(self.apply_bambu_defaults)
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
        self.btn_bambu_defaults.setText(self.tr("apply_bambu_defaults"))
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
        cbox("flip_z", False)
        cbox("bottom_cut_enable", False)
        dspin("bottom_cut_diameter_mm", 20.0, 0.1, 10000.0, 0.1, 2)
        dspin("model_offset_x", 0.0, -500.0, 500.0, 0.1, 2)
        dspin("model_offset_y", 0.0, -500.0, 500.0, 0.1, 2)

        dspin("layer_height", 0.2, 0.01, 2.0, 0.01, 3)
        dspin("initial_layer_height", 0.2, 0.01, 2.0, 0.01, 3)
        cbox("adaptive_layer_enable", False)
        dspin("adaptive_layer_min_height", 0.08, 0.01, 1.0, 0.01, 3)
        dspin("adaptive_layer_sensitivity", 1.0, 0.0, 10.0, 0.1, 2)
        dspin("sample_step", 0.6, 0.05, 5.0, 0.05, 3)

        dspin("base_offset", 0.8, 0.0, 10.0, 0.05, 3)
        dspin("amplitude", 0.5, 0.0, 10.0, 0.05, 3)
        dspin("waves", 18.0, 0.1, 300.0, 0.5, 2)
        dspin("phase_start", 0.0, -6.3, 6.3, 0.1, 3)
        dspin("twist_per_layer_deg", 2.0, -360.0, 360.0, 0.1, 2)

        ispin("perimeter_count", 2, 1, 10)
        cbox("close_bottom", True)
        ispin("bottom_solid_layers", 3, 0, 100)
        dspin("bottom_solid_spacing_factor", 1.00, 1.00, 1.60, 0.01, 2)
        ispin("bottom_transition_layers", 1, 0, 20)
        dspin("bottom_wave_embed_mm", 0.25, 0.0, 5.0, 0.01, 3)
        ispin("bottom_wave_embed_layers", 3, 1, 50)
        dspin("infill_density", 0.0, 0.0, 1.0, 0.01, 3)
        dspin("infill_angle_deg", 45.0, 0.0, 180.0, 1.0, 1)
        self.inputs["infill_density"].setEnabled(False)
        self.inputs["infill_angle_deg"].setEnabled(False)

        cbox("support_enable", False)
        dspin("support_density", 0.2, 0.01, 1.0, 0.01, 3)
        dspin("support_xy_gap", 0.4, 0.0, 3.0, 0.05, 3)

        dspin("line_width", 0.42, 0.1, 2.0, 0.01, 3)
        dspin("initial_layer_line_width", 0.50, 0.1, 2.0, 0.01, 3)
        dspin("nozzle_diameter_mm", 0.4, 0.1, 2.0, 0.01, 3)
        cbox("auto_extrusion", True)
        dspin("filament_diameter_mm", 1.75, 1.0, 3.5, 0.01, 3)
        dspin("extrusion_gain", 1.00, 0.1, 5.0, 0.05, 3)

        ispin("nozzle_temp", 220, 0, 350)
        ispin("bed_temp", 55, 0, 150)
        ispin("fan_speed", 100, 0, 100)

        dspin("travel_feed", 9000.0, 0.5, 20000.0, 0.5, 2)
        dspin("print_feed", 2400.0, 0.5, 20000.0, 0.5, 2)
        dspin("support_feed", 2400.0, 0.5, 20000.0, 0.5, 2)

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
        return Path(__file__).resolve().parent / "profiles"

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

    def apply_bambu_defaults(self):
        for k, v in self.BAMBU_BASE_DEFAULTS.items():
            if k in self.inputs:
                self._set_value(self.inputs[k], v)
        if self.slicer is not None and self.chk_auto_reslice.isChecked():
            self.run_slice()

    def collect_settings(self) -> SliceSettings:
        s = SliceSettings()
        for k, w in self.inputs.items():
            setattr(s, k, self._value_of(w))
        # Keep auto extrusion enabled by default policy.
        s.auto_extrusion = True
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
        # Force-enable auto extrusion even when old profiles contain false.
        if "auto_extrusion" in self.inputs:
            self._set_value(self.inputs["auto_extrusion"], True)

    def save_printer_profile(self):
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Printer Profile保存",
            str(self._profile_root() / "printers" / "new_printer.json"),
            "JSON (*.json)",
        )
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
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Filament Profile保存",
            str(self._profile_root() / "filaments" / "new_filament.json"),
            "JSON (*.json)",
        )
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
        path, _ = QFileDialog.getOpenFileName(
            self,
            "STLを選択" if self.lang == "ja" else "Select STL",
            "",
            "STL Files (*.stl)",
        )
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
        path, _ = QFileDialog.getSaveFileName(
            self,
            "G-code保存" if self.lang == "ja" else "Export G-code",
            "output.gcode",
            "G-code (*.gcode)",
        )
        if not path:
            return
        try:
            self.slicer.export_gcode(path, self.collect_settings(), self.layers)
            self.lbl_info.setText(f"状態: 保存完了 {path}" if self.lang == "ja" else f"Status: Saved {path}")
        except Exception as e:
            QMessageBox.critical(self, "保存エラー" if self.lang == "ja" else "Save Error", str(e))


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
