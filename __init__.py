# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import os
import sys
from typing import Any, Callable
import bpy
import datetime
import math
import requests
from subprocess import Popen

from bpy.props import *
from bpy.types import Context, Modifier, Object, Operator, PropertyGroup, Scene, UILayout

from . import require, mail

bl_info = {
    "name": "Butler",
    "author": "doodlezucc",
    "description": "",
    "blender": (2, 80, 0),
    "version": (0, 0, 1),
    "location": "",
    "warning": "",
    "category": "Generic"
}
classes = list()

daemon = None
initialized_bake_objects = False

def registered(cls):
    classes.append(cls)
    return cls


class ButlerActionType:
    OBJECT_OPERATOR = "OBJECT_OPERATOR"
    PYTHON_OPERATOR = "PYTHON_OPERATOR"
    RENDER = "RENDER"
    BAKE = "BAKE"


class ButlerRenderRange:
    FINAL = "FINAL"
    PREVIEW = "PREVIEW"
    CUSTOM = "CUSTOM"


@registered
class Bakeable(PropertyGroup):
    bl_idname = "butler.bakeable"
    bl_label = "Bakeable"

def await_depsgraph_update(done: Callable[[], Any]):
    event = bpy.app.handlers.depsgraph_update_post
    def handler(a, b):
        print("called after depsgraph :)")
        event.remove(handler)
        done()

    event.append(handler)

def await_interval(check: Callable[[], bool], done: Callable[[], Any], interval=1.0):
    def single_check():
        if not check():
            print("bump")
            return interval

        done()
        return None

    bpy.app.timers.register(single_check)

def await_file_write(filepath, done: Callable[[], Any], interval=1.0):
    cached_mtime = datetime.datetime.now().timestamp()

    def check():
        try:
            return os.stat(filepath).st_mtime >= cached_mtime
        except:
            return False
    
    return await_interval(check, done, interval)

cache_mods = [
    "CLOTH",
    "SOFT_BODY",
]

bake_objects = set()

def on_target_update(butler_action, ctx: Context):
    update_bake_objects(ctx.scene)
    return None

def on_modifier_update(butler_action, ctx: Context):
    mod = butler_action.mod_ref(ctx)
    if mod is not None and mod.type == "DYNAMIC_PAINT" and mod.canvas_settings:
        surfaces = mod.canvas_settings.canvas_surfaces

        if len(surfaces):
            butler_action.bake_paint_surface = surfaces[0].name
    return None

def update_bake_objects(scene: Scene):
    global bake_objects
    bake_objects = set()

    butler = scene.butler
    for flow in butler.flows:
        for action in flow.actions:
            if action.action_type == ButlerActionType.BAKE and action.target != "":
                bake_objects.add(action.target)

def is_bakeable(mod: Modifier):
    if mod.type == "FLUID":
        return mod.fluid_type == "DOMAIN"
    elif mod.type == "DYNAMIC_PAINT":
        return mod.ui_type == "CANVAS" and mod.canvas_settings
    
    return mod.type in cache_mods

def update_bakeables(obj: Object):
    obj.bakeable.clear()

    for mod in obj.modifiers:
        if is_bakeable(mod):
            bak = obj.bakeable.add()
            bak.name = mod.name


def mod_icon(modtype):
    if modtype == "CLOTH":
        return "MOD_CLOTH"
    elif modtype == "FLUID":
        return "MOD_FLUIDSIM"
    elif modtype == "DYNAMIC_PAINT":
        return "MOD_DYNAMICPAINT"

    return modtype


@registered
class ButlerAction(PropertyGroup):
    bl_idname = "butler.action"

    enabled: BoolProperty(name="Enable", default=True)

    action_type: EnumProperty(name="Action Type", items=[
        (ButlerActionType.OBJECT_OPERATOR, "Object Operator", "Call an operator on a specific object", "SEQUENCE_COLOR_02", 0),
        (ButlerActionType.PYTHON_OPERATOR, "Python Operator", "Execute a line of Python code", "SEQUENCE_COLOR_04", 1),
        (ButlerActionType.RENDER, "Render", "Render the scene", "SEQUENCE_COLOR_06", 2),
        (ButlerActionType.BAKE, "Bake Physics", "Bake a physics modifier", "SEQUENCE_COLOR_05", 3),
    ])

    target: StringProperty(name="Object", update=on_target_update)
    operator: StringProperty(name="Operator")

    single_operator: StringProperty(name="Operator")
    py_operator: StringProperty(name="Operator")

    frame_start: IntProperty(name="Frame Start", default=0)
    frame_end: IntProperty(name="Frame End", default=250)

    def beautify_render_ranges(self, ctx: Context):
        s = ctx.scene
        return [
            (ButlerRenderRange.FINAL, f"Final ({s.frame_start} - {s.frame_end})", "Use default frame range"),
            (ButlerRenderRange.PREVIEW, f"Preview ({s.frame_preview_start} - {s.frame_preview_end})", "Use preview frame range"),
            (ButlerRenderRange.CUSTOM, "Custom", "Use a custom frame range"),
        ]
    render_range: EnumProperty(name="Frame Range", items=beautify_render_ranges)

    bake_modifier: StringProperty(name="Modifier", update=on_modifier_update)
    bake_paint_surface: StringProperty(name="Surface")
    rebake: BoolProperty(name="Rebake", description="Bake this modifier even if it's already cached")
    bake_fluid_mesh: BoolProperty(name="Bake Fluid Mesh", description="Bake fluid mesh", default=True)

    def draw(self, layout: UILayout, ctx: Context):
        flow = settings(ctx).get_active_flow()
        action_index = flow.actions.values().index(self)

        box = layout.box()
        row = box.row()
        col = row.column()
        col.alignment = "RIGHT"
        col.enabled = self.enabled

        top = col.row()
        top.prop(self, "action_type", text="")
        top.operator(ButlerRemoveAction.bl_idname, text="", icon="X").index = action_index

        if self.action_type == ButlerActionType.OBJECT_OPERATOR:
            col.prop_search(self, "target", ctx.scene, "objects")
            col.prop(self, "operator", icon_only=True, icon="RNA")
        elif self.action_type == ButlerActionType.PYTHON_OPERATOR:
            col.prop(self, "single_operator",
                     icon_only=True, icon="SCRIPTPLUGINS")
        elif self.action_type == ButlerActionType.RENDER:
            col.prop(self, "render_range")

            if self.render_range == ButlerRenderRange.CUSTOM:
                col = col.column(align=True)
                col.prop(self, "frame_start")
                col.prop(self, "frame_end")
        elif self.action_type == ButlerActionType.BAKE:
            targets = col.column(align=True)
            targets.prop_search(self, "target", ctx.scene, "objects", text="")
            obj = self.obj_ref(ctx)
            mod = self.mod_ref(ctx)

            if obj is not None:
                icon = "PHYSICS"

                if mod is not None:
                    icon = mod_icon(mod.type)

                targets.prop_search(self, "bake_modifier", obj, "bakeable", icon=icon, text="")

                if mod is not None and mod.canvas_settings:
                    targets.prop_search(self, "bake_paint_surface", mod.canvas_settings, "canvas_surfaces", icon="TPAINT_HLT", text="")

            checks = col.row()
            checks.prop(self, "rebake")

            if self.can_bake_fluid_mesh(ctx):
                checks.prop(self, "bake_fluid_mesh")

        r = row.column(align=True)

        def move_button(icon, mod):
            wrapper = r.column(align=True)
            move = wrapper.operator(
                ButlerMoveAction.bl_idname, icon=icon, text="")
            move.index = action_index

            end = action_index + mod
            move.end = end
            wrapper.enabled = end >= 0 and end < len(flow.actions)

        move_button("TRIA_UP", -1)
        r.prop(self, "enabled", text="", icon="HIDE_" +
               ("OFF" if self.enabled else "ON"))
        move_button("TRIA_DOWN", 1)
    
    def obj_ref(self, ctx: Context):
        return ctx.scene.objects.get(self.target, None)
    
    def mod_ref(self, ctx: Context):
        obj = self.obj_ref(ctx)
        if obj is not None:
                return obj.modifiers.get(self.bake_modifier, None)
        return None
    
    # well this sure is specific and only works for one setting
    def can_bake_fluid_mesh(self, ctx: Context):
        mod = self.mod_ref(ctx)
        if mod is not None and mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            dom = mod.domain_settings
            return dom.domain_type == "LIQUID" and dom.use_mesh and dom.cache_type == "MODULAR" and dom.cache_resumable
        return False
    
    def can_bake_paint(self, ctx: Context):
        mod = self.mod_ref(ctx)
        if mod is not None and mod.type == "DYNAMIC_PAINT" and mod.canvas_settings:
            return True
        return False

    def run(self, ctx: Context, callback):
        if not self.enabled:
            return callback()
        print("Running " + self.action_type)

        if self.action_type == ButlerActionType.OBJECT_OPERATOR:
            self.run_object_operator(ctx)
            callback()
        elif self.action_type == ButlerActionType.PYTHON_OPERATOR:
            self.run_python_operator()
            callback()
        elif self.action_type == ButlerActionType.RENDER:
            self.run_render(ctx, callback)
        elif self.action_type == ButlerActionType.BAKE:
            self.run_bake(ctx, callback)

    def run_object_operator(self, ctx: Context):
        if self.target == None:
            return

        exec("obj." + self.operator, globals(),
             {"obj": self.obj_ref(ctx)})

    def run_python_operator(self):
        exec(self.single_operator, globals(), locals())

    def get_frame_range(self, end: bool, s: Scene):
        if self.render_range == ButlerRenderRange.FINAL:
            return s.frame_end if end else s.frame_start
        elif self.render_range == ButlerRenderRange.PREVIEW:
            return s.frame_preview_end if end else s.frame_preview_start
        elif self.render_range == ButlerRenderRange.CUSTOM:
            return self.frame_end if end else self.frame_start

    def run_render(self, c: Context, callback):
        ctx = c.copy()
        scene = ctx["scene"]
        a_start = scene.frame_start
        a_end = scene.frame_end

        scene.frame_start = self.get_frame_range(False, scene)
        scene.frame_end = self.get_frame_range(True, scene)

        bpy.ops.render.render("INVOKE_DEFAULT", animation=True, use_viewport=True)

        filepath = scene.render.frame_path(frame=scene.frame_end)

        def find_render_window():
            for win in ctx["window_manager"].windows:
                if win.screen.name == "temp":
                    return win
            return None

        def post_render():
            print("yay")
            rwin = find_render_window()
            if rwin is not None:
                ctx["window"] = rwin
                ctx["area"] = rwin.screen.areas[0]
                bpy.ops.render.view_cancel(ctx)
            bpy.ops.render.play_rendered_anim()

            scene.frame_start = a_start
            scene.frame_end = a_end
            callback()

        await_file_write(filepath, post_render)

    def run_bake(self, ctx: Context, callback):
        try:
            obj = ctx.scene.objects[self.target]
            mod = obj.modifiers[self.bake_modifier]
        except:
            return callback()
        
        override = bpy.context.copy()
        override['active_object'] = obj
        override['object'] = obj
        
        if mod.type == "FLUID":
            do_mesh = self.bake_fluid_mesh and self.can_bake_fluid_mesh(ctx)
            dom = mod.domain_settings

            def on_data_baked():
                print("data baked")
                if do_mesh:
                    print("baking mesh")
                    bpy.ops.fluid.bake_mesh(override, "INVOKE_DEFAULT")
                    await_interval(lambda: dom.cache_frame_pause_mesh >= dom.cache_frame_end, callback)
                else:
                    callback()

            def on_data_freed():
                print("now free")
                bpy.ops.fluid.bake_data(override, "INVOKE_DEFAULT")
                await_interval(lambda: dom.cache_frame_pause_data >= dom.cache_frame_end, on_data_baked)

            if self.rebake:
                print("freeing")
                bpy.ops.fluid.free_all(override, "INVOKE_DEFAULT")
                return await_interval(lambda: dom.cache_frame_pause_data <= dom.cache_frame_start, on_data_freed, interval=0.2)
            else:
                if dom.cache_frame_pause_data >= dom.cache_frame_end:
                    return on_data_baked()
                return on_data_freed()

        if mod.type == "DYNAMIC_PAINT":
            surface = mod.canvas_settings.canvas_surfaces[self.bake_paint_surface]
            if surface.surface_format == "IMAGE":
                names = list()
                if surface.use_output_a: names.append(surface.output_name_a)
                if surface.use_output_b: names.append(surface.output_name_b)

                if not len(names):
                    print("Skipped baking because there no outputs would have been generated.")
                    return callback()

                format = surface.image_fileformat
                lastframe = names[-1] + str(surface.frame_end).rjust(4, "0")
                lastframe = bpy.path.ensure_ext(lastframe, ".png" if format == "PNG" else ".exr")
                
                filepath = os.path.join(bpy.path.abspath(surface.image_output_path), lastframe)
                print("Waiting for ", filepath)

                bpy.ops.dpaint.bake(override, "INVOKE_DEFAULT")
                return await_file_write(filepath, callback)
            else:
                cache = surface.point_cache
            
        else:
            cache = mod.point_cache

        if self.rebake or not cache.is_baked:
            override['point_cache'] = cache
            bpy.ops.ptcache.free_bake(override)
            bpy.ops.ptcache.bake(override, "INVOKE_DEFAULT", bake=True)

            await_interval(lambda: cache.is_baked, callback)
        else:
            callback()

BUTLER_URL = "http://localhost:2048/update/blender-butler"

def update_butler_task(title=None, description=None, progress=None):
    if daemon:
        params = {}
        if title is not None: params["title"] = title
        if description is not None: params["description"] = description
        if progress is not None: params["progress"] = progress

        return requests.get(BUTLER_URL, params=params)
    print("Daemon disabled, task update not sent")
    return None

@registered
class ButlerFlow(PropertyGroup):
    bl_idname = "butler.flow"
    name: StringProperty(default="Flow")
    actions: CollectionProperty(type=ButlerAction)

    def draw(self, layout: UILayout, ctx: Context):
        if not self.actions:
            row = layout.row()
            row.alignment = "CENTER"
            row.label(text="No actions added.")

        for action in self.actions:
            action.draw(layout, ctx)

        layout.operator(ButlerAddAction.bl_idname)

    def run(self, ctx: Context):
        if len(self.actions) == 0:
            return
        
        update_butler_task(title=f"Blender: {self.name}", progress=0)

        start_time = datetime.datetime.now().timestamp()

        def callback():
            end_time = datetime.datetime.now().timestamp()
            seconds = end_time - start_time

            seconds_for_mail = 5 * 60

            if True or seconds > seconds_for_mail:
                min = math.floor(seconds / 60)
                sec = math.floor(seconds % 60)
                time = f"{sec} seconds"

                if min > 0:
                    time = f"{min} minutes, {time}"

                content = f"All actions of your selected Butler flow have finished in {time}!"
                update_butler_task(description=content, progress=1)
                # mail.send_email("Tasks done!", content)

        self.run_recursive(ctx, 0, callback)
    
    def post_update(self, index):
        count = len(self.actions)
        update_butler_task(description=f"Task {index + 1}/{count}", progress=index/count)

    def run_recursive(self, ctx, index, done: Callable[[], Any]):
        if index >= len(self.actions):
            print("Done!")
            return done()
        
        self.post_update(index)

        def callback():
            self.run_recursive(ctx, index + 1, done)

        self.actions[index].run(ctx, callback)


@registered
class ButlerSettings(PropertyGroup):
    bl_idname = "butler.settings"
    flows: CollectionProperty(type=ButlerFlow)
    active_flow: IntProperty("Selected Flow", default=0, min=0)

    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self.flows.clear()
        self.flows.add()
        self.active_flow = 0

    def get_active_flow(self) -> ButlerFlow:
        return self.flows[self.active_flow]

@registered
class BUTLER_UL_flows(bpy.types.UIList):
    def draw_item(self, ctx: Context, layout, data, item: ButlerFlow, icon, active_data, active_propname):
        # draw_item must handle the three layout types... Usually 'DEFAULT' and 'COMPACT' can share the same code.
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "name", text="", emboss=False, icon_value=icon)
        
        # 'GRID' layout type should be as compact as possible (typically a single icon!).
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)


@registered
class ButlerPanel(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_butler"
    bl_label = "Butler"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Butler"

    def draw(self, ctx: Context):
        layout = self.layout
        butler = settings(ctx)

        row = layout.row()
        row.template_list("BUTLER_UL_flows", "", butler, "flows", butler, "active_flow", rows=2)
        col = row.column(align=True)
        col.operator(ButlerAddFlow.bl_idname, icon="ADD", text="")
        col.operator(ButlerRemoveFlow.bl_idname, icon="REMOVE", text="").index = butler.active_flow

@registered
class ButlerFlow(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_butler_flow"
    bl_label = "Flow"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Butler"

    @classmethod
    def poll(cls, ctx: Context):
        return settings(ctx).flows

    def draw(self, ctx: Context):
        layout = self.layout
        butler = settings(ctx)
        
        butler.get_active_flow().draw(layout, ctx)
        layout.operator(RunButler.bl_idname)

@registered
class RunButler(bpy.types.Operator):
    bl_idname = "butler.run"
    bl_label = "Run Butler Flow"
    bl_options = {'REGISTER'}

    def execute(self, ctx: bpy.types.Context):
        butler = settings(ctx)
        butler.get_active_flow().run(ctx)
        return {'FINISHED'}


@registered
class ResetButler(bpy.types.Operator):
    bl_idname = "butler.reset"
    bl_label = "Reset Butler Flow"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, ctx: bpy.types.Context):
        settings(ctx).reset()
        return {'FINISHED'}

@registered
class ButlerAddFlow(bpy.types.Operator):
    bl_idname = "butler.add_flow"
    bl_label = "Add Flow"
    bl_options = {'UNDO'}

    def execute(self, ctx: Context):
        butler = settings(ctx)
        butler.flows.add()
        butler.active_flow = len(butler.flows) - 1

        return {'FINISHED'}
@registered
class ButlerRemoveFlow(bpy.types.Operator):
    bl_idname = "butler.remove_flow"
    bl_label = "Remove Flow"
    bl_options = {'UNDO'}

    index: IntProperty()

    def execute(self, ctx: Context):
        butler = settings(ctx)
        if len(butler.flows) > 1:
            butler.active_flow -= 1
        butler.flows.remove(self.index)
        return {'FINISHED'}

@registered
class ButlerAddAction(bpy.types.Operator):
    bl_idname = "butler.add_action"
    bl_label = "Add Action"
    bl_options = {'UNDO'}

    def execute(self, ctx: Context):
        settings(ctx).get_active_flow().actions.add()
        return {'FINISHED'}
@registered
class ButlerRemoveAction(bpy.types.Operator):
    bl_idname = "butler.remove_action"
    bl_label = "Remove Action"
    bl_options = {'UNDO'}

    index: IntProperty()

    def execute(self, ctx: Context):
        settings(ctx).get_active_flow().actions.remove(self.index)
        return {'FINISHED'}


@registered
class ButlerMoveAction(Operator):
    bl_idname = "butler.move_action"
    bl_label = "Move Action"

    index: IntProperty()
    end: IntProperty()

    def execute(self, ctx: Context):
        l = settings(ctx).get_active_flow().actions

        if self.end < 0 or self.end >= len(l):
            return {"CANCELLED"}

        l.move(self.index, self.end)

        return {"FINISHED"}


def settings(context: Context) -> ButlerSettings:
    return context.scene.butler


def on_depsgraph_update(scene: Scene):
    global initialized_bake_objects
    if not initialized_bake_objects:
        update_bake_objects(scene)
        initialized_bake_objects = True
    
    for id in bake_objects:
        update_bakeables(scene.objects[id])

def start_server():
    global daemon
    print("Starting Butler server")
    require.require(["aiohttp"])
    dir = os.path.dirname(__file__)
    path = os.path.join(dir, "server/server.py")
    daemon = Popen([sys.executable, path], stdout=sys.stdout, stderr=sys.stderr)

def kill_server():
    global daemon
    if daemon is not None:
        print("Killing Butler server")
        daemon.terminate()


# store keymaps here to access after registration
addon_keymaps = []


def register():
    start_server()

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.butler = PointerProperty(type=ButlerSettings)
    bpy.types.Object.bakeable = CollectionProperty(type=Bakeable)
    bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)

    # handle the keymap
    wm = bpy.context.window_manager
    # Note that in background mode (no GUI available), keyconfigs are not available either,
    # so we have to check this to avoid nasty errors in background case.
    kc = wm.keyconfigs.addon
    if kc:
        km = wm.keyconfigs.addon.keymaps.new(
            name='3D View Generic', space_type='VIEW_3D')
        addon_keymaps.append((km, km.keymap_items.new(
            ResetButler.bl_idname, 'B', 'PRESS', ctrl=True, shift=True)))


def unregister():
    kill_server()
    
    # Remove depsgraph update handler (without throwing an error)
    my_handler = None
    for handler in bpy.app.handlers.depsgraph_update_post:
        if handler.__name__ == on_depsgraph_update.__name__:
            my_handler = handler
            break
    
    if my_handler is not None:
        bpy.app.handlers.depsgraph_update_post.remove(my_handler)

    # Note: when unregistering, it's usually good practice to do it in reverse order you registered.
    # Can avoid strange issues like keymap still referring to operators already unregistered...
    # handle the keymap
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    for cls in classes:
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
