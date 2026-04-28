bl_info = {
    "name": "Custom STL Contour Z-Wave Slicer",
    "author": "Custom",
    "version": (1, 0, 0),
    "blender": (3, 3, 0),
    "location": "View3D > Sidebar > Z-Wave Slicer",
    "description": "Generate Z-wave toolpaths along STL contours",
    "category": "3D View",
}

import math

import bpy
import bmesh
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector
from mathutils.bvhtree import BVHTree


# ---------- geometry helpers ----------
def polygon_area_xy(points):
    area = 0.0
    n = len(points)
    for i in range(n):
        p0 = points[i]
        p1 = points[(i + 1) % n]
        area += p0.x * p1.y - p0.y * p1.x
    return 0.5 * area


def edge_plane_intersection(p0, p1, z, eps=1e-9):
    d0 = p0.z - z
    d1 = p1.z - z
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
    return p0.lerp(p1, t)


def quantize_key(v, tol):
    return (round(v.x / tol), round(v.y / tol))


def build_loops_from_segments(segments, tol=0.02):
    if not segments:
        return []

    nodes = {}
    adj = {}
    edges = []

    def node_for(v):
        k = quantize_key(v, tol)
        if k not in nodes:
            nodes[k] = Vector((v.x, v.y, v.z))
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

    visited = set()
    loops = []

    def pick_next(prev_key, cur_key):
        prev_dir = nodes[cur_key] - nodes[prev_key]
        if prev_dir.length <= 1e-12:
            prev_dir = Vector((1.0, 0.0, 0.0))
        else:
            prev_dir.normalize()

        best = None
        best_score = -1e18
        for cand_key, cand_eid in adj[cur_key]:
            if cand_eid in visited:
                continue
            cand_dir = nodes[cand_key] - nodes[cur_key]
            if cand_dir.length <= 1e-12:
                continue
            cand_dir.normalize()
            score = prev_dir.dot(cand_dir)
            if score > best_score:
                best_score = score
                best = (cand_key, cand_eid)
        return best

    for sid, (ka, kb) in enumerate(edges):
        if sid in visited:
            continue
        visited.add(sid)
        chain = [ka, kb]

        while True:
            cur = chain[-1]
            prev = chain[-2]
            nxt = pick_next(prev, cur)
            if nxt is None:
                break
            nxt_key, nxt_sid = nxt
            visited.add(nxt_sid)
            chain.append(nxt_key)
            if nxt_key == chain[0]:
                break

        if len(chain) >= 4 and chain[0] == chain[-1]:
            loops.append([nodes[k].copy() for k in chain[:-1]])

    return loops


def get_outer_loop(loops):
    if not loops:
        return None
    return max(loops, key=lambda pts: abs(polygon_area_xy(pts)))


def resample_closed_loop(points, target_count):
    if len(points) < 3:
        return points[:]

    src = points[:-1] if (points[0] - points[-1]).length < 1e-6 else points[:]
    n = len(src)
    if n < 3:
        return src

    seg_len = []
    total = 0.0
    for i in range(n):
        d = (src[(i + 1) % n] - src[i]).length
        seg_len.append(d)
        total += d

    if total <= 1e-12:
        return src

    out = []
    for k in range(target_count):
        t = (k / target_count) * total
        acc = 0.0
        idx = 0
        while idx < n and acc + seg_len[idx] < t:
            acc += seg_len[idx]
            idx += 1
        idx = min(idx, n - 1)
        local = 0.0 if seg_len[idx] < 1e-12 else (t - acc) / seg_len[idx]
        out.append(src[idx].lerp(src[(idx + 1) % n], local))
    return out


def rotate_loop(points, shift):
    if not points:
        return points
    n = len(points)
    s = shift % n
    return points[s:] + points[:s]


def canonicalize_loop_phase(points):
    if len(points) < 3:
        return points[:]
    loop = points[:]
    if polygon_area_xy(loop) < 0.0:
        loop.reverse()
    idx = max(range(len(loop)), key=lambda i: (loop[i].x, -loop[i].y))
    return rotate_loop(loop, idx)


def align_loop_start(reference, loop):
    if len(reference) != len(loop):
        return loop

    n = len(loop)
    stride = max(1, n // 64)
    idxs = list(range(0, n, stride))

    def shift_cost(shift):
        c = 0.0
        for j in idxs:
            c += (reference[j] - loop[(j + shift) % n]).length_squared
        return c

    best_shift = min(range(n), key=shift_cost)
    return [loop[(i + best_shift) % n] for i in range(n)]


def safe_normalize(v, fallback):
    if v.length <= 1e-12:
        out = fallback.copy()
        if out.length <= 1e-12:
            return Vector((0.0, 0.0, 1.0))
        out.normalize()
        return out
    out = v.copy()
    out.normalize()
    return out


def nearest_equivalent_u(base_u, target_u, period_u):
    if period_u <= 1e-12:
        return base_u
    k = round((target_u - base_u) / period_u)
    return base_u + k * period_u


def lerp_closed(seq, u):
    n = len(seq)
    if n == 0:
        return None
    uf = u % 1.0
    x = uf * n
    i0 = int(math.floor(x)) % n
    i1 = (i0 + 1) % n
    t = x - math.floor(x)
    return seq[i0].lerp(seq[i1], t)


def eval_wave_point(sampled, wave_dirs, amp, freq, phase_shift, u_offset, u, z_shift=0.0):
    base = lerp_closed(sampled, u)
    d0 = lerp_closed(wave_dirs, u)
    direction = safe_normalize(d0, Vector((0.0, 0.0, 1.0)))
    if base is None:
        return Vector((0.0, 0.0, 0.0))
    if abs(z_shift) > 1e-12:
        base = base + Vector((0.0, 0.0, z_shift))
    if freq <= 1e-12 or amp <= 1e-12:
        return base
    wave = amp * math.sin((2.0 * math.pi * freq * (u + u_offset)) + phase_shift)
    return base + (direction * wave)


def estimate_model_center_xy(layers):
    if not layers:
        return 0.0, 0.0
    acc = Vector((0.0, 0.0, 0.0))
    cnt = 0
    for pts in layers:
        for p in pts:
            acc += p
            cnt += 1
    if cnt <= 0:
        return 0.0, 0.0
    c = acc / cnt
    return c.x, c.y


# ---------- mesh slicing cache ----------
class MeshSliceCache:
    def __init__(self, obj):
        self.deps = bpy.context.evaluated_depsgraph_get()
        self.obj_eval = obj.evaluated_get(self.deps)
        self.mesh = self.obj_eval.to_mesh()
        self.mesh.calc_loop_triangles()
        mw = obj.matrix_world

        self.verts = [mw @ v.co for v in self.mesh.vertices]
        self.tris = [tuple(t.vertices) for t in self.mesh.loop_triangles]
        self.tri_zmin = []
        self.tri_zmax = []
        for i0, i1, i2 in self.tris:
            z0 = self.verts[i0].z
            z1 = self.verts[i1].z
            z2 = self.verts[i2].z
            self.tri_zmin.append(min(z0, z1, z2))
            self.tri_zmax.append(max(z0, z1, z2))

    def clear(self):
        self.obj_eval.to_mesh_clear()

    def section_loops(self, z, tol=0.02):
        segments = []
        for tidx, (i0, i1, i2) in enumerate(self.tris):
            if z < self.tri_zmin[tidx] - 1e-9 or z > self.tri_zmax[tidx] + 1e-9:
                continue
            p = [self.verts[i0], self.verts[i1], self.verts[i2]]
            hits = []
            for e0, e1 in ((0, 1), (1, 2), (2, 0)):
                hp = edge_plane_intersection(p[e0], p[e1], z)
                if hp is not None:
                    hits.append(hp)
            uniq = []
            for h in hits:
                if all((h - u).length >= 1e-6 for u in uniq):
                    uniq.append(h)
            if len(uniq) == 2:
                segments.append((uniq[0], uniq[1]))
        return build_loops_from_segments(segments, tol=tol)


class SurfaceProjector:
    def __init__(self, obj):
        self.deps = bpy.context.evaluated_depsgraph_get()
        self.obj_eval = obj.evaluated_get(self.deps)
        self.mesh = self.obj_eval.to_mesh()
        self.mesh.calc_loop_triangles()
        mw = obj.matrix_world

        self.verts = [mw @ v.co for v in self.mesh.vertices]
        self.tris = [tuple(t.vertices) for t in self.mesh.loop_triangles]
        self.tri_normals = []
        for i0, i1, i2 in self.tris:
            e1 = self.verts[i1] - self.verts[i0]
            e2 = self.verts[i2] - self.verts[i0]
            n = e1.cross(e2)
            n = safe_normalize(n, Vector((0.0, 0.0, 1.0)))
            self.tri_normals.append(n)

        self.bvh = BVHTree.FromPolygons(self.verts, self.tris, all_triangles=True)

    def clear(self):
        self.obj_eval.to_mesh_clear()

    def nearest_point_normal(self, p):
        hit = self.bvh.find_nearest(p)
        if hit is None:
            return p, None
        loc, nrm, idx, _ = hit
        if loc is None:
            return p, None
        if nrm is not None and nrm.length > 1e-12:
            return loc, nrm.normalized()
        if idx is not None and 0 <= idx < len(self.tri_normals):
            return loc, self.tri_normals[idx].copy()
        return loc, None


# ---------- path generation ----------
def calculate_contact_wave_path(props):
    obj = props.target_object
    if obj is None or obj.type != 'MESH':
        return []

    bb = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    z_min = min(v.z for v in bb)
    z_max = max(v.z for v in bb)
    if z_max <= z_min:
        return []

    default_layer_step_z = max(
        0.01,
        (props.z_amplitude * 2.0) + props.filament_thickness - props.contact_overlap,
    )
    min_contact_step_z = max(0.01, props.filament_thickness - props.contact_overlap)

    all_layers = []
    cache = MeshSliceCache(obj)
    projector = SurfaceProjector(obj)
    try:
        prev_loop = None
        prev_wave_dirs = None
        prev_state = None
        prev_peak_u_anchor = None
        prev_valley_u_anchor = None
        next_layer_step_z = default_layer_step_z
        z = z_min + props.initial_layer_height

        for layer_idx in range(props.total_layers):
            if z > z_max + 1e-9:
                break

            loops = cache.section_loops(z, tol=props.join_tolerance)
            outer = get_outer_loop(loops)
            if outer is None:
                z += next_layer_step_z
                continue

            sampled = resample_closed_loop(outer, props.path_steps)
            sampled = canonicalize_loop_phase(sampled)
            if prev_loop is not None:
                sampled = align_loop_start(prev_loop, sampled)
            prev_loop = sampled[:]

            # Crest-valley contact across every adjacent layer pair.
            phase_shift = math.pi * (layer_idx % 2)
            # Keep frequency uniform across layers so crest/valley pairs line up.
            freq = props.z_frequency
            # Optional flat first layer.
            amp = 0.0 if (layer_idx == 0 and props.flat_initial_layer) else props.z_amplitude

            if freq <= 1e-9:
                u_offset = 0.0
            else:
                # Fixed phase origin. Do not include layer phase here,
                # otherwise phase_shift is canceled and crest/valley pairing breaks.
                u_offset = -1.0 / (4.0 * freq)

            # Anchor current layer by previous layer valley:
            # current crest should seat to previous valley branch.
            if prev_valley_u_anchor is not None and freq > 1e-9:
                period_u = 1.0 / freq
                crest_base_u = (0.25 - (phase_shift / (2.0 * math.pi))) / freq
                crest_target_u = nearest_equivalent_u(crest_base_u, prev_valley_u_anchor, period_u)
                # Extremum position is u = base_u - u_offset.
                u_offset = crest_base_u - crest_target_u

            n = len(sampled)
            points = []
            loop_center = sum(sampled, Vector((0.0, 0.0, 0.0))) / max(1, n)
            prev_wave_dir = None
            wave_dirs = []
            for i, p in enumerate(sampled):
                p_prev = sampled[(i - 1) % n]
                p_next = sampled[(i + 1) % n]
                contour_tangent = safe_normalize(p_next - p_prev, Vector((1.0, 0.0, 0.0)))

                _, surf_n = projector.nearest_point_normal(p)
                if surf_n is None:
                    surf_n = Vector((0.0, 0.0, 1.0))

                # Visible wave needs a tangent direction orthogonal to the contour tangent.
                # (Moving along contour tangent only re-parameterizes the same curve.)
                wave_dir_raw = surf_n.cross(contour_tangent)
                wave_dir = safe_normalize(wave_dir_raw, Vector((1.0, 0.0, 0.0)))

                # Prefer outward orientation in XY as a stable sign hint.
                outward_hint = p - loop_center
                outward_hint.z = 0.0
                if outward_hint.length > 1e-9 and wave_dir.dot(outward_hint) < 0.0:
                    wave_dir = -wave_dir

                # Keep local orientation continuous to avoid sign flips.
                if prev_wave_dir is not None and wave_dir.dot(prev_wave_dir) < 0.0:
                    wave_dir = -wave_dir
                prev_wave_dir = wave_dir

                wave_dirs.append(wave_dir)

            # Keep layer-to-layer direction branch consistent to avoid crossings.
            if prev_wave_dirs is not None and len(prev_wave_dirs) == len(wave_dirs):
                dot_sum = 0.0
                for i in range(len(wave_dirs)):
                    dot_sum += wave_dirs[i].dot(prev_wave_dirs[i])
                if dot_sum < 0.0:
                    wave_dirs = [-d for d in wave_dirs]
                # Per-point orientation lock to avoid local branch flips between layers.
                for i in range(len(wave_dirs)):
                    if wave_dirs[i].dot(prev_wave_dirs[i]) < 0.0:
                        wave_dirs[i] = -wave_dirs[i]

            # Optimize phase offset so current crests touch previous valleys.
            if prev_state is not None and freq > 1e-9 and amp > 1e-9:
                period_u = 1.0 / freq
                base_off = u_offset
                prev_phase = prev_state["phase_shift"]
                prev_off = prev_state["u_offset"]
                prev_freq = prev_state["freq"]
                prev_period_u = 1.0 / max(1e-9, prev_freq)
                anchor_off = u_offset

                def contact_score(off):
                    score = 0.0
                    pair_count = min(48, max(8, int(round(freq * 3.0))))
                    for j in range(pair_count):
                        # Previous valley branch anchored explicitly by prev_valley_u_anchor.
                        if prev_valley_u_anchor is not None:
                            u_prev = prev_valley_u_anchor + (j * prev_period_u)
                        else:
                            u_prev = ((0.75 - (prev_phase / (2.0 * math.pi))) / prev_freq) - prev_off + (j / prev_freq)
                        # Current crest (match periodic branch to previous valley)
                        u_curr_base = ((0.25 - (phase_shift / (2.0 * math.pi))) / freq) - off + (j / freq)
                        u_curr = nearest_equivalent_u(u_curr_base, u_prev, period_u)

                        prev_pts = prev_state.get("final_points")
                        if prev_pts:
                            prev_pt = lerp_closed(prev_pts, u_prev)
                        else:
                            prev_pt = eval_wave_point(
                                prev_state["sampled"],
                                prev_state["wave_dirs"],
                                prev_state["amp"],
                                prev_state["freq"],
                                prev_phase,
                                prev_off,
                                u_prev,
                                z_shift=prev_state.get("z_shift", 0.0),
                            )
                        curr_pt = eval_wave_point(
                            sampled,
                            wave_dirs,
                            amp,
                            freq,
                            phase_shift,
                            off,
                            u_curr,
                        )
                        score += (curr_pt - prev_pt).length_squared
                    score /= pair_count
                    # Keep current layer phase tied to the previous-layer anchor branch.
                    d_anchor = (off - anchor_off) / max(1e-12, period_u)
                    score += (d_anchor * d_anchor) * (amp * 0.25) ** 2 * 6.0
                    return score

                best_off = base_off
                # Narrow local search around anchor to ensure "previous wave as reference".
                search_plan = ((period_u * 0.08, 72), (period_u * 0.025, 96), (period_u * 0.008, 120))
                for span, steps in search_plan:
                    local_best = best_off
                    local_score = contact_score(best_off)
                    for k in range(-steps, steps + 1):
                        cand = best_off + (k / steps) * span
                        sc = contact_score(cand)
                        if sc < local_score:
                            local_score = sc
                            local_best = cand
                    best_off = local_best
                u_offset = best_off

            points = []
            layer_z_bias = 0.0

            def pick_prev_point_for_contact(u_ref, ref_pt, mode):
                if prev_state is None:
                    return None
                prev_freq = max(1e-9, prev_state["freq"])
                prev_phase = prev_state["phase_shift"]
                prev_off = prev_state["u_offset"]
                prev_period_u = 1.0 / prev_freq
                prev_pts = prev_state.get("final_points")

                if mode == "crest":
                    if prev_peak_u_anchor is not None:
                        base_u = nearest_equivalent_u(prev_peak_u_anchor, u_ref, prev_period_u)
                    else:
                        prev_crest_base_u = ((0.25 - (prev_phase / (2.0 * math.pi))) / prev_freq) - prev_off
                        base_u = nearest_equivalent_u(prev_crest_base_u, u_ref, prev_period_u)
                elif mode == "valley":
                    if prev_valley_u_anchor is not None:
                        base_u = nearest_equivalent_u(prev_valley_u_anchor, u_ref, prev_period_u)
                    else:
                        prev_valley_base_u = ((0.75 - (prev_phase / (2.0 * math.pi))) / prev_freq) - prev_off
                        base_u = nearest_equivalent_u(prev_valley_base_u, u_ref, prev_period_u)
                else:
                    base_u = nearest_equivalent_u(u_ref, u_ref, prev_period_u)

                best_pt = None
                best_score = None
                for dk in (-2, -1, 0, 1, 2):
                    u_cand = base_u + (dk * prev_period_u)
                    if prev_pts:
                        cand_pt = lerp_closed(prev_pts, u_cand)
                    else:
                        cand_pt = eval_wave_point(
                            prev_state["sampled"],
                            prev_state["wave_dirs"],
                            prev_state["amp"],
                            prev_freq,
                            prev_phase,
                            prev_off,
                            u_cand,
                            z_shift=prev_state.get("z_shift", 0.0),
                        )
                    dxy2 = ((cand_pt.x - ref_pt.x) ** 2) + ((cand_pt.y - ref_pt.y) ** 2)
                    dz2 = (cand_pt.z - ref_pt.z) ** 2
                    # Prioritize XY alignment, then Z proximity as tie-breaker.
                    score = dxy2 + (dz2 * 0.05)
                    if best_score is None or score < best_score:
                        best_score = score
                        best_pt = cand_pt
                return best_pt
            for i, p in enumerate(sampled):
                u = (i / max(1, n)) + u_offset
                s = 0.0 if freq <= 1e-9 else math.sin((2.0 * math.pi * freq * u) + phase_shift)
                wave = amp * s
                cur_pt = p + (wave_dirs[i] * wave)

                points.append(cur_pt)

            # Local contact correction:
            # 1) crests are driven toward previous-valley contact
            # 2) conservative anti-penetration is enforced per point
            # 3) corrections are smoothed to preserve wave continuity
            if points and prev_state is not None and freq > 1e-9 and amp > 1e-9:
                prev_freq_g = max(1e-9, prev_state["freq"])
                prev_phase_g = prev_state["phase_shift"]
                prev_off_g = prev_state["u_offset"]
                prev_period_g = 1.0 / prev_freq_g
                prev_pts_g = prev_state.get("final_points")
                corr = [0.0] * n

                def conservative_prev_z(u_i, p_cur):
                    cand_items = []
                    base_near = nearest_equivalent_u(u_i, u_i, prev_period_g)
                    if prev_valley_u_anchor is not None:
                        base_valley = nearest_equivalent_u(prev_valley_u_anchor, u_i, prev_period_g)
                    else:
                        valley_base_raw = ((0.75 - (prev_phase_g / (2.0 * math.pi))) / prev_freq_g) - prev_off_g
                        base_valley = nearest_equivalent_u(valley_base_raw, u_i, prev_period_g)

                    for base_u in (base_near, base_valley):
                        for dk in (-2, -1, 0, 1, 2):
                            u_cand = base_u + (dk * prev_period_g)
                            if prev_pts_g:
                                cand_pt = lerp_closed(prev_pts_g, u_cand)
                            else:
                                cand_pt = eval_wave_point(
                                    prev_state["sampled"],
                                    prev_state["wave_dirs"],
                                    prev_state["amp"],
                                    prev_freq_g,
                                    prev_phase_g,
                                    prev_off_g,
                                    u_cand,
                                    z_shift=prev_state.get("z_shift", 0.0),
                                )
                            dxy2 = ((cand_pt.x - p_cur.x) ** 2) + ((cand_pt.y - p_cur.y) ** 2)
                            cand_items.append((dxy2, cand_pt.z))
                    if not cand_items:
                        return None
                    min_dxy2 = min(v[0] for v in cand_items)
                    dxy_thresh = (min_dxy2 * 1.6) + 0.04
                    return max(v[1] for v in cand_items if v[0] <= dxy_thresh)

                for i, p in enumerate(points):
                    u_i = (i / max(1, n)) + u_offset
                    s_i = math.sin((2.0 * math.pi * freq * u_i) + phase_shift)
                    slope_flatness_i = 1.0 - max(0.0, min(1.0, abs(wave_dirs[i].z)))

                    # Crest-driven contact correction.
                    if s_i > 0.05:
                        prev_pt_valley = pick_prev_point_for_contact(u_i, p, "valley")
                        if prev_pt_valley is not None:
                            target_z = prev_pt_valley.z - props.contact_overlap
                            # Weight stronger near crest tops.
                            w_crest = min(1.0, max(0.0, (s_i - 0.05) / 0.95))
                            # Flat areas need stronger pull-down to remove floating gaps.
                            w = min(1.0, w_crest * (1.0 + 0.90 * slope_flatness_i))
                            corr[i] += (target_z - p.z) * w

                    # Per-point anti-penetration correction.
                    ref_z = conservative_prev_z(u_i, p)
                    if ref_z is not None:
                        need_up = (ref_z - props.contact_overlap) - (p.z + corr[i])
                        if need_up > 0.0:
                            # Keep steep-slope protection strong, relax guard on flat regions.
                            steepness = 1.0 - slope_flatness_i
                            guard_w = 0.25 + 0.75 * (steepness * steepness)
                            corr[i] += need_up * guard_w

                # Circular smoothing to avoid local spikes / collapse.
                if n >= 5:
                    sm = [0.0] * n
                    for i in range(n):
                        sm[i] = (
                            corr[(i - 2) % n]
                            + 2.0 * corr[(i - 1) % n]
                            + 3.0 * corr[i]
                            + 2.0 * corr[(i + 1) % n]
                            + corr[(i + 2) % n]
                        ) / 9.0
                    corr = sm

                # Residual crest closure pass (flat-priority):
                # after smoothing, pull remaining floating crests closer to target.
                for i, p in enumerate(points):
                    u_i = (i / max(1, n)) + u_offset
                    s_i = math.sin((2.0 * math.pi * freq * u_i) + phase_shift)
                    if s_i <= 0.10:
                        continue
                    prev_pt_valley = pick_prev_point_for_contact(u_i, p, "valley")
                    if prev_pt_valley is None:
                        continue
                    target_z = prev_pt_valley.z - props.contact_overlap
                    gap = target_z - (p.z + corr[i])
                    if gap > 0.0:
                        slope_flatness_i = 1.0 - max(0.0, min(1.0, abs(wave_dirs[i].z)))
                        extra_cap = props.filament_thickness * (0.10 + 0.60 * slope_flatness_i)
                        corr[i] += min(gap, extra_cap)

                # Clamp by local slope (less downward room on steep areas).
                for i, p in enumerate(points):
                    slope_flatness_i = 1.0 - max(0.0, min(1.0, abs(wave_dirs[i].z)))
                    max_up_i = props.filament_thickness * (0.28 + 0.92 * slope_flatness_i)
                    if props.contact_overlap <= 1e-9:
                        max_down_i = 0.0
                    else:
                        max_down_i = props.filament_thickness * (0.04 + 0.22 * slope_flatness_i)
                    dz = max(-max_down_i, min(max_up_i, corr[i]))
                    p.z += dz
                    layer_z_bias += dz

                if n > 0:
                    layer_z_bias /= n
            if points:
                points_raw = [p.copy() for p in points]
                # Always start from crest in normal mode.
                if freq > 1e-9 and amp > 1e-12 and n > 0:
                    peak_idx = max(
                        range(n),
                        key=lambda i: math.sin((2.0 * math.pi * freq * ((i / n) + u_offset)) + phase_shift),
                    )
                    points = rotate_loop(points, peak_idx)
                points_out = points + [points[0].copy()]
                all_layers.append(points_out)
                prev_wave_dirs = wave_dirs
                prev_state = {
                    "sampled": sampled,
                    "wave_dirs": wave_dirs,
                    "final_points": points_raw,
                    "amp": amp,
                    "freq": freq,
                    "phase_shift": phase_shift,
                    "u_offset": u_offset,
                    "z_shift": layer_z_bias,
                }

                # Save current crest/valley branches as references for the next layer.
                if freq > 1e-9:
                    period_u = 1.0 / freq
                    peak_base_u = (0.25 - (phase_shift / (2.0 * math.pi))) / freq
                    peak_u = peak_base_u - u_offset
                    if prev_peak_u_anchor is not None:
                        peak_u = nearest_equivalent_u(peak_u, prev_peak_u_anchor, period_u)
                    prev_peak_u_anchor = peak_u
                    valley_base_u = (0.75 - (phase_shift / (2.0 * math.pi))) / freq
                    valley_u = valley_base_u - u_offset
                    if prev_valley_u_anchor is not None:
                        valley_u = nearest_equivalent_u(valley_u, prev_valley_u_anchor, period_u)
                    prev_valley_u_anchor = valley_u

            # Adaptive layer step (normal mode only):
            # estimate vertical reach from crest-phase local directions, using a very
            # conservative quantile to keep crest contact on shallow slopes.
            if wave_dirs:
                crest_abs_z = []
                for i, d in enumerate(wave_dirs):
                    u_i = (i / max(1, n)) + u_offset
                    s_i = 0.0 if freq <= 1e-9 else math.sin((2.0 * math.pi * freq * u_i) + phase_shift)
                    if s_i > 0.15:
                        crest_abs_z.append(abs(d.z))
                src_abs = crest_abs_z if crest_abs_z else [abs(d.z) for d in wave_dirs]
                src_abs.sort()
                q_idx = int((len(src_abs) - 1) * 0.03)
                low_abs_z = src_abs[max(0, min(q_idx, len(src_abs) - 1))]
            else:
                low_abs_z = 1.0
            low_abs_z = max(0.0, min(1.0, low_abs_z))
            contact_abs_z = low_abs_z * 0.85

            # Flat first layer needs one-sided contact:
            # layer1 is flat, so layer2 valley alone must reach layer1.
            # On shallow slopes, keep the base contact step small to avoid valley floating.
            contact_step_z = min_contact_step_z * max(0.05, contact_abs_z)
            if layer_idx == 0 and props.flat_initial_layer:
                eff_amp_z = props.z_amplitude * contact_abs_z
                next_layer_step_z = max(0.01, contact_step_z + eff_amp_z)
            else:
                # Normal crest-valley pairing (both layers contribute).
                eff_amp_z = amp * contact_abs_z
                next_layer_step_z = max(0.01, contact_step_z + (2.0 * eff_amp_z))

            # Compensate only positive layer lift.
            # Negative bias (lowering) must not increase the next layer spacing.
            z += max(0.01, next_layer_step_z - max(0.0, layer_z_bias))
    finally:
        cache.clear()
        projector.clear()

    return all_layers


# ---------- Blender operators / UI ----------
class CS_OT_UpdateZWavePreview(bpy.types.Operator):
    bl_idname = "custom_z_slicer.update_preview"
    bl_label = "プレビュー更新"

    def execute(self, context):
        props = context.scene.custom_z_slicer_props

        for name in ["Slicer_Preview_Mesh", "Slicer_Path", "Slicer_BuildPlate"]:
            if name in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

        layers_data = calculate_contact_wave_path(props)
        if not layers_data:
            self.report({'WARNING'}, "輪郭を抽出できませんでした。対象メッシュを確認してください。")
            return {'CANCELLED'}

        mesh_b = bpy.data.meshes.new("BuildPlate_Mesh")
        obj_b = bpy.data.objects.new("Slicer_BuildPlate", mesh_b)
        context.collection.objects.link(obj_b)
        bm_b = bmesh.new()
        bx, by = props.bed_size_x / 2.0, props.bed_size_y / 2.0
        verts_b = [
            bm_b.verts.new((-bx, -by, 0.0)),
            bm_b.verts.new((bx, -by, 0.0)),
            bm_b.verts.new((bx, by, 0.0)),
            bm_b.verts.new((-bx, by, 0.0)),
        ]
        for i in range(4):
            bm_b.edges.new((verts_b[i], verts_b[(i + 1) % 4]))
        bm_b.to_mesh(mesh_b)
        bm_b.free()

        mesh_m = bpy.data.meshes.new("ZWaveMesh")
        obj_m = bpy.data.objects.new("Slicer_Preview_Mesh", mesh_m)
        context.collection.objects.link(obj_m)
        bm = bmesh.new()
        rx, rz = props.line_width / 2.0, props.filament_thickness / 2.0
        segments = max(6, props.preview_tube_segments)

        for points in layers_data:
            layer_rings = []
            for i, p in enumerate(points):
                next_i = (i + 1) % len(points)
                vec = points[next_i] - p
                if vec.length <= 1e-12:
                    vec = Vector((1.0, 0.0, 0.0))
                else:
                    vec.normalize()
                up = Vector((0.0, 0.0, 1.0))
                side = vec.cross(up)
                if side.length <= 1e-12:
                    side = Vector((1.0, 0.0, 0.0))
                else:
                    side.normalize()
                act_up = side.cross(vec)
                if act_up.length <= 1e-12:
                    act_up = Vector((0.0, 0.0, 1.0))
                else:
                    act_up.normalize()

                mesh_center = p - (act_up * rz)
                ring = []
                for s in range(segments):
                    ang = (2.0 * math.pi) * (s / segments)
                    offset = (side * math.cos(ang) * rx) + (act_up * math.sin(ang) * rz)
                    ring.append(bm.verts.new(mesh_center + offset))
                layer_rings.append(ring)

            for i in range(len(layer_rings) - 1):
                r1 = layer_rings[i]
                r2 = layer_rings[i + 1]
                for s in range(segments):
                    v1 = r1[s]
                    v2 = r2[s]
                    v3 = r2[(s + 1) % segments]
                    v4 = r1[(s + 1) % segments]
                    try:
                        bm.faces.new((v1, v2, v3, v4))
                    except ValueError:
                        pass

        bm.to_mesh(mesh_m)
        bm.free()
        obj_m.hide_viewport = not props.show_mesh

        curve_data = bpy.data.curves.new("ZPathData", type='CURVE')
        curve_data.dimensions = '3D'
        obj_c = bpy.data.objects.new("Slicer_Path", curve_data)
        context.collection.objects.link(obj_c)
        for points in layers_data:
            if len(points) < 2:
                continue
            spline = curve_data.splines.new('POLY')
            spline.points.add(len(points) - 1)
            for i, p in enumerate(points):
                spline.points[i].co = (p.x, p.y, p.z, 1.0)
        obj_c.show_in_front = True
        obj_c.hide_viewport = not props.show_path

        self.report({'INFO'}, f"プレビュー更新: {len(layers_data)} 層")
        return {'FINISHED'}


class CS_OT_ExportZWaveGcode(bpy.types.Operator, ExportHelper):
    bl_idname = "custom_z_slicer.export_gcode"
    bl_label = "G-code保存"
    filename_ext = ".gcode"

    def execute(self, context):
        props = context.scene.custom_z_slicer_props
        layers_data = calculate_contact_wave_path(props)
        if not layers_data:
            self.report({'ERROR'}, "出力対象のパスがありません。")
            return {'CANCELLED'}

        if props.auto_center_to_bed:
            mx, my = estimate_model_center_xy(layers_data)
            shift_x = (props.bed_size_x * 0.5) - mx
            shift_y = (props.bed_size_y * 0.5) - my
        else:
            shift_x = 0.0
            shift_y = 0.0

        try:
            with open(self.filepath, "w", encoding='utf-8') as f:
                f.write("; --- STL Contour Z-Wave Slicer ---\n")
                f.write("G21\nG90\nM83\n")
                f.write(f"M140 S{props.bed_temp}\nM104 S{props.nozzle_temp}\n")
                f.write("G28\n")
                f.write(f"M106 S{int(max(0, min(100, props.initial_fan_speed)) * 2.55)}\n")
                f.write(f"M190 S{props.bed_temp}\nM109 S{props.nozzle_temp}\n")

                purge_f = props.purge_speed * 60.0
                f.write(f"G1 Z2.0 F3000\nG1 X10 Y10 Z0.28 F{purge_f:.1f}\n")
                f.write(f"G1 X10 Y{props.bed_size_y - 20.0:.3f} Z0.28 E15 F{purge_f:.1f}\n")
                f.write("G92 E0\nG1 Z2.0 F3000\n")

                for l_idx, points in enumerate(layers_data):
                    if len(points) < 2:
                        continue

                    pf = (props.initial_speed if l_idx == 0 else props.regular_speed) * 60.0
                    tf = (props.initial_travel_speed if l_idx == 0 else props.travel_speed) * 60.0
                    if l_idx == 1:
                        f.write(f"M106 S{int(max(0, min(100, props.fan_speed)) * 2.55)}\n")

                    f.write(f"\n; LAYER:{l_idx}\n")
                    for i, p in enumerate(points):
                        x = p.x + shift_x
                        y = p.y + shift_y
                        if i == 0:
                            f.write(f"G0 X{x:.3f} Y{y:.3f} Z{p.z:.3f} F{tf:.1f}\n")
                        else:
                            dist = (points[i] - points[i - 1]).length
                            e_val = dist * props.line_width * props.filament_thickness * props.extrusion_gain
                            f.write(f"G1 X{x:.3f} Y{y:.3f} Z{p.z:.3f} E{e_val:.5f} F{pf:.1f}\n")

                f.write("\nM104 S0\nM140 S0\nM107\nG28 X0\nM84\n")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        self.report({'INFO'}, "保存完了")
        return {'FINISHED'}


class CustomZWaveProperties(bpy.types.PropertyGroup):
    target_object: bpy.props.PointerProperty(
        name="対象メッシュ",
        type=bpy.types.Object,
        description="STLを読み込んだメッシュオブジェクトを選択",
    )

    bed_size_x: bpy.props.FloatProperty(name="ベッドX", default=220.0, min=1.0)
    bed_size_y: bpy.props.FloatProperty(name="ベッドY", default=220.0, min=1.0)

    nozzle_temp: bpy.props.IntProperty(name="ノズル温度", default=200, min=0, max=350)
    bed_temp: bpy.props.IntProperty(name="ベッド温度", default=60, min=0, max=150)

    initial_fan_speed: bpy.props.IntProperty(name="1層目ファン (%)", default=0, min=0, max=100)
    fan_speed: bpy.props.IntProperty(name="通常ファン (%)", default=100, min=0, max=100)

    purge_speed: bpy.props.FloatProperty(name="パージ速度 (mm/s)", default=25.0, min=0.1)
    initial_travel_speed: bpy.props.FloatProperty(name="1層目トラベル (mm/s)", default=50.0, min=0.1)
    travel_speed: bpy.props.FloatProperty(name="通常トラベル (mm/s)", default=120.0, min=0.1)
    initial_speed: bpy.props.FloatProperty(name="1層目造形速度 (mm/s)", default=15.0, min=0.1)
    regular_speed: bpy.props.FloatProperty(name="通常造形速度 (mm/s)", default=45.0, min=0.1)

    flat_initial_layer: bpy.props.BoolProperty(name="1層目フラット", default=False)
    initial_layer_height: bpy.props.FloatProperty(name="1層目高さ", default=0.2, min=0.01)

    z_amplitude: bpy.props.FloatProperty(name="Z波の振幅", default=2.0, min=0.0)
    initial_frequency: bpy.props.FloatProperty(name="1層目波の数", default=10.0, min=0.0)
    z_frequency: bpy.props.FloatProperty(name="通常波の数", default=10.0, min=0.0)
    total_layers: bpy.props.IntProperty(name="積層数", default=30, min=1)

    line_width: bpy.props.FloatProperty(name="フィラメント幅(XY)", default=0.6, min=0.05)
    filament_thickness: bpy.props.FloatProperty(name="フィラメント厚(Z)", default=0.4, min=0.01)
    contact_overlap: bpy.props.FloatProperty(name="接点の食い込み", default=0.1, min=0.0)
    extrusion_gain: bpy.props.FloatProperty(name="押出ゲイン", default=1.25, min=0.01)

    path_steps: bpy.props.IntProperty(name="輪郭サンプル点数", default=400, min=32, max=5000)
    join_tolerance: bpy.props.FloatProperty(name="輪郭接続許容", default=0.02, min=0.001, max=1.0, precision=4)
    preview_tube_segments: bpy.props.IntProperty(name="プレビュー断面分割", default=8, min=4, max=64)

    auto_center_to_bed: bpy.props.BoolProperty(name="G-codeをベッド中央へ自動配置", default=True)
    show_mesh: bpy.props.BoolProperty(name="造形物を表示", default=True)
    show_path: bpy.props.BoolProperty(name="パスを表示", default=True)


class CS_PT_ZWavePanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Z-Wave Slicer'
    bl_label = "Z-Wave Contact Slicer"

    def draw(self, context):
        p = context.scene.custom_z_slicer_props
        layout = self.layout

        box = layout.box()
        box.label(text="Model", icon='MESH_DATA')
        box.prop(p, "target_object")

        box = layout.box()
        box.label(text="Display Settings", icon='HIDE_OFF')
        row = box.row()
        row.prop(p, "show_mesh", toggle=True)
        row.prop(p, "show_path", toggle=True)

        box = layout.box()
        box.label(text="Printer & Hardware", icon='TOOL_SETTINGS')
        row = box.row()
        row.prop(p, "bed_size_x")
        row.prop(p, "bed_size_y")
        row = box.row()
        row.prop(p, "nozzle_temp")
        row.prop(p, "bed_temp")
        row = box.row()
        row.prop(p, "initial_fan_speed")
        row.prop(p, "fan_speed")

        box = layout.box()
        box.label(text="Speed Settings (mm/s)", icon='DRIVER')
        box.prop(p, "purge_speed")
        row = box.row()
        row.prop(p, "initial_travel_speed")
        row.prop(p, "travel_speed")
        row = box.row()
        row.prop(p, "initial_speed")
        row.prop(p, "regular_speed")

        box = layout.box()
        box.label(text="Wave Shape & Layers", icon='MOD_WAVE')
        box.prop(p, "flat_initial_layer")
        box.prop(p, "initial_layer_height")
        box.prop(p, "z_amplitude")
        row = box.row()
        row.prop(p, "initial_frequency")
        row.prop(p, "z_frequency")
        box.prop(p, "total_layers")

        box = layout.box()
        box.label(text="Filament Dimension", icon='LINE_DATA')
        box.prop(p, "line_width")
        box.prop(p, "filament_thickness")
        box.prop(p, "contact_overlap")
        box.prop(p, "extrusion_gain")

        box = layout.box()
        box.label(text="Contour Sampling", icon='MESH_CIRCLE')
        box.prop(p, "path_steps")
        box.prop(p, "join_tolerance")
        box.prop(p, "preview_tube_segments")
        box.prop(p, "auto_center_to_bed")

        layout.separator()
        layout.operator("custom_z_slicer.update_preview", icon='FILE_REFRESH')
        layout.operator("custom_z_slicer.export_gcode", icon='EXPORT')


classes = (
    CustomZWaveProperties,
    CS_OT_UpdateZWavePreview,
    CS_OT_ExportZWaveGcode,
    CS_PT_ZWavePanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.custom_z_slicer_props = bpy.props.PointerProperty(type=CustomZWaveProperties)


def unregister():
    if hasattr(bpy.types.Scene, "custom_z_slicer_props"):
        del bpy.types.Scene.custom_z_slicer_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
