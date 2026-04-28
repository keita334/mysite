from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import List, Tuple

import numpy as np
import trimesh
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiLineString, Polygon, MultiPolygon


@dataclass
class SliceSettings:
    layer_height: float = 0.2
    initial_layer_height: float = 0.3
    sample_step: float = 0.6

    base_offset: float = 0.8
    amplitude: float = 0.5
    waves: float = 18.0
    phase_start: float = 0.0
    twist_per_layer_deg: float = 2.0

    bed_x: float = 220.0
    bed_y: float = 220.0
    center_on_bed: bool = True
    model_offset_x: float = 0.0
    model_offset_y: float = 0.0

    perimeter_count: int = 2
    infill_density: float = 0.0
    infill_angle_deg: float = 45.0

    support_enable: bool = True
    support_density: float = 0.2
    support_xy_gap: float = 0.4

    line_width: float = 0.45
    extrusion_gain: float = 1.25

    nozzle_temp: int = 200
    bed_temp: int = 60
    fan_speed: int = 100

    travel_feed: float = 4200.0
    print_feed: float = 1800.0
    support_feed: float = 1200.0


@dataclass
class LayerToolpaths:
    z: float
    perimeters: list[np.ndarray]  # each Nx2
    infill: list[np.ndarray]      # each 2x2 segment
    support: list[np.ndarray]     # each 2x2 segment


class WaveSlicer:
    def __init__(self, mesh: trimesh.Trimesh):
        if not isinstance(mesh, trimesh.Trimesh):
            raise TypeError("mesh must be trimesh.Trimesh")
        if mesh.is_empty:
            raise ValueError("mesh is empty")
        self.mesh = mesh
        self._verts = np.asarray(self.mesh.vertices, dtype=float)
        self._faces = np.asarray(self.mesh.faces, dtype=int)
        tri = self._verts[self._faces]  # (F, 3, 3)
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
        c = np.array(poly.exterior.coords[:-1], dtype=float)
        return c

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

    def slice_layers(self, settings: SliceSettings) -> list[LayerToolpaths]:
        min_b, max_b = self.mesh.bounds
        z_min = float(min_b[2])
        z_max = float(max_b[2])

        shift_x, shift_y = self._placement_shift(settings)

        raw: list[tuple[float, Polygon]] = []
        prev = None
        z = z_min + settings.initial_layer_height
        layer_idx = 0

        while z <= z_max + 1e-9:
            outer = self._extract_outer_loop(z)
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
                for i, (p, nrm) in enumerate(zip(sampled, normals)):
                    u = i / n
                    s = math.sin(2.0 * math.pi * settings.waves * u + phase)
                    offset = -settings.base_offset + settings.amplitude * s
                    offset = min(offset, 0.0)
                    wave[i] = p + nrm * offset

                wave[:, 0] += shift_x
                wave[:, 1] += shift_y
                poly = self._to_polygon(wave)
                if poly is not None and poly.area > 1e-6:
                    raw.append((z, poly))

            z += settings.layer_height
            layer_idx += 1

        layers: list[LayerToolpaths] = []
        solids: list[Polygon] = []
        for z, poly in raw:
            perimeters: list[np.ndarray] = []
            ring = poly
            for _ in range(max(1, int(settings.perimeter_count))):
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

            # Infill is intentionally disabled.
            infill: list[np.ndarray] = []

            layers.append(LayerToolpaths(z=z, perimeters=perimeters, infill=infill, support=[]))
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

        with out.open("w", encoding="utf-8") as f:
            f.write("; --- WaveSlicer App G-code ---\n")
            f.write("G21\nG90\nM82\n")
            f.write(f"M140 S{int(settings.bed_temp)}\nM104 S{int(settings.nozzle_temp)}\n")
            f.write("G28\n")
            f.write(f"M190 S{int(settings.bed_temp)}\nM109 S{int(settings.nozzle_temp)}\n")
            f.write("G92 E0\n")

            for li, layer in enumerate(layers):
                z = layer.z
                h = settings.initial_layer_height if li == 0 else settings.layer_height
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
                            e += d * settings.line_width * h * settings.extrusion_gain
                            f.write(f"G1 X{p[0]:.3f} Y{p[1]:.3f} E{e:.5f} F{feed:.0f}\n")
                        prev = p

                for loop in layer.perimeters:
                    extrude_path(loop, settings.print_feed, close=True)
                for seg in layer.infill:
                    extrude_path(seg, settings.print_feed, close=False)
                for seg in layer.support:
                    extrude_path(seg, settings.support_feed, close=False)

            f.write("\nM104 S0\nM140 S0\nM107\nM84\n")
