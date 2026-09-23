bl_info = {
    "name": "Model Library Shelf",
    "author": "Olivershot1",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Ctrl+Shift+L opens/closes the Asset Browser at the bottom",
    "description": "Bottom Asset Browser for your own models: save selected objects with textures, folders (catalogs), search",
    "category": "Import-Export",
}

#------------------------------------
# Still issue with the texures trying to make it as smooth as in can should be fixed for the next update i hope.
#------------------------------------
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid

import bpy
import gpu
import numpy as np
from bpy.props import EnumProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel
from mathutils import Matrix, Vector

LIB_NAME = "My Models"
CATS_FILE = "blender_assets.cats.txt"
THUMB_SIZE = 256
BOTTOM_FRACTION = 0.33

STATE = {"area_ptr": None}


# ----------------------------------------------------------------------------
# Preferences / library registration
# ----------------------------------------------------------------------------

def get_lib_dir():
    prefs = bpy.context.preferences.addons[__name__].preferences
    path = os.path.abspath(bpy.path.abspath(prefs.library_path))
    os.makedirs(path, exist_ok=True)
    return path


def ensure_library():
    """Register the folder as an asset library in Blender's preferences."""
    try:
        path = get_lib_dir()
        libs = bpy.context.preferences.filepaths.asset_libraries
        for lib in libs:
            if lib.name == LIB_NAME:
                if os.path.abspath(bpy.path.abspath(lib.path)) != path:
                    lib.path = path
                return
        libs.new(name=LIB_NAME, directory=path)
    except Exception as e:
        print("Model Library: could not register asset library:", e)


def _prefs_update(self, context):
    ensure_library()


class MODELLIB_Preferences(AddonPreferences):
    bl_idname = __name__

    library_path: StringProperty(
        name="Library Folder",
        subtype='DIR_PATH',
        default=os.path.join(os.path.expanduser("~"), "BlenderModelLibrary"),
        update=_prefs_update,
    )

    def draw(self, context):
        self.layout.prop(self, "library_path")


# ----------------------------------------------------------------------------
# Catalog (folder) helpers - writes Blender's blender_assets.cats.txt
# ----------------------------------------------------------------------------

CATS_HEADER = (
    "# This is an Asset Catalog Definition file for Blender.\n"
    "#\n"
    "# Empty lines and lines starting with `#` will be ignored.\n"
    "# The first non-ignored line should be the version indicator.\n"
    "# Other lines are of the format \"UUID:catalog/path/for/assets:simple catalog name\"\n"
    "\n"
    "VERSION 1\n"
    "\n"
)


def _catalog_parts(catalog_path):
    return [re.sub(r'[:*?"<>|]', "_", p.strip())
            for p in catalog_path.replace("\\", "/").split("/") if p.strip()]


def catalog_folder(lib_dir, catalog_path):
    """Physical folder on disk matching a catalog path, e.g. 'Props/Industrial'."""
    parts = _catalog_parts(catalog_path)
    folder = os.path.join(lib_dir, *parts) if parts else lib_dir
    os.makedirs(folder, exist_ok=True)
    return folder


def ensure_catalog(lib_dir, catalog_path):
    parts = _catalog_parts(catalog_path)
    if not parts:
        return None
    os.makedirs(os.path.join(lib_dir, *parts), exist_ok=True)

    fp = os.path.join(lib_dir, CATS_FILE)
    existing = {}
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("VERSION"):
                    continue
                bits = line.split(":", 2)
                if len(bits) == 3:
                    existing[bits[1]] = bits[0]
    else:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(CATS_HEADER)

    new_lines, uid = [], None
    for i in range(len(parts)):
        p = "/".join(parts[:i + 1])
        if p not in existing:
            existing[p] = str(uuid.uuid4())
            new_lines.append(f"{existing[p]}:{p}:{p.replace('/', '-')}\n")
        uid = existing[p]

    if new_lines:
        with open(fp, "a", encoding="utf-8") as f:
            f.writelines(new_lines)
    return uid


# ----------------------------------------------------------------------------
# Thumbnail with textures
# ----------------------------------------------------------------------------

def find_view3d(context):
    area = context.area if (context.area and context.area.type == 'VIEW_3D') else None
    if area is None:
        for a in context.window.screen.areas:
            if a.type == 'VIEW_3D':
                area = a
                break
    if area is None:
        return None, None, None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    return area, region, area.spaces.active


def _perspective(fov, aspect, near, far):
    f = 1.0 / math.tan(fov / 2.0)
    return Matrix((
        (f / aspect, 0, 0, 0),
        (0, f, 0, 0),
        (0, 0, -(far + near) / (far - near), -2.0 * far * near / (far - near)),
        (0, 0, -1, 0),
    ))


def bbox_of(objs):
    corners = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    mn = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    mx = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return mn, mx


def render_thumbnail(context, objs, size=THUMB_SIZE):
    """Returns a flat float32 RGBA numpy array, or None."""
    area, region, space = find_view3d(context)
    if space is None or region is None:
        return None

    mn, mx = bbox_of(objs)
    center = (mn + mx) / 2.0
    radius = max((mx - mn).length / 2.0, 0.001)

    fov = math.radians(35)
    dist = radius / math.sin(fov / 2.0) * 1.1
    direction = Vector((0.8, -1.0, 0.6)).normalized()
    loc = center + direction * dist
    quat = (-direction).to_track_quat('-Z', 'Y')
    cam = Matrix.Translation(loc) @ quat.to_matrix().to_4x4()
    view = cam.inverted()
    proj = _perspective(fov, 1.0, max(dist - radius * 1.5, 0.001), dist + radius * 2.0)

    vl = context.view_layer
    hidden = []
    for o in vl.objects:
        if o not in objs and not o.hide_get():
            o.hide_set(True)
            hidden.append(o)

    sh = space.shading
    old = (sh.type, sh.light, sh.color_type)
    sh.type = 'SOLID'
    sh.light = 'STUDIO'
    sh.color_type = 'TEXTURE'
    vl.update()

    offscreen = gpu.types.GPUOffScreen(size, size)
    try:
        offscreen.draw_view3d(context.scene, vl, space, region, view, proj, do_color_management=True)
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            buf = fb.read_color(0, 0, size, size, 4, 0, 'UBYTE')
        buf.dimensions = size * size * 4
        arr = np.array(buf, dtype=np.float32) / 255.0
        arr[3::4] = 1.0
        return arr
    finally:
        offscreen.free()
        sh.type, sh.light, sh.color_type = old
        for o in hidden:
            o.hide_set(False)


def pack_textures(objs):
    for o in objs:
        for slot in getattr(o, "material_slots", []):
            mat = slot.material
            if mat and mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image and not node.image.packed_file:
                        try:
                            node.image.pack()
                        except Exception:
                            pass


def refresh_asset_browsers(context):
    for win in context.window_manager.windows:
        for a in win.screen.areas:
            if a.type == 'FILE_BROWSER' and getattr(a.spaces.active, "browse_mode", "") == 'ASSETS':
                region = next((r for r in a.regions if r.type == 'WINDOW'), None)
                try:
                    with context.temp_override(window=win, screen=win.screen, area=a, region=region):
                        bpy.ops.asset.library_refresh()
                except Exception:
                    pass


SYNC_SCRIPT = r'''
import bpy, sys, os

blend, cat_id = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(blend, link=False) as (df, dt):
    dt.collections = list(df.collections)
    dt.materials = list(df.materials)
ids = [c for c in dt.collections if c] + [m for m in dt.materials if m]
if not ids:
    raise SystemExit("No assets found in " + blend)

bpy.data.orphans_purge(do_local_ids=False, do_linked_ids=True, do_recursive=True)
for lib in list(bpy.data.libraries):
    try:
        if os.path.abspath(bpy.path.abspath(lib.filepath)) == os.path.abspath(blend):
            bpy.data.libraries.remove(lib)
    except Exception:
        pass

changed = False
for d in ids:
    if d.asset_data is None:
        d.asset_mark()
        changed = True
    if d.asset_data.catalog_id != cat_id:
        d.asset_data.catalog_id = cat_id
        changed = True
    d.use_fake_user = True

if changed:
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
print("MODELLIB_SYNC_CHANGED" if changed else "MODELLIB_SYNC_NOCHANGE")
'''


def sync_blend_catalog(blend, cat_id):
    """Makes every asset in blend belong to cat_id, matching its folder on disk.
    Returns (ok, changed, message)."""
    script = os.path.join(tempfile.gettempdir(), "modellib_sync.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(SYNC_SCRIPT)

    backup = blend + ".modellib_bak"
    shutil.copy2(blend, backup)
    try:
        res = subprocess.run(
            [bpy.app.binary_path, "-b", "--factory-startup", "--python", script,
             "--", blend, cat_id],
            capture_output=True, text=True, timeout=180,
        )
        if res.returncode != 0:
            shutil.copy2(backup, blend)
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:]
            return False, False, str(tail)
        return True, "MODELLIB_SYNC_CHANGED" in res.stdout, ""
    except Exception as e:
        shutil.copy2(backup, blend)
        return False, False, str(e)
    finally:
        try:
            os.remove(backup)
        except OSError:
            pass


class MODELLIB_OT_new_folder(Operator):
    bl_idname = "modellib.new_folder"
    bl_label = "New Folder"
    bl_description = "Create a new folder in your library"

    parent: StringProperty(
        name="Inside",
        description="Parent folder path, e.g.  Props  (leave empty to create it at the top level)",
        default="",
    )
    name: StringProperty(name="Folder Name", default="New Folder")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        self.layout.prop(self, "parent")
        self.layout.prop(self, "name")

    def execute(self, context):
        name = self.name.strip()
        if not name:
            self.report({'WARNING'}, "Type a folder name")
            return {'CANCELLED'}
        lib = get_lib_dir()
        path = f"{self.parent.strip()}/{name}" if self.parent.strip() else name
        ensure_catalog(lib, path)
        try:
            bpy.ops.asset.catalogs_save()
        except Exception:
            pass
        refresh_asset_browsers(context)
        self.report({'INFO'}, f"Created folder '{path}'")
        return {'FINISHED'}


class MODELLIB_OT_detect_folders(Operator):
    bl_idname = "modellib.detect_folders"
    bl_label = "Detect Folders"
    bl_description = ("Scan your library folder for folders and .blend files added or moved "
                       "outside Blender, and match Blender's folders to them")

    def execute(self, context):
        lib = get_lib_dir()

        # 1. Register every physical folder as a catalog.
        for dirpath, dirnames, filenames in os.walk(lib):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = os.path.relpath(dirpath, lib)
            if rel != ".":
                ensure_catalog(lib, rel.replace(os.sep, "/"))
        try:
            bpy.ops.asset.catalogs_save()
        except Exception:
            pass

        # 2. Make sure every .blend file's catalog matches the folder it's actually in.
        updated, checked = 0, 0
        for dirpath, dirnames, filenames in os.walk(lib):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = os.path.relpath(dirpath, lib)
            cat_path = "" if rel == "." else rel.replace(os.sep, "/")
            cat_id = ensure_catalog(lib, cat_path) if cat_path else NULL_UUID
            cat_id = cat_id or NULL_UUID
            for f in filenames:
                if not f.lower().endswith(".blend"):
                    continue
                checked += 1
                ok, changed, msg = sync_blend_catalog(os.path.join(dirpath, f), cat_id)
                if not ok:
                    self.report({'ERROR'}, f"{f}: {msg}")
                elif changed:
                    updated += 1

        refresh_asset_browsers(context)
        self.report({'INFO'}, f"Checked {checked} file(s), updated {updated}")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Save selected objects as an asset (with textures + preview + folder)
# ----------------------------------------------------------------------------

def _texture_preview_pixels(img, size=128):
    """Downsize a copy of img and return its raw pixels for use as an asset preview."""
    thumb = img.copy()
    thumb.name = "_modellib_thumb_tmp"
    try:
        thumb.scale(size, size)
        pixels = list(thumb.pixels)
    finally:
        bpy.data.images.remove(thumb)
    return pixels, size


TEXTURE_PREVIEW_SCRIPT = r'''
import bpy, sys, os, math
from mathutils import Vector

blend, shape = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(blend, link=False) as (df, dt):
    dt.materials = list(df.materials)
mats = [m for m in dt.materials if m]
if not mats:
    raise SystemExit("No material found in " + blend)
mat = mats[0]

# Appended data can still show the source file as a "used library" until
# orphaned references are cleared - clear it now so we can save back over
# the same file.
bpy.data.orphans_purge(do_local_ids=False, do_linked_ids=True, do_recursive=True)
for lib in list(bpy.data.libraries):
    try:
        if os.path.abspath(bpy.path.abspath(lib.filepath)) == os.path.abspath(blend):
            bpy.data.libraries.remove(lib)
    except Exception:
        pass

if shape == 'CUBE':
    bpy.ops.mesh.primitive_cube_add(size=2)
elif shape == 'PLANE':
    bpy.ops.mesh.primitive_plane_add(size=2)
else:
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=48, ring_count=24)
obj = bpy.context.active_object
obj.data.materials.append(mat)
for p in obj.data.polygons:
    p.use_smooth = (shape != 'CUBE')

scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
size = 256
scene.render.resolution_x = size
scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
png = os.path.splitext(blend)[0] + ".png"
scene.render.filepath = png
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'TEXTURE'

radius = 1.3
cam_data = bpy.data.cameras.new("_cam")
cam_data.angle = math.radians(35)
cam = bpy.data.objects.new("_cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
dist = radius / math.sin(cam_data.angle / 2.0) * 1.1
direction = Vector((0.8, -1.0, 0.6)).normalized()
cam.location = direction * dist
cam.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
cam_data.clip_start = max(dist * 0.01, 0.001)
cam_data.clip_end = dist * 5.0
bpy.context.view_layer.update()

bpy.ops.render.render(write_still=True)
print("MODELLIB_TEXTURE_RENDER_OK" if os.path.exists(png) else "MODELLIB_TEXTURE_RENDER_FAILED")

if os.path.exists(png):
    pv_img = bpy.data.images.load(png)
    w, h = pv_img.size
    pv = mat.preview_ensure()
    pv.image_size = (w, h)
    pv.image_pixels_float.foreach_set(list(pv_img.pixels))

if mat.asset_data is None:
    mat.asset_mark()
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
'''


def render_texture_preview(blend_path, shape):
    """Renders the saved image onto a sphere/cube/plane and stores it as the asset preview.
    Returns (ok, message)."""
    script = os.path.join(tempfile.gettempdir(), "modellib_texture_preview.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(TEXTURE_PREVIEW_SCRIPT)

    backup = blend_path + ".modellib_bak"
    shutil.copy2(blend_path, backup)
    try:
        res = subprocess.run(
            [bpy.app.binary_path, "-b", "--factory-startup", "--python", script,
             "--", blend_path, shape],
            capture_output=True, text=True, timeout=180,
        )
        print("---- Model Library: texture preview output ----")
        print(res.stdout)
        if res.stderr:
            print(res.stderr)
        print("---- Model Library: end of output (returncode %s) ----" % res.returncode)

        if res.returncode != 0:
            shutil.copy2(backup, blend_path)
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:]
            return False, str(tail) + " - see System Console for details"
        if "MODELLIB_TEXTURE_RENDER_OK" not in res.stdout:
            return True, "Preview render did not confirm success - see System Console for details"
        return True, ""
    except Exception as e:
        shutil.copy2(backup, blend_path)
        return False, str(e)
    finally:
        try:
            os.remove(backup)
        except OSError:
            pass


class MODELLIB_OT_set_preview_shape(Operator):
    bl_idname = "modellib.set_preview_shape"
    bl_label = "Set Preview Shape"
    bl_description = "Re-render the preview of the selected texture/material asset(s) on a different shape"
    bl_options = {'REGISTER'}

    shape: EnumProperty(
        items=[('SPHERE', "Sphere", ""), ('CUBE', "Cube", ""), ('PLANE', "Plane", "")],
        default='SPHERE',
    )

    def execute(self, context):
        paths = selected_asset_blends(context)
        if not paths:
            self.report({'WARNING'}, "Select a texture/material asset in the browser first")
            return {'CANCELLED'}

        done = 0
        for blend in paths:
            if not os.path.exists(blend):
                continue
            ok, msg = render_texture_preview(blend, self.shape)
            if ok and not msg:
                done += 1
            elif not ok:
                self.report({'ERROR'}, f"{os.path.basename(blend)}: {msg}")
            elif msg:
                self.report({'WARNING'}, f"{os.path.basename(blend)}: {msg}")

        refresh_asset_browsers(context)
        if done:
            self.report({'INFO'}, f"Updated preview for {done} asset(s)")
        return {'FINISHED'}


class MODELLIB_OT_save_texture(Operator):
    bl_idname = "modellib.save_texture"
    bl_label = "Save Texture to Library"
    bl_description = "Save this texture into your library so you can reuse it later"

    name: StringProperty(name="Name", default="Texture")
    shape: EnumProperty(
        name="Preview Shape",
        description="Shape the preview thumbnail shows the texture on",
        items=[('SPHERE', "Sphere", ""), ('CUBE', "Cube", ""), ('PLANE', "Plane", "")],
        default='SPHERE',
    )
    catalog: StringProperty(
        name="Folder",
        description="Folder inside the library, e.g.  Textures/Metal  (leave empty for Unassigned)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        img = getattr(context.space_data, "image", None)
        return img is not None

    def invoke(self, context, event):
        img = context.space_data.image
        self.name = (bpy.path.display_name_from_filepath(img.filepath)
                     if img.filepath else img.name) or "Texture"
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        self.layout.prop(self, "name")
        self.layout.prop(self, "shape")
        self.layout.prop(self, "catalog")

    def execute(self, context):
        img = context.space_data.image
        if img is None:
            self.report({'WARNING'}, "No texture open to save")
            return {'CANCELLED'}

        ensure_library()
        folder = get_lib_dir()

        base = re.sub(r'[\\/:*?"<>|]', "_", self.name).strip() or "Texture"
        save_dir = catalog_folder(folder, self.catalog) if self.catalog.strip() else folder
        safe, n = base, 1
        while os.path.exists(os.path.join(save_dir, safe + ".blend")):
            safe = f"{base}.{n:03d}"
            n += 1
        blend_path = os.path.join(save_dir, safe + ".blend")

        if not img.packed_file:
            try:
                img.pack()
            except Exception:
                pass

        # Images can't be shown in the Asset Browser, so wrap it in a
        # material with the texture already wired into Base Color.
        mat = bpy.data.materials.new(safe)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
        tex_node.image = img
        tex_node.location = (-300, 300)
        if bsdf:
            mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])

        mat.asset_mark()
        if self.catalog.strip():
            uid = ensure_catalog(folder, self.catalog)
            if uid:
                mat.asset_data.catalog_id = uid

        try:
            bpy.data.libraries.write(blend_path, {mat}, compress=True)
        finally:
            bpy.data.materials.remove(mat)

        ok, msg = render_texture_preview(blend_path, self.shape)
        if not ok:
            self.report({'WARNING'}, f"Saved, but preview failed: {msg}")
        elif msg:
            self.report({'WARNING'}, msg)

        refresh_asset_browsers(context)
        self.report({'INFO'}, f"Saved '{safe}' to library")
        return {'FINISHED'}


_target_file_items_cache = []


def _target_file_items(self, context):
    items = [("", "New File", "")]
    try:
        root = get_lib_dir()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in sorted(filenames):
                if f.lower().endswith(".blend"):
                    rel = os.path.relpath(os.path.join(dirpath, f), root)[:-6]
                    items.append((rel, rel, ""))
    except Exception:
        pass
    _target_file_items_cache[:] = items
    return _target_file_items_cache


class MODELLIB_OT_save(Operator):
    bl_idname = "modellib.save_selected"
    bl_label = "Save Selected to Library"
    bl_description = "Save the selected objects, with their textures, into your model library"

    name: StringProperty(name="Name", default="Model")
    catalog: StringProperty(
        name="Folder",
        description="Folder inside the library, e.g.  Props/Industrial  (leave empty for Unassigned)",
        default="",
    )
    target_file: EnumProperty(
        name="Add Into",
        items=_target_file_items,
        description="Add this model into an existing library file instead of creating a new one",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(o.select_get() for o in context.view_layer.objects)

    def invoke(self, context, event):
        if context.active_object:
            self.name = context.active_object.name
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        self.layout.prop(self, "name")
        self.layout.prop(self, "catalog")
        self.layout.prop(self, "target_file")

    def execute(self, context):
        objs = [o for o in context.view_layer.objects if o.select_get()]
        if not objs:
            self.report({'WARNING'}, "Select some objects first")
            return {'CANCELLED'}
        # also take everything parented under the selection (meshes under an armature, etc.)
        everything = set(objs)
        for o in objs:
            everything.update(o.children_recursive)
        objs = list(everything)

        ensure_library()
        folder = get_lib_dir()

        col_base = re.sub(r'[\\/:*?"<>|]', "_", self.name).strip() or "Model"

        if self.target_file:
            # target_file is a path relative to the library root, e.g. "Props/Industrial/Bin"
            blend_path = os.path.join(folder, self.target_file + ".blend")
        else:
            save_dir = catalog_folder(folder, self.catalog) if self.catalog.strip() else folder
            file_base, n = col_base, 1
            while os.path.exists(os.path.join(save_dir, file_base + ".blend")):
                file_base = f"{col_base}.{n:03d}"
                n += 1
            blend_path = os.path.join(save_dir, file_base + ".blend")

        pack_textures(objs)

        mn, mx = bbox_of(objs)
        col = bpy.data.collections.new(col_base)
        for o in objs:
            col.objects.link(o)
        col.instance_offset = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))

        col.asset_mark()
        cat_id = NULL_UUID
        if self.catalog.strip():
            uid = ensure_catalog(folder, self.catalog)
            if uid:
                cat_id = uid
                col.asset_data.catalog_id = uid

        # If adding into an existing file, load what's already in it so the
        # write below doesn't wipe those other models out.
        existing_cols = []
        if self.target_file and os.path.exists(blend_path):
            try:
                with bpy.data.libraries.load(blend_path, link=False) as (df, dt):
                    dt.collections = list(df.collections)
                existing_cols = [c for c in dt.collections if c]
                bpy.data.orphans_purge(do_local_ids=False, do_linked_ids=True, do_recursive=True)
                for lib in list(bpy.data.libraries):
                    try:
                        if os.path.abspath(bpy.path.abspath(lib.filepath)) == os.path.abspath(blend_path):
                            bpy.data.libraries.remove(lib)
                    except Exception:
                        pass
            except Exception as e:
                self.report({'WARNING'}, f"Could not read '{self.target_file}', it will be replaced: {e}")
                existing_cols = []

        target_name = col.name  # capture in case Blender auto-renamed it to avoid a clash
        try:
            bpy.data.libraries.write(blend_path, set(existing_cols) | {col}, compress=True)
        finally:
            bpy.data.collections.remove(col)
            for c in existing_cols:
                bpy.data.collections.remove(c)

        # re-save in a background Blender so the preview and folder are stored properly
        ok, msg = rewrite_asset_file(blend_path, cat_id, render=True, target_name=target_name)
        if not ok:
            self.report({'WARNING'}, f"Saved, but finishing step failed: {msg}")
        elif msg:
            self.report({'WARNING'}, msg)

        refresh_asset_browsers(context)
        self.report({'INFO'}, f"Saved '{target_name}' to library")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Move selected models into a folder (catalog)
# ----------------------------------------------------------------------------
# Blender only lets you drag assets onto folders when the asset lives in the
# file you currently have open. Our models live in the library, so instead we
# rewrite the model's file in a background Blender with the new folder set.

NO_FOLDER = "__NONE__"
NULL_UUID = "00000000-0000-0000-0000-000000000000"
_folder_items_cache = []

BACKGROUND_SCRIPT = r'''
import bpy, sys, os, math
from mathutils import Vector

blend, cat_id, png, mode, target_name = sys.argv[sys.argv.index("--") + 1:][:5]
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(blend, link=False) as (df, dt):
    dt.collections = list(df.collections)
cols = [c for c in dt.collections if c]
if not cols:
    raise SystemExit("No collections found in " + blend)
# Appended data can still show the source file as a "used library" until
# orphaned references are cleared - clear it now so we can save back over
# the same file.
bpy.data.orphans_purge(do_local_ids=False, do_linked_ids=True, do_recursive=True)
for lib in list(bpy.data.libraries):
    try:
        if os.path.abspath(bpy.path.abspath(lib.filepath)) == os.path.abspath(blend):
            bpy.data.libraries.remove(lib)
    except Exception:
        pass

GEO = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'CURVES', 'POINTCLOUD', 'VOLUME'}


def render_preview(col, path, size=256):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.use_file_extension = False
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = path
    scene.view_settings.view_transform = 'Standard'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'TEXTURE'

    scene.collection.children.link(col)
    bpy.context.view_layer.update()
    objs = [o for o in col.all_objects if o.type in GEO] or list(col.all_objects)
    corners = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not corners:
        return False
    mn = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    mx = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    center = (mn + mx) / 2.0
    radius = max((mx - mn).length / 2.0, 0.001)

    cam_data = bpy.data.cameras.new("_cam")
    cam_data.angle = math.radians(35)
    cam = bpy.data.objects.new("_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    dist = radius / math.sin(cam_data.angle / 2.0) * 1.1
    direction = Vector((0.8, -1.0, 0.6)).normalized()
    cam.location = center + direction * dist
    cam.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
    cam_data.clip_start = max(dist * 0.01, 0.001)
    cam_data.clip_end = dist * 5.0
    bpy.context.view_layer.update()

    bpy.ops.render.render(write_still=True)

    scene.collection.children.unlink(col)
    bpy.data.objects.remove(cam)
    bpy.data.cameras.remove(cam_data)
    return os.path.exists(path)


# When target_name is given, only that collection is touched (this file
# may already contain other collections that were saved earlier and should
# keep their own catalog/preview untouched). Empty target_name means "all"
# (used when moving a whole file to a different folder).
targets = [c for c in cols if c.name == target_name] if target_name else cols
if not targets:
    targets = cols
col = targets[0]

if mode == "render" or not os.path.exists(png):
    try:
        print("MODELLIB_RENDER_OK" if render_preview(col, png) else "MODELLIB_RENDER_FAILED: empty model")
    except Exception as e:
        print("MODELLIB_RENDER_FAILED:", e)

for c in targets:
    if c.asset_data is None:
        c.asset_mark()
    c.use_fake_user = True
    c.asset_data.catalog_id = cat_id
    if os.path.exists(png):
        img = bpy.data.images.load(png)
        w, h = img.size
        pv = c.preview_ensure()
        pv.image_size = (w, h)
        pv.image_pixels_float.foreach_set(list(img.pixels))
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
'''


def rewrite_asset_file(blend, cat_id, render=False, target_name=""):
    """Re-saves a model file in a background Blender with folder + preview applied.
    Returns (ok, message). A preview problem is reported in message even when ok is True.
    target_name: if given, only that collection is touched, leaving any other
    collections already in the file (from earlier saves) untouched."""
    png = os.path.splitext(blend)[0] + ".png"
    script = os.path.join(tempfile.gettempdir(), "modellib_rewrite.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(BACKGROUND_SCRIPT)

    backup = blend + ".modellib_bak"
    shutil.copy2(blend, backup)
    try:
        res = subprocess.run(
            [bpy.app.binary_path, "-b", "--factory-startup", "--python", script,
             "--", blend, cat_id, png, "render" if render else "keep", target_name],
            capture_output=True, text=True, timeout=300,
        )
        print("---- Model Library: background process output ----")
        print(res.stdout)
        if res.stderr:
            print(res.stderr)
        print("---- Model Library: end of output (returncode %s) ----" % res.returncode)

        if res.returncode != 0:
            shutil.copy2(backup, blend)
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:]
            return False, str(tail) + " - see System Console for details"
        if "MODELLIB_RENDER_OK" not in res.stdout:
            for line in res.stdout.splitlines():
                if line.startswith("MODELLIB_RENDER_FAILED"):
                    return True, line + " - see System Console for details"
            if render:
                return True, "Preview render did not confirm success - see System Console for details"
        return True, ""
    except Exception as e:
        shutil.copy2(backup, blend)
        return False, str(e)
    finally:
        try:
            os.remove(backup)
        except OSError:
            pass


def read_catalog_paths(lib_dir):
    fp = os.path.join(lib_dir, CATS_FILE)
    paths = []
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("VERSION"):
                    continue
                bits = line.split(":", 2)
                if len(bits) == 3:
                    paths.append(bits[1])
    return sorted(set(paths), key=str.lower)


def _folder_items(self, context):
    items = [(NO_FOLDER, "Unassigned (no folder)", "")]
    try:
        items += [(p, p, "") for p in read_catalog_paths(get_lib_dir())]
    except Exception:
        pass
    _folder_items_cache[:] = items      # keep references alive
    return _folder_items_cache


def selected_asset_blends(context):
    paths = []
    for a in (getattr(context, "selected_assets", None) or []):
        p = getattr(a, "full_library_path", "")
        if p:
            paths.append(p)
    if not paths:                                   # older Blender versions
        lib = get_lib_dir()
        for f in (getattr(context, "selected_asset_files", None) or []):
            rel = f.relative_path
            i = rel.lower().find(".blend")
            if i != -1:
                paths.append(os.path.join(lib, rel[:i + 6]))
    return list(dict.fromkeys(paths))


class MODELLIB_OT_move(Operator):
    bl_idname = "modellib.move_to_folder"
    bl_label = "Move to Folder"
    bl_description = "Move the selected model(s) into a folder"

    folder: EnumProperty(name="Folder", items=_folder_items)
    new_folder: StringProperty(
        name="Or new folder",
        description="Type a new folder, e.g.  Props/Industrial  (overrides the list above)",
    )

    _paths = []

    def invoke(self, context, event):
        try:                                        # save folders made with the + button first
            bpy.ops.asset.catalogs_save()
        except Exception:
            pass
        MODELLIB_OT_move._paths = selected_asset_blends(context)
        if not MODELLIB_OT_move._paths:
            self.report({'WARNING'}, "Click a model in the browser first")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        n = len(MODELLIB_OT_move._paths)
        self.layout.label(text=f"Moving {n} model{'s' if n != 1 else ''}")
        self.layout.prop(self, "folder")
        self.layout.prop(self, "new_folder")

    def execute(self, context):
        lib = get_lib_dir()
        target = self.new_folder.strip()
        if target:
            cat_id = ensure_catalog(lib, target)
            dest_dir = catalog_folder(lib, target)
        elif self.folder == NO_FOLDER:
            cat_id = NULL_UUID
            dest_dir = lib
        else:
            cat_id = ensure_catalog(lib, self.folder)
            dest_dir = catalog_folder(lib, self.folder)
        if not cat_id:
            cat_id = NULL_UUID

        moved = 0
        for blend in MODELLIB_OT_move._paths:
            if not os.path.exists(blend):
                continue
            base = os.path.splitext(os.path.basename(blend))[0]
            png = os.path.splitext(blend)[0] + ".png"

            new_blend = os.path.join(dest_dir, base + ".blend")
            if os.path.abspath(new_blend) != os.path.abspath(blend):
                new_png = os.path.join(dest_dir, base + ".png")
                n = 1
                while os.path.exists(new_blend):
                    new_blend = os.path.join(dest_dir, f"{base}.{n:03d}.blend")
                    new_png = os.path.join(dest_dir, f"{base}.{n:03d}.png")
                    n += 1
                try:
                    shutil.move(blend, new_blend)
                    if os.path.exists(png):
                        shutil.move(png, new_png)
                except Exception as e:
                    self.report({'ERROR'}, f"Could not move {base}: {e}")
                    continue
                blend = new_blend

            ok, msg = rewrite_asset_file(blend, cat_id)
            if ok:
                moved += 1
            else:
                self.report({'ERROR'}, f"Could not move {os.path.basename(blend)}: {msg}")

        refresh_asset_browsers(context)
        if moved:
            self.report({'INFO'}, f"Moved {moved} model(s)")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Open / close the Asset Browser at the bottom
# ----------------------------------------------------------------------------

def _find_area(screen, ptr):
    if ptr is None:
        return None
    for a in screen.areas:
        if a.as_pointer() == ptr:
            return a
    return None


_auto_set_areas = set()


def _select_my_models(space):
    params = getattr(space, "params", None)
    if params is None:
        return False
    ok = False
    try:
        prop = params.bl_rna.properties["asset_library_reference"]
        for it in prop.enum_items:
            if it.name == LIB_NAME:
                params.asset_library_reference = it.identifier
                ok = True
                break
    except Exception as e:
        print("Model Library: could not select library:", e)
    try:
        params.import_method = 'APPEND'
        params.instance_collections_on_append = False
    except Exception as e:
        print("Model Library: could not set import method:", e)
    return ok


def _setup_browser(ptr, tries=[0]):
    """Timer: point the new Asset Browser at our library once its space is ready."""
    for win in bpy.context.window_manager.windows:
        area = _find_area(win.screen, ptr)
        if area is None:
            continue
        space = area.spaces.active
        if getattr(space, "params", None) is None:
            tries[0] += 1
            return 0.15 if tries[0] < 20 else None
        _select_my_models(space)
        _auto_set_areas.add(ptr)
        tries[0] = 0
        return None
    tries[0] = 0
    return None


class MODELLIB_OT_toggle(Operator):
    bl_idname = "modellib.toggle_shelf"
    bl_label = "Toggle Model Library"
    bl_description = "Open or close the model library (Asset Browser) at the bottom of the screen"

    def execute(self, context):
        window = context.window
        screen = window.screen

        # --- close ---
        existing = _find_area(screen, STATE["area_ptr"])
        if existing is not None:
            region = next((r for r in existing.regions if r.type == 'WINDOW'), None)
            with context.temp_override(window=window, screen=screen, area=existing, region=region):
                bpy.ops.screen.area_close()
            STATE["area_ptr"] = None
            return {'FINISHED'}

        # --- open ---
        ensure_library()
        src = context.area if (context.area and context.area.type == 'VIEW_3D') else None
        if src is None:
            src = next((a for a in screen.areas if a.type == 'VIEW_3D'), None)
        if src is None:
            self.report({'ERROR'}, "No 3D Viewport found to split")
            return {'CANCELLED'}

        before = {a.as_pointer() for a in screen.areas}
        region = next((r for r in src.regions if r.type == 'WINDOW'), None)
        with context.temp_override(window=window, screen=screen, area=src, region=region):
            bpy.ops.screen.area_split(direction='HORIZONTAL', factor=BOTTOM_FRACTION)

        new_areas = [a for a in screen.areas if a.as_pointer() not in before]
        if not new_areas:
            self.report({'ERROR'}, "Could not split the area")
            return {'CANCELLED'}

        bottom = min([src] + new_areas, key=lambda a: a.y)
        bottom.ui_type = 'ASSETS'
        STATE["area_ptr"] = bottom.as_pointer()
        bpy.app.timers.register(lambda p=bottom.as_pointer(): _setup_browser(p), first_interval=0.15)
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# UI: buttons inside the Asset Browser header + small N-panel
# ----------------------------------------------------------------------------

def _header_draw(self, context):
    sd = context.space_data
    if getattr(sd, "browse_mode", "") == 'ASSETS':
        area = context.area
        ptr = area.as_pointer() if area else None
        if ptr is not None and ptr not in _auto_set_areas:
            if _select_my_models(sd):
                _auto_set_areas.add(ptr)

        row = self.layout.row(align=True)
        row.operator("modellib.save_selected", text="Save Selected", icon='ADD')
        row.operator("modellib.move_to_folder", text="Move to Folder", icon='FILE_FOLDER')
        row.operator("modellib.new_folder", text="New Folder", icon='NEWFOLDER')
        row.operator("modellib.detect_folders", text="", icon='FILE_REFRESH')

        row2 = self.layout.row(align=True)
        row2.label(text="Preview:")
        op = row2.operator("modellib.set_preview_shape", text="", icon='MESH_UVSPHERE')
        op.shape = 'SPHERE'
        op = row2.operator("modellib.set_preview_shape", text="", icon='MESH_CUBE')
        op.shape = 'CUBE'
        op = row2.operator("modellib.set_preview_shape", text="", icon='MESH_PLANE')
        op.shape = 'PLANE'

        self.layout.operator("modellib.toggle_shelf", text="", icon='X')


def _image_header_draw(self, context):
    if context.space_data.image:
        self.layout.operator("modellib.save_texture", text="Save Texture", icon='ADD')


class MODELLIB_PT_panel(Panel):
    bl_label = "Model Library"
    bl_idname = "MODELLIB_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Model Library"

    def draw(self, context):
        prefs = context.preferences.addons[__name__].preferences
        self.layout.operator("modellib.toggle_shelf", icon='ASSET_MANAGER', text="Open / Close Library")
        self.layout.operator("modellib.save_selected", icon='ADD')
        self.layout.label(text="Open a texture in an Image Editor")
        self.layout.label(text="to save it too (Save Texture).")
        self.layout.prop(prefs, "library_path", text="")


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------

classes = (
    MODELLIB_Preferences,
    MODELLIB_OT_save,
    MODELLIB_OT_save_texture,
    MODELLIB_OT_set_preview_shape,
    MODELLIB_OT_move,
    MODELLIB_OT_new_folder,
    MODELLIB_OT_detect_folders,
    MODELLIB_OT_toggle,
    MODELLIB_PT_panel,
)

addon_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)

    bpy.types.FILEBROWSER_HT_header.append(_header_draw)
    bpy.types.IMAGE_HT_header.append(_image_header_draw)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        # "Window" keymap so the same keys also close it while the mouse is over the browser
        km = kc.keymaps.new(name="Window", space_type='EMPTY')
        kmi = km.keymap_items.new(MODELLIB_OT_toggle.bl_idname, 'L', 'PRESS', ctrl=True, shift=True)
        addon_keymaps.append((km, kmi))

    bpy.app.timers.register(lambda: (ensure_library(), None)[1], first_interval=0.5)


def unregister():
    try:
        bpy.types.FILEBROWSER_HT_header.remove(_header_draw)
    except Exception:
        pass
    try:
        bpy.types.IMAGE_HT_header.remove(_image_header_draw)
    except Exception:
        pass
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
