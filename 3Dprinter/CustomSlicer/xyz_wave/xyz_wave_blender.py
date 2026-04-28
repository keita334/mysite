bl_info = {
    "name": "Custom Slicer STL XYZ-Wave",
    "author": "Custom",
    "version": (2, 0, 0),
    "blender": (3, 3, 0),
    "location": "View3D > Sidebar > Custom Slicer",
    "description": "Slice STL contours and generate tangent-wave XYZ toolpaths on curved surfaces",
    "category": "3D View",
}

import math

import bpy
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


def nearest_equivalent_u(base_u, target_u, period_u):
    if period_u <= 1e-12:
        return base_u
    k = round((target_u - base_u) / period_u)
    return base_u + k * period_u


def get_wave_slice_step(props):
    # Dense provisional slicing step (independent from normal layer pitch).
    if props.amplitude <= 1e-9:
        return 0.01
    return max(0.005, props.amplitude * 0.06)


def average_loop_displacement(loop_a, loop_b):
    if not loop_a or not loop_b or len(loop_a) != len(loop_b):
        return 0.0
    acc = 0.0
    n = len(loop_a)
    for i in range(n):
        acc += (loop_b[i] - loop_a[i]).length
    return acc / max(1, n)


def min_loop_displacement(loop_a, loop_b):
    if not loop_a or not loop_b or len(loop_a) != len(loop_b):
        return 0.0
    m = None
    n = len(loop_a)
    for i in range(n):
        d = (loop_b[i] - loop_a[i]).length
        if m is None or d < m:
            m = d
    return 0.0 if m is None else m


def robust_uniform_amplitude(candidates, requested_amp):
    # Avoid collapsing amplitude to ~0 by tiny outlier loops (e.g. near tips/caps).
    caps = [c.get("amp_cap", 0.0) for c in candidates if c.get("amp_cap", 0.0) > 1e-6]
    if not caps:
        return 0.0
    caps.sort()
    q = 0.20  # conservative but robust against a few tiny caps
    idx = int((len(caps) - 1) * q)
    q_cap = caps[max(0, min(idx, len(caps) - 1))]
    # Keep some visible wave unless geometry is truly too small.
    floor_cap = requested_amp * 0.25
    return min(requested_amp, max(q_cap, floor_cap))


def resample_layers_by_surface_span(candidates, base_amplitude):
    if not candidates:
        return []
    if len(candidates) == 1:
        return candidates[:]

    out = [candidates[0]]
    acc = 0.0
    prev = candidates[0]
    last_kept = candidates[0]
    for cand in candidates[1:]:
        acc += average_loop_displacement(prev["points"], cand["points"])
        prev = cand
        # Contact-oriented span:
        # use the sum of adjacent amplitudes so lower peak and upper valley can seat.
        target_span = max(0.01, (last_kept["amp_eff"] + cand["amp_eff"]) * 1.00)
        if acc + 1e-12 >= target_span:
            out.append(cand)
            last_kept = cand
            acc = 0.0

    if out[-1] is not candidates[-1]:
        out.append(candidates[-1])
    return out


def get_outer_loop(loops):
    if not loops:
        return None
    return max(loops, key=lambda pts: abs(polygon_area_xy(pts)))


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
        best_score = -1e9
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


def loop_centroid(points):
    if not points:
        return Vector((0.0, 0.0, 0.0))
    c = Vector((0.0, 0.0, 0.0))
    for p in points:
        c += p
    return c / len(points)


def compute_outward_xy(points):
    n = len(points)
    if n == 0:
        return []
    area = polygon_area_xy(points)
    ccw = area > 0.0
    out = []
    for i in range(n):
        p_prev = points[(i - 1) % n]
        p_next = points[(i + 1) % n]
        t = p_next - p_prev
        txy = Vector((t.x, t.y, 0.0))
        if txy.length <= 1e-12:
            out.append(Vector((1.0, 0.0, 0.0)))
            continue
        txy.normalize()
        nxy = Vector((txy.y, -txy.x, 0.0)) if ccw else Vector((-txy.y, txy.x, 0.0))
        if nxy.length <= 1e-12:
            nxy = Vector((1.0, 0.0, 0.0))
        else:
            nxy.normalize()
        out.append(nxy)
    # Smooth outward hints to reduce local direction jitter on noisy contours.
    for _ in range(2):
        smoothed = []
        for i in range(n):
            v = out[(i - 1) % n] + out[i] * 2.0 + out[(i + 1) % n]
            smoothed.append(safe_normalize(v, out[i]))
        out = smoothed
    return out


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


def lerp_surface_frame(layers, layer_idx, u):
    points = layers[layer_idx]["points"]
    n = len(points)
    if n == 0:
        return (
            Vector((0.0, 0.0, 0.0)),
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((1.0, 0.0, 0.0)),
        )

    uf = u % 1.0
    x = uf * n
    i0 = int(math.floor(x)) % n
    i1 = (i0 + 1) % n
    t = x - math.floor(x)

    p0, n0, tu0, tz0 = build_surface_frame(layers, layer_idx, i0)
    p1, n1, tu1, tz1 = build_surface_frame(layers, layer_idx, i1)

    p = p0.lerp(p1, t)
    t_u = safe_normalize(tu0 * (1.0 - t) + tu1 * t, tu0)
    nrm = safe_normalize(n0 * (1.0 - t) + n1 * t, n0)
    t_z = safe_normalize(tz0 * (1.0 - t) + tz1 * t, tz0)
    out_xy = layers[layer_idx]["out_xy"]
    o0 = out_xy[i0]
    o1 = out_xy[i1]
    out_hint = safe_normalize(o0 * (1.0 - t) + o1 * t, o0)
    return p, t_u, nrm, t_z, out_hint


def evaluate_wave_state(
    layers,
    layer_idx,
    u,
    phase_layer,
    u_contact_offset,
    props,
    amplitude_override=None,
    surface_projector=None,
    base_offset_bias=0.0,
    wave_bias=0.0,
):
    p, t_u, nrm_est, t_z, out_hint = lerp_surface_frame(layers, layer_idx, u)
    phase = (2.0 * math.pi * props.xy_frequency * (u + u_contact_offset)) + phase_layer
    amp = props.amplitude if amplitude_override is None else amplitude_override
    wave = amp * math.sin(phase) + wave_bias

    surf_p = p
    surf_n = None
    if surface_projector is not None:
        surf_p, surf_n = surface_projector.nearest_point_normal(p)
    if surf_n is None:
        nrm = nrm_est
    else:
        if surf_n.dot(out_hint) < 0.0:
            surf_n = -surf_n
        # Blend projected normal with contour-derived normal to avoid facet jitter.
        nrm = safe_normalize(nrm_est * 0.75 + surf_n * 0.25, nrm_est)

    # Stable tangent direction on the local surface:
    # use wall tangent as primary direction to keep the wave continuous.
    wall_tan_raw = t_z - t_u * t_z.dot(t_u)
    wall_tan = safe_normalize(wall_tan_raw, t_z)
    wall_tan_on_surface = safe_normalize(wall_tan - nrm * wall_tan.dot(nrm), wall_tan)
    out_on_surface = out_hint - nrm * out_hint.dot(nrm)
    wave_dir = safe_normalize(wall_tan_on_surface, out_on_surface)
    if wave_dir.dot(out_hint) < 0.0:
        wave_dir = -wave_dir

    base_offset = -(props.surface_inset + props.base_offset) + base_offset_bias
    # Keep contour-following stable: use contour point as base (not nearest-point snap).
    point = p + nrm * base_offset + wave_dir * wave
    return point, wave_dir, nrm, wave


def evaluate_wave_point(
    layers,
    layer_idx,
    u,
    phase_layer,
    u_contact_offset,
    props,
    amplitude_override=None,
    surface_projector=None,
    base_offset_bias=0.0,
    wave_bias=0.0,
):
    point, _, _, _ = evaluate_wave_state(
        layers,
        layer_idx,
        u,
        phase_layer,
        u_contact_offset,
        props,
        amplitude_override=amplitude_override,
        surface_projector=surface_projector,
        base_offset_bias=base_offset_bias,
        wave_bias=wave_bias,
    )
    return point


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
            if n.length <= 1e-12:
                n = Vector((0.0, 0.0, 1.0))
            else:
                n.normalize()
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
        if nrm is None or nrm.length <= 1e-12:
            if idx is not None and 0 <= idx < len(self.tri_normals):
                nrm = self.tri_normals[idx].copy()
            else:
                nrm = None
        elif nrm.length > 1e-12:
            nrm = nrm.normalized()
        return loc, nrm


# ---------- mesh slicing cache ----------
class MeshSliceCache:
    def __init__(self, obj):
        self.obj = obj
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


# ---------- XYZ-wave generation ----------
def build_contour_layers(props):
    obj = props.target_object
    if obj is None or obj.type != 'MESH':
        return []

    bb = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    z_min = min(v.z for v in bb)
    z_max = max(v.z for v in bb)
    if z_max <= z_min:
        return []

    candidates = []
    cache = MeshSliceCache(obj)
    try:
        prev_loop = None
        target_count = None
        dense_step = get_wave_slice_step(props)
        z = z_min + props.initial_layer_height

        while z <= z_max + 1e-9:
            loops = cache.section_loops(z, tol=props.join_tolerance)
            if not loops:
                z += dense_step
                continue

            outer = get_outer_loop(loops)
            if outer is None:
                z += dense_step
                continue

            perimeter = sum((outer[(i + 1) % len(outer)] - outer[i]).length for i in range(len(outer)))
            if target_count is None:
                count = int(perimeter / max(0.05, props.target_segment_length))
                count = max(count, int(props.xy_frequency * 96.0))
                target_count = max(props.min_points_per_loop, min(props.max_points_per_loop, count))

            sampled = resample_closed_loop(outer, target_count)
            sampled = canonicalize_loop_phase(sampled)
            if prev_loop is not None:
                sampled = align_loop_start(prev_loop, sampled)
            local_step = perimeter / max(1, len(sampled))
            # Anti-cross protection:
            # 1) same-layer crossing guard from local arc step
            amp_cap_step = 0.85 * local_step
            # 2) inter-layer crossing guard from previous contour gap
            if prev_loop is not None:
                gap_min = min_loop_displacement(prev_loop, sampled)
                amp_cap_gap = 0.48 * gap_min
                amp_cap = min(amp_cap_step, amp_cap_gap)
            else:
                amp_cap = amp_cap_step
            amp_cap = max(0.0, amp_cap)
            prev_loop = sampled[:]

            candidates.append(
                {
                    "z": z,
                    "points": sampled,
                    "perimeter": max(1e-9, perimeter),
                    "centroid": loop_centroid(sampled),
                    "out_xy": compute_outward_xy(sampled),
                    "amp_cap": amp_cap,
                }
            )
            z += dense_step
    finally:
        cache.clear()

    if not candidates:
        return []

    # Use one global effective amplitude for all layers so wave amplitude is uniform.
    # Robust selection prevents one tiny loop from collapsing the entire wave.
    global_amp_eff = robust_uniform_amplitude(candidates, props.amplitude)
    for c in candidates:
        c["amp_eff"] = global_amp_eff

    # Final layer split follows effective amplitude so peak/valley contact is achievable.
    return resample_layers_by_surface_span(candidates, global_amp_eff)


def build_surface_frame(layers, layer_idx, point_idx):
    layer = layers[layer_idx]
    points = layer["points"]
    n = len(points)
    p = points[point_idx]

    p_prev = points[(point_idx - 1) % n]
    p_next = points[(point_idx + 1) % n]
    t_u = safe_normalize(p_next - p_prev, Vector((1.0, 0.0, 0.0)))

    if len(layers) == 1:
        t_z_raw = Vector((0.0, 0.0, 1.0))
    elif layer_idx == 0:
        t_z_raw = layers[1]["points"][point_idx] - p
    elif layer_idx == len(layers) - 1:
        t_z_raw = p - layers[layer_idx - 1]["points"][point_idx]
    else:
        t_z_raw = layers[layer_idx + 1]["points"][point_idx] - layers[layer_idx - 1]["points"][point_idx]

    t_z = safe_normalize(t_z_raw, Vector((0.0, 0.0, 1.0)))

    nrm = t_u.cross(t_z)
    out_xy = layer["out_xy"][point_idx]
    if nrm.dot(out_xy) < 0.0:
        nrm = -nrm
    nrm = safe_normalize(nrm, out_xy if out_xy.length > 1e-12 else Vector((1.0, 0.0, 0.0)))

    return p, nrm, t_u, t_z


def calculate_stl_xyz_wave_layers(props):
    contour_layers = build_contour_layers(props)
    if not contour_layers:
        return []

    freq = props.xy_frequency
    use_wave = freq > 1e-9 and props.amplitude > 0.0
    period_u = (1.0 / freq) if freq > 1e-9 else 1.0

    all_layers = []
    prev_peak_u_anchor = None
    prev_peak_point = None
    prev_phase_layer = None
    prev_u_contact_offset = 0.0
    prev_base_bias = 0.0
    projector = SurfaceProjector(props.target_object)
    try:
        for layer_idx, base in enumerate(contour_layers):
            points = base["points"]
            n = len(points)
            wave_loop = []
            layer_amplitude = base.get("amp_eff", props.amplitude)

            phase_layer = props.phase_start + layer_idx * props.layer_phase_shift
            u_contact_offset = 0.0
            layer_base_bias = 0.0

            if use_wave and prev_peak_u_anchor is not None:
                valley_base_u = (1.5 * math.pi - phase_layer) / (2.0 * math.pi * freq)
                valley_target_u = nearest_equivalent_u(valley_base_u, prev_peak_u_anchor, period_u)
                # Extremum position is u = base_u - offset (phase uses u + offset).
                u_contact_offset = valley_base_u - valley_target_u
                off_ref = u_contact_offset

                if prev_peak_point is not None:
                    prev_peak_base_u = (0.5 * math.pi - prev_phase_layer) / (2.0 * math.pi * freq)
                    pair_count = max(8, min(96, int(round(freq * 3.0))))

                    def contact_score(off, base_bias):
                        u_valley0 = valley_base_u - off
                        u_prev_peak0 = prev_peak_base_u - prev_u_contact_offset
                        w = 1.0 / pair_count
                        score = 0.0
                        for j in range(pair_count):
                            du = j * period_u
                            u_curr = u_valley0 + du
                            u_prev = u_prev_peak0 + du
                            curr_pt = evaluate_wave_point(
                                contour_layers,
                                layer_idx,
                                u_curr,
                                phase_layer,
                                off,
                                props,
                                amplitude_override=layer_amplitude,
                                surface_projector=projector,
                                base_offset_bias=base_bias,
                            )
                            prev_pt = evaluate_wave_point(
                                contour_layers,
                                layer_idx - 1,
                                u_prev,
                                prev_phase_layer,
                                prev_u_contact_offset,
                                props,
                                amplitude_override=contour_layers[layer_idx - 1].get("amp_eff", props.amplitude),
                                surface_projector=projector,
                                base_offset_bias=prev_base_bias,
                            )
                            score += (curr_pt - prev_pt).length_squared * w

                        # Keep a continuous phase branch across layers (avoid layer 2->3 jumps).
                        if period_u > 1e-12:
                            off_near_ref = nearest_equivalent_u(off_ref, off, period_u)
                            d_phase = (off_near_ref - off_ref) / period_u
                            penalty_scale = (max(1e-6, layer_amplitude) * 0.8) ** 2 * 8.0
                            score += penalty_scale * (d_phase * d_phase)
                        return score

                    best_off = off_ref
                    # First pair (layer1/layer2) needs tighter lock, then use normal search.
                    if layer_idx == 1:
                        search_plan = ((period_u * 0.45, 96), (period_u * 0.12, 120), (period_u * 0.03, 140))
                    else:
                        search_plan = ((period_u * 0.16, 72), (period_u * 0.05, 100), (period_u * 0.015, 140))

                    for span, steps in search_plan:
                        local_best = best_off
                        local_score = contact_score(best_off, layer_base_bias)
                        for k in range(-steps, steps + 1):
                            off = best_off + (k / steps) * span
                            score = contact_score(off, layer_base_bias)
                            if local_score is None or score < local_score:
                                local_score = score
                                local_best = off
                        best_off = local_best
                    u_contact_offset = best_off

                    # Global seating correction along local normals (preserve wave shape).
                    for _ in range(2):
                        u_valley0 = valley_base_u - u_contact_offset
                        u_prev_peak0 = prev_peak_base_u - prev_u_contact_offset
                        signed_gap = 0.0
                        for j in range(pair_count):
                            du = j * period_u
                            u_curr = u_valley0 + du
                            u_prev = u_prev_peak0 + du
                            curr_pt, _, curr_nrm, _ = evaluate_wave_state(
                                contour_layers,
                                layer_idx,
                                u_curr,
                                phase_layer,
                                u_contact_offset,
                                props,
                                amplitude_override=layer_amplitude,
                                surface_projector=projector,
                                base_offset_bias=layer_base_bias,
                            )
                            prev_pt = evaluate_wave_point(
                                contour_layers,
                                layer_idx - 1,
                                u_prev,
                                prev_phase_layer,
                                prev_u_contact_offset,
                                props,
                                amplitude_override=contour_layers[layer_idx - 1].get("amp_eff", props.amplitude),
                                surface_projector=projector,
                                base_offset_bias=prev_base_bias,
                            )
                            signed_gap += (prev_pt - curr_pt).dot(curr_nrm)
                        layer_base_bias += signed_gap / pair_count

                        local_span = period_u * 0.01
                        local_steps = 120
                        local_best = u_contact_offset
                        local_score = contact_score(u_contact_offset, layer_base_bias)
                        for k in range(-local_steps, local_steps + 1):
                            off = u_contact_offset + (k / local_steps) * local_span
                            score = contact_score(off, layer_base_bias)
                            if score < local_score:
                                local_score = score
                                local_best = off
                        u_contact_offset = local_best

            for i in range(n):
                u = i / n
                wp = evaluate_wave_point(
                    contour_layers,
                    layer_idx,
                    u,
                    phase_layer,
                    u_contact_offset,
                    props,
                    amplitude_override=layer_amplitude,
                    surface_projector=projector,
                    base_offset_bias=layer_base_bias,
                )
                wave_loop.append(wp)

            all_layers.append((base["z"], [wave_loop]))

            if use_wave:
                peak_base_u = (0.5 * math.pi - phase_layer) / (2.0 * math.pi * freq)
                peak_u = peak_base_u - u_contact_offset
                if prev_peak_u_anchor is not None:
                    peak_u = nearest_equivalent_u(peak_u, prev_peak_u_anchor, period_u)
                prev_peak_u_anchor = peak_u
                prev_peak_point = evaluate_wave_point(
                    contour_layers,
                    layer_idx,
                    peak_u,
                    phase_layer,
                    u_contact_offset,
                    props,
                    amplitude_override=layer_amplitude,
                    surface_projector=projector,
                    base_offset_bias=layer_base_bias,
                )
                prev_phase_layer = phase_layer
                prev_u_contact_offset = u_contact_offset
                prev_base_bias = layer_base_bias
    finally:
        projector.clear()

    return all_layers


# ---------- Blender operators / UI ----------
class CS_OT_UpdatePreviewXYZ(bpy.types.Operator):
    bl_idname = "custom_slicer.update_preview_xyz"
    bl_label = "XYZ Waveプレビュー更新"

    def execute(self, context):
        props = context.scene.custom_slicer_xyz_props

        for name in ["XYZ_Wave_Path", "XYZ_BuildPlate"]:
            if name in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

        layers = calculate_stl_xyz_wave_layers(props)
        if not layers:
            self.report({'WARNING'}, "輪郭が抽出できませんでした。対象オブジェクトを確認してください。")
            return {'CANCELLED'}

        mesh_b = bpy.data.meshes.new("XYZ_BuildPlate_Mesh")
        obj_b = bpy.data.objects.new("XYZ_BuildPlate", mesh_b)
        context.collection.objects.link(obj_b)
        verts = [
            (0.0, 0.0, 0.0),
            (props.bed_size_x, 0.0, 0.0),
            (props.bed_size_x, props.bed_size_y, 0.0),
            (0.0, props.bed_size_y, 0.0),
        ]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        mesh_b.from_pydata(verts, edges, [])
        mesh_b.update()

        curve_data = bpy.data.curves.new("XYZ_Wave_Path_Data", type='CURVE')
        curve_data.dimensions = '3D'
        obj_curve = bpy.data.objects.new("XYZ_Wave_Path", curve_data)
        context.collection.objects.link(obj_curve)

        for _, loops in layers:
            for points in loops:
                spline = curve_data.splines.new('POLY')
                spline.points.add(len(points))
                for i, p in enumerate(points + [points[0]]):
                    spline.points[i].co = (p.x, p.y, p.z, 1.0)

        obj_curve.show_in_front = True
        mat = bpy.data.materials.new(name="XYZ_Wave_Path_Mat")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        emi = nodes.new("ShaderNodeEmission")
        emi.inputs["Color"].default_value = (0.2, 0.7, 1.0, 1.0)
        emi.inputs["Strength"].default_value = 2.0
        links.new(emi.outputs[0], out.inputs[0])
        obj_curve.data.materials.append(mat)
        obj_curve.hide_viewport = not props.show_path

        self.report({'INFO'}, f"プレビュー更新: {len(layers)} 層")
        return {'FINISHED'}


class CS_OT_ExportGcodeXYZ(bpy.types.Operator, ExportHelper):
    bl_idname = "custom_slicer.export_gcode_xyz"
    bl_label = "XYZ Wave G-code保存"
    filename_ext = ".gcode"

    def execute(self, context):
        props = context.scene.custom_slicer_xyz_props
        layers = calculate_stl_xyz_wave_layers(props)
        if not layers:
            self.report({'ERROR'}, "出力対象のパスがありません。")
            return {'CANCELLED'}

        e_acc = 0.0
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write("; --- STL XYZ-Wave Slicer ---\n")
                f.write("G21\nG90\nM82\n")
                f.write(f"M140 S{props.bed_temp}\nM104 S{props.nozzle_temp}\n")
                f.write("G28\n")
                f.write(f"M190 S{props.bed_temp}\nM109 S{props.nozzle_temp}\n")
                f.write("G92 E0\n")

                for layer_idx, (z, loops) in enumerate(layers):
                    if layer_idx == 0:
                        if len(layers) > 1:
                            curr_h = max(0.01, layers[1][0] - layers[0][0])
                        else:
                            curr_h = max(0.01, props.initial_layer_height)
                    else:
                        curr_h = max(0.01, layers[layer_idx][0] - layers[layer_idx - 1][0])
                    print_f = (props.initial_speed if layer_idx == 0 else props.regular_speed) * 60.0
                    travel_f = (props.initial_travel_speed if layer_idx == 0 else props.travel_speed) * 60.0
                    f.write(f"\n; LAYER:{layer_idx} Z:{z:.3f}\n")

                    for loop in loops:
                        first = loop[0]
                        f.write(f"G0 X{first.x:.3f} Y{first.y:.3f} Z{first.z:.3f} F{travel_f:.1f}\n")
                        prev = first
                        for p in loop[1:] + [first]:
                            dist = (p - prev).length
                            e_acc += dist * props.line_width * curr_h * props.extrusion_gain
                            f.write(f"G1 X{p.x:.3f} Y{p.y:.3f} Z{p.z:.3f} E{e_acc:.5f} F{print_f:.1f}\n")
                            prev = p

                f.write("\nM104 S0\nM140 S0\nM107\nM84\n")
            self.report({'INFO'}, "保存完了")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        return {'FINISHED'}


class CustomSlicerXYZProperties(bpy.types.PropertyGroup):
    target_object: bpy.props.PointerProperty(
        name="対象メッシュ",
        type=bpy.types.Object,
        description="STLを読み込んだメッシュオブジェクトを選択",
    )

    bed_size_x: bpy.props.FloatProperty(name="ベッドX", default=220.0, min=1.0)
    bed_size_y: bpy.props.FloatProperty(name="ベッドY", default=220.0, min=1.0)

    nozzle_temp: bpy.props.IntProperty(name="ノズル温度", default=220, min=0, max=350)
    bed_temp: bpy.props.IntProperty(name="ベッド温度", default=55, min=0, max=150)

    initial_travel_speed: bpy.props.FloatProperty(name="1層目トラベル (mm/s)", default=50.0, min=0.1, precision=2)
    travel_speed: bpy.props.FloatProperty(name="通常トラベル (mm/s)", default=150.0, min=0.1, precision=2)
    initial_speed: bpy.props.FloatProperty(name="1層目造形速度 (mm/s)", default=20.0, min=0.1, precision=2)
    regular_speed: bpy.props.FloatProperty(name="通常造形速度 (mm/s)", default=40.0, min=0.1, precision=2)

    base_offset: bpy.props.FloatProperty(name="基本オフセット", default=0.8, min=0.0)
    surface_inset: bpy.props.FloatProperty(name="表面インセット", default=0.05, min=0.0, precision=3)
    amplitude: bpy.props.FloatProperty(name="振幅", default=0.5, min=0.0)
    xy_frequency: bpy.props.FloatProperty(name="XY波数", default=18.0, min=0.1)
    phase_start: bpy.props.FloatProperty(name="開始位相(rad)", default=0.0)
    layer_phase_shift: bpy.props.FloatProperty(name="層位相シフト(rad)", default=math.pi)
    line_width: bpy.props.FloatProperty(name="線幅", default=0.45, min=0.05)
    extrusion_gain: bpy.props.FloatProperty(name="押出ゲイン", default=1.00, min=0.01)
    initial_layer_height: bpy.props.FloatProperty(name="1層目ピッチ", default=0.2, min=0.01)
    layer_height: bpy.props.FloatProperty(name="通常ピッチ", default=0.2, min=0.01)

    target_segment_length: bpy.props.FloatProperty(name="目標セグメント長", default=0.6, min=0.05, max=5.0, precision=3)
    min_points_per_loop: bpy.props.IntProperty(name="最小点数", default=180, min=16, max=4000)
    max_points_per_loop: bpy.props.IntProperty(name="最大点数", default=1200, min=32, max=6000)
    join_tolerance: bpy.props.FloatProperty(name="輪郭接続許容", default=0.02, min=0.001, max=1.0, precision=4)

    show_path: bpy.props.BoolProperty(name="パスを表示", default=True)


class CS_PT_MainPanelXYZ(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Custom Slicer'
    bl_label = "STL XYZ Wave Slicer"

    def draw(self, context):
        p = context.scene.custom_slicer_xyz_props
        layout = self.layout

        box = layout.box()
        box.label(text="Model / Bed")
        box.prop(p, "target_object")
        row = box.row()
        row.prop(p, "bed_size_x")
        row.prop(p, "bed_size_y")

        box = layout.box()
        box.label(text="Wave / Sampling")
        box.prop(p, "base_offset")
        box.prop(p, "surface_inset")
        box.prop(p, "amplitude")
        box.prop(p, "xy_frequency")
        box.prop(p, "phase_start")
        box.prop(p, "layer_phase_shift")
        box.prop(p, "join_tolerance")
        box.prop(p, "target_segment_length")
        row = box.row()
        row.prop(p, "min_points_per_loop")
        row.prop(p, "max_points_per_loop")

        box = layout.box()
        box.label(text="Layer / Print")
        row = box.row()
        row.prop(p, "initial_layer_height")
        row.prop(p, "layer_height")
        box.prop(p, "line_width")
        box.prop(p, "extrusion_gain")
        row = box.row()
        row.prop(p, "initial_travel_speed")
        row.prop(p, "travel_speed")
        row = box.row()
        row.prop(p, "initial_speed")
        row.prop(p, "regular_speed")
        row = box.row()
        row.prop(p, "nozzle_temp")
        row.prop(p, "bed_temp")

        layout.separator()
        layout.prop(p, "show_path", icon='CURVE_PATH')
        layout.operator("custom_slicer.update_preview_xyz", icon='FILE_REFRESH')
        layout.operator("custom_slicer.export_gcode_xyz", icon='EXPORT')


classes = (
    CustomSlicerXYZProperties,
    CS_OT_UpdatePreviewXYZ,
    CS_OT_ExportGcodeXYZ,
    CS_PT_MainPanelXYZ,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.custom_slicer_xyz_props = bpy.props.PointerProperty(type=CustomSlicerXYZProperties)


def unregister():
    if hasattr(bpy.types.Scene, "custom_slicer_xyz_props"):
        del bpy.types.Scene.custom_slicer_xyz_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
