bl_info = {
    "name": "Custom Slicer STL XY-Wave",
    "author": "Custom",
    "version": (1, 0, 0),
    "blender": (3, 3, 0),
    "location": "View3D > Sidebar > Custom Slicer",
    "description": "Slice STL mesh contours and generate XY sinusoidal wrapped paths",
    "category": "3D View",
}

import bpy
import math
from mathutils import Vector
from bpy_extras.io_utils import ExportHelper


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

    if (points[0] - points[-1]).length < 1e-6:
        src = points[:-1]
    else:
        src = points[:]

    n = len(src)
    if n < 3:
        return src

    seg_len = []
    total = 0.0
    for i in range(n):
        d = (src[(i + 1) % n] - src[i]).length
        seg_len.append(d)
        total += d

    if total <= 1e-9:
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
        p = src[idx].lerp(src[(idx + 1) % n], local)
        out.append(p)
    return out


def rotate_loop(points, shift):
    n = len(points)
    if n == 0:
        return points
    s = shift % n
    return points[s:] + points[:s]


def canonicalize_loop_phase(points):
    # Stable orientation and start index to avoid layer-to-layer phase jumps.
    if len(points) < 3:
        return points[:]
    loop = points[:]
    if polygon_area_xy(loop) < 0.0:
        loop.reverse()
    idx = max(range(len(loop)), key=lambda i: (loop[i].x, -loop[i].y))
    return rotate_loop(loop, idx)


def loop_length_xy(points):
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        total += (points[(i + 1) % len(points)] - points[i]).length
    return total


def outward_normals(points):
    n = len(points)
    area = polygon_area_xy(points)
    ccw = area > 0.0
    normals = []
    for i in range(n):
        p_prev = points[(i - 1) % n]
        p_next = points[(i + 1) % n]
        t = p_next - p_prev
        t.z = 0.0
        if t.length < 1e-12:
            normals.append(Vector((1.0, 0.0, 0.0)))
            continue
        t.normalize()
        if ccw:
            nrm = Vector((t.y, -t.x, 0.0))
        else:
            nrm = Vector((-t.y, t.x, 0.0))
        if nrm.length < 1e-12:
            nrm = Vector((1.0, 0.0, 0.0))
        else:
            nrm.normalize()
        normals.append(nrm)
    return normals


def get_outer_loop(loops):
    if not loops:
        return None
    return max(loops, key=lambda pts: abs(polygon_area_xy(pts)))


def align_loop_start(reference, loop):
    n = len(reference)
    if len(loop) != n:
        return loop
    target = reference[0]
    best_shift = min(range(n), key=lambda i: (loop[i] - target).length_squared)
    return [loop[(i + best_shift) % n] for i in range(n)]


def align_loop_to_reference(reference, loop):
    if len(reference) != len(loop):
        return loop
    cand_a = align_loop_start(reference, loop)
    cand_b = align_loop_start(reference, list(reversed(loop)))

    def cost(a, b):
        return sum((x - y).length_squared for x, y in zip(a, b))

    return cand_a if cost(reference, cand_a) <= cost(reference, cand_b) else cand_b




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
            pts = [nodes[k].copy() for k in chain[:-1]]
            loops.append(pts)

    return loops


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
                ok = True
                for u in uniq:
                    if (h - u).length < 1e-6:
                        ok = False
                        break
                if ok:
                    uniq.append(h)

            if len(uniq) == 2:
                segments.append((uniq[0], uniq[1]))
        return build_loops_from_segments(segments, tol=tol)


def mesh_section_loops_world(obj, z, tol=0.02, cache=None):
    if cache is not None:
        return cache.section_loops(z, tol=tol)

    deps = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(deps)
    me = obj_eval.to_mesh()
    me.calc_loop_triangles()
    mw = obj.matrix_world

    segments = []
    for tri in me.loop_triangles:
        ids = tri.vertices
        p = [mw @ me.vertices[i].co for i in ids]

        hits = []
        for e0, e1 in ((0, 1), (1, 2), (2, 0)):
            hp = edge_plane_intersection(p[e0], p[e1], z)
            if hp is not None:
                hits.append(hp)

        uniq = []
        for h in hits:
            ok = True
            for u in uniq:
                if (h - u).length < 1e-6:
                    ok = False
                    break
            if ok:
                uniq.append(h)

        if len(uniq) == 2:
            segments.append((uniq[0], uniq[1]))

    obj_eval.to_mesh_clear()
    return build_loops_from_segments(segments, tol=tol)


def classify_bottom_mode(raw_layers):
    # raw_layers: [{"z":..., "loops":[...]}]
    sampled = [x for x in raw_layers if x["loops"]][: min(12, len(raw_layers))]
    if len(sampled) < 3:
        return 'CYLINDER'

    if sum(1 for x in sampled[:6] if len(x["loops"]) >= 2) >= 4:
        return 'TUBE'

    areas = []
    for x in sampled:
        loops_sorted = sorted(x["loops"], key=lambda pts: abs(polygon_area_xy(pts)), reverse=True)
        areas.append(abs(polygon_area_xy(loops_sorted[0])))
    if not areas:
        return 'CYLINDER'

    first = areas[0]
    mx = max(areas)
    # Sphere-like bottoms often start from very small section area.
    if mx > 1e-9 and first / mx < 0.18:
        return 'SPHERE'
    return 'CYLINDER'


def outer_equivalent_radius(loop):
    a = abs(polygon_area_xy(loop))
    if a <= 0.0:
        return 0.0
    return math.sqrt(a / math.pi)


def generate_disk_fill_loops(outer_loop, line_width, density=1.0, max_rings=40):
    if len(outer_loop) < 3:
        return []
    count = max(64, int(len(outer_loop) * max(0.5, density)))
    outer = resample_closed_loop(outer_loop, count)
    center = sum((p for p in outer), Vector((0.0, 0.0, 0.0))) / len(outer)
    max_r = max((p - center).length for p in outer)
    if max_r <= 1e-9:
        return []
    pitch = max(0.2 * line_width, line_width * 0.95)
    rings = max(1, min(max_rings, int(max_r / pitch)))
    out = []
    for i in range(rings, 0, -1):
        t = i / rings
        ring = []
        for p in outer:
            q = center.lerp(p, t)
            q.z = outer[0].z
            ring.append(q)
        out.append(ring)
    return out


def layers_xy_bounds(layers):
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    for _, loops in layers:
        for loop in loops:
            for p in loop:
                min_x = min(min_x, p.x)
                min_y = min(min_y, p.y)
                max_x = max(max_x, p.x)
                max_y = max(max_y, p.y)
    if min_x == float("inf"):
        return None
    return min_x, min_y, max_x, max_y


def apply_bed_transform(layers, props, obj_bb):
    if not layers:
        return layers

    bb_min_x = min(v.x for v in obj_bb)
    bb_max_x = max(v.x for v in obj_bb)
    bb_min_y = min(v.y for v in obj_bb)
    bb_max_y = max(v.y for v in obj_bb)
    bb_min_z = min(v.z for v in obj_bb)

    shift_x = props.model_offset_x
    shift_y = props.model_offset_y
    if props.center_on_bed:
        obj_cx = 0.5 * (bb_min_x + bb_max_x)
        obj_cy = 0.5 * (bb_min_y + bb_max_y)
        bed_cx = 0.5 * props.bed_size_x
        bed_cy = 0.5 * props.bed_size_y
        shift_x += bed_cx - obj_cx
        shift_y += bed_cy - obj_cy

    shift_z = 0.0
    if props.place_bottom_on_bed:
        shift_z = -bb_min_z

    if abs(shift_x) < 1e-12 and abs(shift_y) < 1e-12 and abs(shift_z) < 1e-12:
        return layers

    transformed = []
    for z, loops in layers:
        moved_loops = []
        for loop in loops:
            moved = []
            for p in loop:
                q = Vector((p.x + shift_x, p.y + shift_y, p.z + shift_z))
                moved.append(q)
            moved_loops.append(moved)
        transformed.append((z + shift_z, moved_loops))
    return transformed


def is_within_bed(layers, bed_x, bed_y, margin=0.0):
    b = layers_xy_bounds(layers)
    if b is None:
        return True
    min_x, min_y, max_x, max_y = b
    return (
        min_x >= margin
        and min_y >= margin
        and max_x <= bed_x - margin
        and max_y <= bed_y - margin
    )


def calculate_stl_wave_layers(props):
    obj = props.target_object
    if obj is None or obj.type != 'MESH':
        return []

    bb = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    z_min = min(v.z for v in bb)
    z_max = max(v.z for v in bb)

    if props.limit_z_range:
        z_min = max(z_min, props.start_z)
        z_max = min(z_max, props.end_z)

    if z_max <= z_min:
        return []

    cache = MeshSliceCache(obj)
    try:
        raw_layers = []
        z = z_min + props.initial_layer_height
        while z <= z_max + 1e-9:
            loops = mesh_section_loops_world(obj, z, tol=props.join_tolerance, cache=cache)
            raw_layers.append({"z": z, "loops": loops})
            z += props.layer_height

        if props.bottom_mode == 'AUTO':
            bottom_mode = classify_bottom_mode(raw_layers)
        else:
            bottom_mode = props.bottom_mode

        all_layers = []
        prev_base_loop = None
        for layer_idx, layer in enumerate(raw_layers):
            z = layer["z"]
            loops = layer["loops"]
            if not loops:
                continue

            loops_sorted = sorted(loops, key=lambda pts: abs(polygon_area_xy(pts)), reverse=True)
            outer = get_outer_loop(loops_sorted)
            if outer is None:
                continue
            use_loops = [outer] if props.largest_loop_only else loops_sorted

            wrapped = []

            # Cylinder bottom: build plain circular base first, then start wave wall.
            if (
                props.close_bottom
                and bottom_mode == 'CYLINDER'
                and layer_idx < props.bottom_solid_layers
                and outer is not None
            ):
                disk = generate_disk_fill_loops(
                    outer_loop=outer,
                    line_width=props.line_width,
                    density=props.path_density,
                    max_rings=props.bottom_cap_max_rings,
                )
                wrapped.extend(disk)
                all_layers.append((z, wrapped))
                continue

            # Tube: no bottom closure.
            if bottom_mode == 'TUBE':
                pass
            # Sphere: keep shrinking contour naturally and keep wave count fixed.

            for loop in use_loops:
                per = loop_length_xy(loop)
                count = int(per / max(0.05, props.target_segment_length))
                count = max(props.min_points_per_loop, min(props.max_points_per_loop, count))
                sampled = resample_closed_loop(loop, count)

                sampled = canonicalize_loop_phase(sampled)
                if prev_base_loop is not None and len(prev_base_loop) == len(sampled):
                    sampled = align_loop_to_reference(prev_base_loop, sampled)
                prev_base_loop = sampled[:]

                normals = outward_normals(sampled)
                wave_loop = []
                r_eq = outer_equivalent_radius(outer) if outer is not None else 0.0
                safe_r = max(1e-6, props.bottom_wave_ramp_mm)
                ramp = max(0.0, min(1.0, r_eq / safe_r))
                for i, (p, nrm) in enumerate(zip(sampled, normals)):
                    u = i / len(sampled)
                    phase = props.phase_start + math.radians(props.twist_per_layer * layer_idx)
                    s = math.sin(2.0 * math.pi * props.frequency * u + phase)
                    # Outer contour baseline: keep toolpath at or inside the model boundary.
                    offset = -(props.surface_inset + props.base_offset) + props.amplitude * s
                    if props.prevent_outward_offset:
                        offset = min(offset, -props.surface_inset)
                    if props.adaptive_bottom_wave and bottom_mode == 'SPHERE':
                        offset *= ramp

                    wp = p + nrm * offset
                    wp.z = z
                    wave_loop.append(wp)
                wrapped.append(wave_loop)

            if wrapped:
                all_layers.append((z, wrapped))

        all_layers = apply_bed_transform(all_layers, props, bb)
        return all_layers
    finally:
        cache.clear()


class CS_OT_UpdatePreviewSTL(bpy.types.Operator):
    bl_idname = "custom_slicer.update_preview_stl"
    bl_label = "STLプレビュー更新"

    def execute(self, context):
        props = context.scene.custom_slicer_props
        for name in ["STL_Wave_Path", "STL_Wave_Preview", "STL_BuildPlate"]:
            if name in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

        layers = calculate_stl_wave_layers(props)
        if not layers:
            self.report({'WARNING'}, "輪郭が抽出できませんでした。対象オブジェクトと設定を確認してください。")
            return {'CANCELLED'}

        # Build plate frame
        mesh_b = bpy.data.meshes.new("STL_BuildPlate_Mesh")
        obj_b = bpy.data.objects.new("STL_BuildPlate", mesh_b)
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

        # Path curve preview
        curve_data = bpy.data.curves.new("STL_Wave_Path_Data", type='CURVE')
        curve_data.dimensions = '3D'
        obj_curve = bpy.data.objects.new("STL_Wave_Path", curve_data)
        context.collection.objects.link(obj_curve)

        for _, loops in layers:
            for points in loops:
                spline = curve_data.splines.new('POLY')
                spline.points.add(len(points))
                for i, p in enumerate(points + [points[0]]):
                    spline.points[i].co = (p.x, p.y, p.z, 1.0)

        obj_curve.show_in_front = True
        mat = bpy.data.materials.new(name="STL_Wave_Path_Mat")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        emi = nodes.new("ShaderNodeEmission")
        emi.inputs["Color"].default_value = (0.1, 1.0, 0.6, 1.0)
        emi.inputs["Strength"].default_value = 2.0
        links.new(emi.outputs[0], out.inputs[0])
        obj_curve.data.materials.append(mat)
        obj_curve.hide_viewport = not props.show_path

        if not is_within_bed(layers, props.bed_size_x, props.bed_size_y, props.bed_margin):
            self.report({'WARNING'}, "パスがビルドプレート範囲外です。ベッド設定またはオフセットを調整してください。")

        self.report({'INFO'}, f"プレビュー更新: {len(layers)} 層")
        return {'FINISHED'}


class CS_OT_ExportGcodeSTL(bpy.types.Operator, ExportHelper):
    bl_idname = "custom_slicer.export_gcode_stl"
    bl_label = "STL Wave G-code保存"
    filename_ext = ".gcode"

    def execute(self, context):
        props = context.scene.custom_slicer_props
        layers = calculate_stl_wave_layers(props)
        if not layers:
            self.report({'ERROR'}, "出力対象のパスがありません。先に設定を確認してください。")
            return {'CANCELLED'}
        in_bed = is_within_bed(layers, props.bed_size_x, props.bed_size_y, props.bed_margin)
        bounds = layers_xy_bounds(layers)

        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write("; --- STL XY-Wave Slicer ---\n")
                f.write(f"; bed_size={props.bed_size_x:.2f}x{props.bed_size_y:.2f} margin={props.bed_margin:.2f}\n")
                if bounds is not None:
                    min_x, min_y, max_x, max_y = bounds
                    f.write(f"; path_bounds=min({min_x:.2f},{min_y:.2f}) max({max_x:.2f},{max_y:.2f})\n")
                f.write(f"; in_bed={int(in_bed)}\n")
                f.write("G21\nG90\nM83\n")
                f.write(f"M140 S{props.bed_temp}\nM104 S{props.nozzle_temp}\n")
                f.write(f"M106 S{int(props.initial_fan_speed * 2.55)}\n")
                f.write("G28\n")
                f.write(f"M190 S{props.bed_temp}\nM109 S{props.nozzle_temp}\n")

                purge_f = props.purge_speed * 60.0
                f.write(f"G1 Z2.0 F3000\nG1 X10 Y10 Z0.28 F{purge_f:.1f}\n")
                f.write(f"G1 X10 Y200 Z0.28 E15 F{purge_f:.1f}\n")
                f.write(f"G1 X10.4 Y200 Z0.28 F{purge_f:.1f}\n")
                f.write(f"G1 X10.4 Y20 Z0.28 E30 F{purge_f:.1f}\n")
                f.write("G92 E0\nG1 Z2.0 F3000\n")

                for layer_idx, (z, loops) in enumerate(layers):
                    f.write(f"\n; LAYER:{layer_idx} Z:{z:.3f}\n")
                    curr_h = props.initial_layer_height if layer_idx == 0 else props.layer_height
                    print_f = (props.initial_speed if layer_idx == 0 else props.regular_speed) * 60.0
                    travel_f = (props.initial_travel_speed if layer_idx == 0 else props.travel_speed) * 60.0

                    if layer_idx == 1:
                        f.write(f"M106 S{int(props.fan_speed * 2.55)}\n")

                    for loop in loops:
                        first = loop[0]
                        f.write(f"G0 X{first.x:.3f} Y{first.y:.3f} Z{z:.3f} F{travel_f:.1f}\n")
                        prev = first
                        for p in loop[1:] + [first]:
                            dist = (p - prev).length
                            e_val = dist * props.line_width * curr_h * props.extrusion_gain
                            f.write(f"G1 X{p.x:.3f} Y{p.y:.3f} E{e_val:.5f} F{print_f:.1f}\n")
                            prev = p

                f.write("\n; --- End G-code ---\n")
                f.write("M104 S0\nM140 S0\nM107\nG1 E-2 F900\nG28 X0\nM84\n")

            if in_bed:
                self.report({'INFO'}, "保存完了")
            else:
                self.report({'WARNING'}, "保存完了（パスがビルドプレート範囲外です）")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        return {'FINISHED'}


class CustomSlicerProperties(bpy.types.PropertyGroup):
    target_object: bpy.props.PointerProperty(
        name="対象メッシュ",
        type=bpy.types.Object,
        description="STLを読み込んだメッシュオブジェクトを選択",
    )
    bed_size_x: bpy.props.FloatProperty(name="ベッドX", default=220.0, min=1.0)
    bed_size_y: bpy.props.FloatProperty(name="ベッドY", default=220.0, min=1.0)
    bed_margin: bpy.props.FloatProperty(name="ベッド余白", default=0.0, min=0.0, max=50.0, precision=2)
    center_on_bed: bpy.props.BoolProperty(name="モデル中心をベッド中心に配置", default=True)
    place_bottom_on_bed: bpy.props.BoolProperty(name="底面をZ=0に合わせる", default=True)
    model_offset_x: bpy.props.FloatProperty(name="モデルXオフセット", default=0.0, precision=3)
    model_offset_y: bpy.props.FloatProperty(name="モデルYオフセット", default=0.0, precision=3)
    nozzle_temp: bpy.props.IntProperty(name="ノズル温度", default=200, min=0, max=350)
    bed_temp: bpy.props.IntProperty(name="ベッド温度", default=60, min=0, max=150)
    initial_fan_speed: bpy.props.IntProperty(name="1層目ファン (%)", default=0, min=0, max=100)
    fan_speed: bpy.props.IntProperty(name="通常ファン (%)", default=100, min=0, max=100)

    purge_speed: bpy.props.FloatProperty(name="パージ速度 (mm/s)", default=25.0, min=0.1, precision=2)
    initial_travel_speed: bpy.props.FloatProperty(name="1層目トラベル (mm/s)", default=50.0, min=0.1, precision=2)
    travel_speed: bpy.props.FloatProperty(name="通常トラベル (mm/s)", default=120.0, min=0.1, precision=2)
    initial_speed: bpy.props.FloatProperty(name="1層目造形速度 (mm/s)", default=15.0, min=0.1, precision=2)
    regular_speed: bpy.props.FloatProperty(name="通常造形速度 (mm/s)", default=45.0, min=0.1, precision=2)

    base_offset: bpy.props.FloatProperty(name="基本オフセット", default=0.8, min=0.0)
    amplitude: bpy.props.FloatProperty(name="振幅", default=0.5, min=0.0)
    frequency: bpy.props.FloatProperty(name="波の数", default=18.0, min=0.1)
    phase_start: bpy.props.FloatProperty(name="開始位相 (rad)", default=0.0)
    twist_per_layer: bpy.props.FloatProperty(name="層ごと位相回転 (deg)", default=2.0)
    path_density: bpy.props.FloatProperty(name="パス密度", default=1.2, min=0.2, max=5.0)
    target_segment_length: bpy.props.FloatProperty(name="目標セグメント長", default=0.6, min=0.05, max=5.0, precision=3)
    min_points_per_loop: bpy.props.IntProperty(name="最小点数", default=96, min=16, max=2000)
    max_points_per_loop: bpy.props.IntProperty(name="最大点数", default=360, min=32, max=4000)
    join_tolerance: bpy.props.FloatProperty(name="輪郭接続許容", default=0.02, min=0.001, max=1.0, precision=4)
    largest_loop_only: bpy.props.BoolProperty(name="最大輪郭のみ", default=True)
    surface_inset: bpy.props.FloatProperty(name="表面インセット", default=0.05, min=0.0, max=5.0, precision=3)
    prevent_outward_offset: bpy.props.BoolProperty(name="外側へのはみ出し禁止", default=True)
    adaptive_bottom_wave: bpy.props.BoolProperty(name="底付近で波を徐々に適用", default=True)
    bottom_wave_ramp_mm: bpy.props.FloatProperty(name="底波ランプ半径(mm)", default=6.0, min=0.1, max=100.0, precision=2)
    close_bottom: bpy.props.BoolProperty(name="底面を閉じる", default=True)
    bottom_mode: bpy.props.EnumProperty(
        name="底面モード",
        items=[
            ('AUTO', "自動判定", "形状から球/円柱/管を推定"),
            ('SPHERE', "球系", "波数固定のまま収束"),
            ('CYLINDER', "円柱系", "底面ディスク後に波を適用"),
            ('TUBE', "管系", "底面なし"),
        ],
        default='AUTO',
    )
    bottom_solid_layers: bpy.props.IntProperty(name="底面レイヤー数", default=4, min=0, max=100)
    bottom_cap_max_rings: bpy.props.IntProperty(name="底面最大リング数", default=28, min=1, max=300)

    line_width: bpy.props.FloatProperty(name="線幅", default=0.45, min=0.05)
    extrusion_gain: bpy.props.FloatProperty(name="押出ゲイン", default=1.25, min=0.01)
    initial_layer_height: bpy.props.FloatProperty(name="1層目ピッチ", default=0.3, min=0.01)
    layer_height: bpy.props.FloatProperty(name="通常ピッチ", default=0.2, min=0.01)

    limit_z_range: bpy.props.BoolProperty(name="Z範囲を指定", default=False)
    start_z: bpy.props.FloatProperty(name="開始Z", default=0.0)
    end_z: bpy.props.FloatProperty(name="終了Z", default=20.0)
    show_path: bpy.props.BoolProperty(name="パスを表示", default=True)


class CS_PT_MainPanelSTL(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Custom Slicer'
    bl_label = "STL XY Wave Slicer"

    def draw(self, context):
        p = context.scene.custom_slicer_props
        layout = self.layout

        box = layout.box()
        box.label(text="Build Plate")
        row = box.row()
        row.prop(p, "bed_size_x")
        row.prop(p, "bed_size_y")
        box.prop(p, "bed_margin")
        box.prop(p, "center_on_bed")
        box.prop(p, "place_bottom_on_bed")
        row = box.row()
        row.prop(p, "model_offset_x")
        row.prop(p, "model_offset_y")

        box = layout.box()
        box.label(text="Target")
        box.prop(p, "target_object")
        box.prop(p, "surface_inset")
        box.prop(p, "prevent_outward_offset")
        box.prop(p, "adaptive_bottom_wave")
        if p.adaptive_bottom_wave:
            box.prop(p, "bottom_wave_ramp_mm")
        box.prop(p, "largest_loop_only")
        box.prop(p, "join_tolerance")
        box.prop(p, "target_segment_length")
        row = box.row()
        row.prop(p, "min_points_per_loop")
        row.prop(p, "max_points_per_loop")
        box.prop(p, "close_bottom")
        if p.close_bottom:
            box.prop(p, "bottom_mode")
            row = box.row()
            row.prop(p, "bottom_solid_layers")
            row.prop(p, "bottom_cap_max_rings")

        box = layout.box()
        box.label(text="Wave Shape")
        box.prop(p, "base_offset")
        box.prop(p, "amplitude")
        box.prop(p, "frequency")
        box.prop(p, "phase_start")
        box.prop(p, "twist_per_layer")
        box.prop(p, "path_density")

        box = layout.box()
        box.label(text="Layer")
        row = box.row()
        row.prop(p, "initial_layer_height")
        row.prop(p, "layer_height")
        box.prop(p, "limit_z_range")
        if p.limit_z_range:
            row = box.row()
            row.prop(p, "start_z")
            row.prop(p, "end_z")

        box = layout.box()
        box.label(text="Print")
        row = box.row()
        row.prop(p, "nozzle_temp")
        row.prop(p, "bed_temp")
        row = box.row()
        row.prop(p, "initial_fan_speed")
        row.prop(p, "fan_speed")
        box.prop(p, "line_width")
        box.prop(p, "extrusion_gain")
        box.prop(p, "purge_speed")
        row = box.row()
        row.prop(p, "initial_travel_speed")
        row.prop(p, "travel_speed")
        row = box.row()
        row.prop(p, "initial_speed")
        row.prop(p, "regular_speed")

        layout.separator()
        layout.prop(p, "show_path", icon='CURVE_PATH')
        layout.operator("custom_slicer.update_preview_stl", icon='FILE_REFRESH')
        layout.operator("custom_slicer.export_gcode_stl", icon='EXPORT')


classes = (
    CustomSlicerProperties,
    CS_OT_UpdatePreviewSTL,
    CS_OT_ExportGcodeSTL,
    CS_PT_MainPanelSTL,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.custom_slicer_props = bpy.props.PointerProperty(type=CustomSlicerProperties)


def unregister():
    if hasattr(bpy.types.Scene, "custom_slicer_props"):
        del bpy.types.Scene.custom_slicer_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
