"""

RenderPipeline

Copyright (c) 2014-2016 tobspr <tobias.springer1@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

"""

from panda3d.core import LVecBase2i, TransformState, RenderState
from panda3d.core import PandaSystem, MaterialAttrib, WindowProperties
from panda3d.core import GeomTristrips, Vec4

from rpcore.globals import Globals
from rpcore.effect import Effect
from rpcore.common_resources import CommonResources

from rpcore.render_target import RenderTarget
from rpcore.pluginbase.manager import PluginManager
from rpcore.pluginbase.day_manager import DayTimeManager

from panda3d._rplight import TagStateManager

from rpcore.util.task_scheduler import TaskScheduler

from rpcore.util.ies_profile_loader import IESProfileLoader

from rpcore.mount_manager import MountManager
from rpcore.stage_manager import StageManager
from rpcore.light_manager import LightManager

from rpcore.stages.ambient_stage import AmbientStage
from rpcore.stages.gbuffer_stage import GBufferStage
from rpcore.stages.final_stage import FinalStage
from rpcore.stages.downscale_z_stage import DownscaleZStage
from rpcore.stages.combine_velocity_stage import CombineVelocityStage
from rpcore.stages.upscale_stage import UpscaleStage

from direct.showbase.ShowBase import ShowBase

class RenderPipeline():

    """ This is the main render pipeline class, it combines all components of
    the pipeline to form a working system. It does not do much work itself, but
    instead setups all the managers and systems to be able to do their work. """

    def __init__(self):
        """ Creates a new pipeline with a given showbase instance. This should
        be done before intializing the ShowBase, the pipeline will take care of
        that. If the showbase has been initialized before, have a look at
        the alternative initialization of the render pipeline (the first sample)."""

        mount_mgr = MountManager()
        mount_mgr.mount()

        self.reference_mode = False
        self.resolution_scale = 1.0
        self.use_r11_g11_b10 = False

        # Settings lights
        self.culling_grid_size_x = 24
        self.culling_grid_size_y = 16
        self.culling_grid_slices = 32
        self.culling_max_distance = 500.0
        self.culling_slice_width = 2048
        self.max_lights_per_cell = 64
        
        # Settings shadow
        self.atlas_size = 4096
        self.max_update_distance = 150.0
        self.max_updates = 8

        self._applied_effects = []
        
        # The folder names of the plugins, in a string format separated by a space.
        self.plugins_used = None

    def reload_shaders(self):
        """ Reloads all shaders. This will reload the shaders of all plugins,
        as well as the pipelines internally used shaders. Because of the
        complexity of some shaders, this operation take might take several
        seconds. Also notice that all applied effects will be lost, and instead
        the default effect will be set on all elements again. Due to this fact,
        this method is primarly useful for fast iterations when developing new
        shaders. """

        self.tag_mgr.cleanup_states()
        self.stage_mgr.reload_shaders()
        self.light_mgr.reload_shaders()
        #self._set_default_effect()
        self.plugin_mgr.trigger_hook("shader_reload")
        
        """ Re-applies all custom shaders the user applied, to avoid them getting
        removed when the shaders are reloaded """
        
        print("Re-applying", len(self._applied_effects), "custom shaders")
        
        for args in self._applied_effects:
            self._internal_set_effect(*args)


    def create(self, base):
        """ This creates the pipeline, and setups all buffers. It also
        constructs the showbase. The settings should have been loaded before
        calling this, and also the base and write path should have been
        initialized properly (see MountManager).

        If base is None, the showbase used in the RenderPipeline constructor
        will be used and initialized. Otherwise it is assumed that base is an
        initialized ShowBase object. In this case, you should call
        pre_showbase_init() before initializing the ShowBase"""

        self.base = base

        """ Inits all global bindings. This includes references to the global
        ShowBase instance, as well as the render resolution, the GUI font,
        and various global logging and output methods. """
        Globals.load(self.base)

        native_w, native_h = self.base.win.get_x_size(), self.base.win.get_y_size()
        Globals.native_resolution = LVecBase2i(native_w, native_h)
        
        self._last_window_dims = LVecBase2i(Globals.native_resolution)
        self._compute_render_resolution()
        RenderTarget.USE_R11G11B10 = self.use_r11_g11_b10

        """ Internal method to create all managers and instances. This also
        initializes the commonly used render stages, which are always required,
        independently of which plugins are enabled. """
        self.task_scheduler = TaskScheduler(self)
        self.tag_mgr = TagStateManager(Globals.base.cam)
        
        self.stage_mgr = StageManager(self)
        
        self.light_mgr = LightManager(self)

        self.daytime_mgr = DayTimeManager(self)
        
        self.ies_loader = IESProfileLoader(self)

        """ Inits the commonly used stages, which don't belong to any plugin,
        but yet are necessary and widely used. """
        self.stage_mgr.add_stage(AmbientStage(self))
        self.stage_mgr.add_stage(GBufferStage(self))
        self.stage_mgr.add_stage(FinalStage(self))
        self.stage_mgr.add_stage(DownscaleZStage(self))
        self.stage_mgr.add_stage(CombineVelocityStage(self))

        self.plugin_mgr = PluginManager(self)
        self.plugin_mgr.load(self.plugins_used)

        self.plugin_mgr.trigger_hook("stage_setup")
        self.plugin_mgr.trigger_hook("post_stage_setup")

        self.daytime_mgr.load_settings()

        self.common_resources = CommonResources(self)
        self.common_resources.write_config()

        """ Creates commonly used defines for the shader configuration. """
        self.stage_mgr.defines["CAMERA_NEAR"] = round(self.base.camLens.get_near(), 10)
        self.stage_mgr.defines["CAMERA_FAR"] = round(self.base.camLens.get_far(), 10)


        # Work arround buggy nvidia driver, which expects arrays to be const
        if "NVIDIA 361.43" in self.base.win.gsg.get_driver_version():
            self.stage_mgr.defines["CONST_ARRAY"] = "const"
        else:
            self.stage_mgr.defines["CONST_ARRAY"] = ""

        self.stage_mgr.defines["REFERENCE_MODE"] = self.reference_mode
        
        self.light_mgr.init_defines()
        self.plugin_mgr.init_defines()

        """ Internal method to initialize all managers, after they have been
        created earlier in _create_managers. The creation and initialization
        is seperated due to the fact that plugins and various other subprocesses
        have to get initialized inbetween. """
        self.stage_mgr.setup()
        self.stage_mgr.reload_shaders()
        
        self.light_mgr.reload_shaders()
        self.light_mgr.init_shadows()
        
        """ Internal method to init the tasks and keybindings. This constructs
        the tasks to be run on a per-frame basis. """
        self.base.addTask(self._manager_update_task, "RP_UpdateManagers", sort=10)
        self.base.addTask(self._plugin_pre_render_update, "RP_Plugin_BeforeRender", sort=12)
        self.base.addTask(self._plugin_post_render_update, "RP_Plugin_AfterRender", sort=15)
        self.base.addTask(self._update_inputs_and_stages, "RP_UpdateInputsAndStages", sort=18)
        self.base.taskMgr.doMethodLater(0.5, self._clear_state_cache, "RP_ClearStateCache")
        self.base.accept("window-event", self._handle_window_event)

        self.plugin_mgr.trigger_hook("pipeline_created")

        """ Sets the default effect used for all objects if not overridden, this
        just calls set_effect with the default effect and options as parameters.
        This uses a very low sort, to make sure that overriding the default
        effect does not require a custom sort parameter to be passed. """
        self.set_effect(Globals.render, "effects/default.yaml", {}, -10)

    def add_light(self, light):
        """ Adds a new light to the rendered lights, check out the LightManager
        add_light documentation for further information. """
        self.light_mgr.add_light(light)

    def remove_light(self, light):
        """ Removes a previously attached light, check out the LightManager
        remove_light documentation for further information. """
        self.light_mgr.remove_light(light)

    def load_ies_profile(self, filename):
        """ Loads an IES profile from a given filename and returns a handle which
        can be used to set an ies profile on a light """
        return self.ies_loader.load(filename)

    def _internal_set_effect(self, nodepath, effect_src, options=None, sort=30):
        """ Sets an effect to the given object, using the specified options.
        Check out the effect documentation for more information about possible
        options and configurations. The object should be a nodepath, and the
        effect will be applied to that nodepath and all nodepaths below whose
        current effect sort is less than the new effect sort (passed by the
        sort parameter). """
        effect = Effect.load(effect_src, options)
        if effect is None:
            return self.error("Could not apply effect")

        for i, stage in enumerate(("gbuffer", "shadow", "voxelize", "envmap", "forward")):
            if not effect.get_option("render_" + stage):
                nodepath.hide(self.tag_mgr.get_mask(stage))
            else:
                shader = effect.get_shader_obj(stage)
                if stage == "gbuffer":
                    nodepath.set_shader(shader, 25)
                else:
                    self.tag_mgr.apply_state(
                        stage, nodepath, shader, str(effect.effect_id), 25 + 10 * i + sort)
                nodepath.show_through(self.tag_mgr.get_mask(stage))

        if effect.get_option("render_gbuffer") and effect.get_option("render_forward"):
            self.error("You cannot render an object forward and deferred at the "
                       "same time! Either use render_gbuffer or use render_forward, "
                       "but not both.")

    def set_effect(self, nodepath, effect_src, options = None, sort=30):
        """ See _internal_set_effect. """
        args = (nodepath, effect_src, options, sort)
        self._applied_effects.append(args)
        self._internal_set_effect(*args)

    def add_environment_probe(self):
        """ Constructs a new environment probe and returns the handle, so that
        the probe can be modified. In case the env_probes plugin is not activated,
        this returns a dummy object which can be modified but has no impact. """
        if not self.plugin_mgr.is_plugin_enabled("env_probes"):
            self.warn("env_probes plugin is not loaded - cannot add environment probe")

            class DummyEnvironmentProbe(object):  # pylint: disable=too-few-public-methods
                def __getattr__(self, *args, **kwargs):
                    return lambda *args, **kwargs: None
            return DummyEnvironmentProbe()

        # Ugh ..
        from rpplugins.env_probes.environment_probe import EnvironmentProbe
        probe = EnvironmentProbe()
        self.plugin_mgr.instances["env_probes"].probe_mgr.add_probe(probe)
        return probe

    def _compute_render_resolution(self):
        """ Computes the internally used render resolution. This might differ
        from the window dimensions in case a resolution scale is set. """
        scale_factor = self.resolution_scale
        w = int(float(Globals.native_resolution.x) * scale_factor)
        h = int(float(Globals.native_resolution.y) * scale_factor)
        # Make sure the resolution is a multiple of 4
        w, h = w - w % 4, h - h % 4
        Globals.resolution = LVecBase2i(w, h)

    def _handle_window_event(self, event):
        """ Checks for window events. This mainly handles incoming resizes,
        and calls the required handlers """
        self.base.windowEvent(event)
        
        window_dims = LVecBase2i(self.base.win.get_x_size(), self.base.win.get_y_size())
        
        if window_dims != self._last_window_dims and window_dims != Globals.native_resolution:
            self._last_window_dims = LVecBase2i(window_dims)

            # Ensure the dimensions are a multiple of 4, and if not, correct it
            if window_dims.x % 4 != 0 or window_dims.y % 4 != 0:
                print("Correcting non-multiple of 4 window size:", window_dims)
                window_dims.x = window_dims.x - window_dims.x % 4
                window_dims.y = window_dims.y - window_dims.y % 4
                props = WindowProperties.size(window_dims.x, window_dims.y)
                self.base.win.request_properties(props)

            Globals.native_resolution = window_dims
            self._compute_render_resolution()
            self.light_mgr.compute_tile_size()
            self.stage_mgr.handle_window_resize()
            self.plugin_mgr.trigger_hook("window_resized")

    def _clear_state_cache(self, task = None):
        """ Task which repeatedly clears the state cache to avoid storing
        unused states. While running once a while, this task prevents over-polluting
        the state-cache with unused states. This complements Panda3D's internal
        state garbarge collector, which does a great job, but still cannot clear
        up all states. """
        task.delayTime = 2.0
        TransformState.clear_cache()
        RenderState.clear_cache()
        return task.again

    def _manager_update_task(self, task):
        """ Update task which gets called before the rendering, and updates
        all managers."""
        self.task_scheduler.step()
        self.daytime_mgr.update()
        self.light_mgr.update()

        return task.cont

    def _update_inputs_and_stages(self, task):
        """ Updates the commonly used inputs each frame. This is a seperate
        task to be able view detailed performance information in pstats, since
        a lot of matrix calculations are involved here. """
        self.common_resources.update()
        self.stage_mgr.update()
        return task.cont

    def _plugin_pre_render_update(self, task):
        """ Update task which gets called before the rendering, and updates the
        plugins. This is a seperate task to split the work, and be able to do
        better performance analysis in pstats later on. """
        self.plugin_mgr.trigger_hook("pre_render_update")
        return task.cont

    def _plugin_post_render_update(self, task):
        """ Update task which gets called after the rendering, and should cleanup
        all unused states and objects. This also triggers the plugin post-render
        update hook. """
        self.plugin_mgr.trigger_hook("post_render_update")
        return task.cont