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

from panda3d.core import CS_yup_right, CS_zup_right, invert, Vec3, Mat4, Vec4
from panda3d.core import SamplerState
from direct.stdpy.file import open

from rpcore.globals import Globals
from rpcore.loader import RPLoader

from rpcore.util.shader_input_blocks import GroupedInputBlock


class CommonResources():

    def __init__(self, pipeline):
        self._pipeline = pipeline
        self._showbase = Globals.base
        self._ptas = {}

        self._load_textures()
        self._setup_inputs()

    def _setup_inputs(self):
        self._input_ubo = GroupedInputBlock("MainSceneData")
        inputs = (
            ("camera_pos", "vec3"),
            ("view_proj_mat_no_jitter", "mat4"),
            ("last_view_proj_mat_no_jitter", "mat4"),
            ("last_inv_view_proj_mat_no_jitter", "mat4"),
            ("view_mat_z_up", "mat4"),
            ("proj_mat", "mat4"),
            ("inv_proj_mat", "mat4"),
            ("view_mat_billboard", "mat4"),
            ("frame_delta", "float"),
            ("smooth_frame_delta", "float"),
            ("frame_time", "float"),
            ("current_film_offset", "vec2"),
            ("frame_index", "int"),
            ("screen_size", "ivec2"),
            ("native_screen_size", "ivec2"),
            ("lc_tile_count", "ivec2"),
            ("ws_frustum_directions", "mat4"),
            ("vs_frustum_directions", "mat4"),
        )
        for name, ipt_type in inputs:
            self._input_ubo.register_pta(name, ipt_type)

        self._pipeline.stage_mgr.input_blocks.append(self._input_ubo)

        # Main camera and main render have to be regular inputs, since they are
        # used in the shaders by that name.
        self._pipeline.stage_mgr.inputs["mainCam"] = self._showbase.cam
        self._pipeline.stage_mgr.inputs["mainRender"] = self._showbase.render

        # Set the correct frame rate interval
        Globals.clock.set_average_frame_rate_interval(3.0)

        # Set initial value for view_proj_mat_no_jitter
        view_mat = Globals.render.get_transform(self._showbase.cam).get_mat()
        proj_mat = Mat4(self._showbase.camLens.get_projection_mat())
        proj_mat.set_cell(1, 0, 0.0)
        proj_mat.set_cell(1, 1, 0.0)
        self._input_ubo.update_input("view_proj_mat_no_jitter", view_mat * proj_mat)

    def write_config(self):
        content = self._input_ubo.generate_shader_code()
        with open("/$$rptemp/$$main_scene_data.inc.glsl", "w") as handle:
            handle.write(content)
 
    def _load_textures(self):
        self._load_environment_cubemap()
        self._load_prefilter_brdf()
        self._load_skydome()

    def _load_environment_cubemap(self):
        envmap = RPLoader.load_cube_map("/$$rp/data/default_cubemap/cubemap.txo", read_mipmaps=True)
        envmap.set_minfilter(SamplerState.FT_linear_mipmap_linear)
        envmap.set_magfilter(SamplerState.FT_linear)
        envmap.set_wrap_u(SamplerState.WM_repeat)
        envmap.set_wrap_v(SamplerState.WM_repeat)
        envmap.set_wrap_w(SamplerState.WM_repeat)
        self._pipeline.stage_mgr.inputs["DefaultEnvmap"] = envmap

    def _load_prefilter_brdf(self):
        luts = [
            {"src": "slices/env_brdf_#.png", "input": "PrefilteredBRDF"},
            {"src": "slices_metal/env_brdf.png", "input": "PrefilteredMetalBRDF"},
            {"src": "slices_coat/env_brdf.png", "input": "PrefilteredCoatBRDF"},
        ]

        for config in luts:
            loader_method = RPLoader.load_texture
            if "#" in config["src"]:
                loader_method = RPLoader.load_3d_texture

            brdf_tex = loader_method("/$$rp/data/environment_brdf/{}".format(config["src"]))
            brdf_tex.set_minfilter(SamplerState.FT_linear)
            brdf_tex.set_magfilter(SamplerState.FT_linear)
            brdf_tex.set_wrap_u(SamplerState.WM_clamp)
            brdf_tex.set_wrap_v(SamplerState.WM_clamp)
            brdf_tex.set_wrap_w(SamplerState.WM_clamp)
            brdf_tex.set_anisotropic_degree(0)

            self._pipeline.stage_mgr.inputs[config["input"]] = brdf_tex

    def _load_skydome(self):
        skydome = RPLoader.load_texture("/$$rp/data/builtin_models/skybox/skybox.txo")
        skydome.set_wrap_u(SamplerState.WM_clamp)
        skydome.set_wrap_v(SamplerState.WM_clamp)
        self._pipeline.stage_mgr.inputs["DefaultSkydome"] = skydome

    def load_default_skybox(self):
        skybox = RPLoader.load_model("/$$rp/data/builtin_models/skybox/skybox.bam")
        return skybox

    def update(self):
        update = self._input_ubo.update_input
        
        view_mat = Globals.render.get_transform(self._showbase.cam).get_mat()

        zup_conversion = Mat4.convert_mat(CS_zup_right, CS_yup_right)
        update("view_mat_z_up", view_mat * zup_conversion)

        view_mat_billboard = Mat4(view_mat)
        view_mat_billboard.set_row(0, Vec3(1, 0, 0))
        view_mat_billboard.set_row(1, Vec3(0, 1, 0))
        view_mat_billboard.set_row(2, Vec3(0, 0, 1))
        update("view_mat_billboard", view_mat_billboard)

        update("camera_pos", self._showbase.camera.get_pos(Globals.render))

        curr_vp = self._input_ubo.get_input("view_proj_mat_no_jitter")
        update("last_view_proj_mat_no_jitter", curr_vp)
        curr_vp = Mat4(curr_vp)
        curr_vp.invert_in_place()
        curr_inv_vp = curr_vp
        update("last_inv_view_proj_mat_no_jitter", curr_inv_vp)

        proj_mat = Mat4(self._showbase.camLens.get_projection_mat())

        proj_mat_zup = Mat4.convert_mat(CS_yup_right, CS_zup_right) * proj_mat
        update("proj_mat", proj_mat_zup)

        update("inv_proj_mat", invert(proj_mat_zup))
        proj_mat.set_cell(1, 0, 0.0)
        proj_mat.set_cell(1, 1, 0.0)
        
        update("view_proj_mat_no_jitter", view_mat * proj_mat)

        update("frame_delta", Globals.clock.get_dt())
        update("smooth_frame_delta", 1.0 / max(1e-5, Globals.clock.get_average_frame_rate()))
        update("frame_time", Globals.clock.get_frame_time())

        update("current_film_offset", self._showbase.camLens.get_film_offset())
        update("frame_index", Globals.clock.get_frame_count())

        ws_frustum_directions = Mat4()
        vs_frustum_directions = Mat4()
        inv_proj_mat = Globals.base.camLens.get_projection_mat_inv()
        view_mat_inv = Mat4(view_mat)
        view_mat_inv.invert_in_place()

        for i, point in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
            result = inv_proj_mat.xform(Vec4(point[0], point[1], 1.0, 1.0))
            vs_dir = (zup_conversion.xform(result)).xyz.normalized()
            vs_frustum_directions.set_row(i, Vec4(vs_dir, 1))
            ws_dir = view_mat_inv.xform(Vec4(result.xyz, 0))
            ws_frustum_directions.set_row(i, ws_dir)

        update("vs_frustum_directions", vs_frustum_directions)
        update("ws_frustum_directions", ws_frustum_directions)

        update("screen_size", Globals.resolution)
        update("native_screen_size", Globals.native_resolution)
        update("lc_tile_count", self._pipeline.light_mgr.num_tiles)
