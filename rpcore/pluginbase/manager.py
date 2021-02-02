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

import importlib
import collections

from rplibs.yaml.my_yaml import load_yaml_file_my

from rpcore.pluginbase.setting_types import make_setting_from_data
from rpcore.pluginbase.day_setting_types import make_daysetting_from_data

class PluginManager():
    def __init__(self, pipeline):
    
        self._pipeline = pipeline
        
        self.settings = {}
        
        self.day_settings = {}
        
        self.instances = {}
        
        self.enabled_plugins = []

        # Used by the plugin configurator and to only load the required data
        self.requires_daytime_settings = True

    def load(self, plugins_used):
        # Список используемых плагинов.
        plugins_list = plugins_used.split()
        
        # Импортируем модули плагинов и создаем экземляры.
        for name in plugins_list:
            self.instances[name] = importlib.import_module("rpplugins."+name+".plugin").Plugin(self._pipeline)
        
        for name in plugins_list:
        
            config = load_yaml_file_my("../render/rpplugins/"+name+"/config.yaml")

            config["settings"] = config["settings"] or []
                
            config["daytime_settings"] = config["daytime_settings"] or []

            self.settings[name] = collections.OrderedDict([(k, make_setting_from_data(v)) for k, v in config["settings"]])
            
            # Добавляем плагин в список включенных.
            self.enabled_plugins.append(name)

            if self.requires_daytime_settings:
                self.day_settings[name] = collections.OrderedDict([(k, make_daysetting_from_data(v)) for k, v in config["daytime_settings"]])

        if self.requires_daytime_settings:

            overrides = load_yaml_file_my("../render/config/daytime.yaml")

            for name, settings in overrides["control_points"].items() or {}.items():
                # Проверка есть ли плагин в списке используемых.
                if name in plugins_list:
                    for setting_id, control_points in settings.items():
                        self.day_settings[name][setting_id].set_control_points(control_points)


    def trigger_hook(self, hook_name):

        hook_method = "on_"+hook_name

        for name in self.enabled_plugins:
        
            plugin_handle = self.instances[name]

            if hasattr(plugin_handle, hook_method):
            
                getattr(plugin_handle, hook_method)()

    def is_plugin_enabled(self, plugin_id):
        return plugin_id in self.enabled_plugins

    def get_setting_handle(self, plugin_id, setting_id):
        return self.settings[plugin_id][setting_id]

    def init_defines(self):
        for name in self.enabled_plugins:
            pluginsettings = self.settings[name]
            self._pipeline.stage_mgr.defines["HAVE_PLUGIN_{}".format(name)] = 1
            for setting_id, setting in pluginsettings.items():
                if setting.shader_runtime or not setting.runtime:
                    setting.add_defines(name, setting_id, self._pipeline.stage_mgr.defines)

    def on_setting_changed(self, plugin_id, setting_id, value):
        if plugin_id not in self.settings or setting_id not in self.settings[plugin_id]:
            print("Got invalid setting change:", plugin_id, "/", setting_id)
            return

        setting = self.settings[plugin_id][setting_id]
        setting.set_value(value)

        if setting.runtime or setting.shader_runtime:
            update_method = self.instances[plugin_id], "update_" + setting_id
            if hasattr(*update_method):
                getattr(*update_method)()

        if setting.shader_runtime:
            self.init_defines()
            self._pipeline.stage_mgr.write_autoconfig()
            self.instances[plugin_id].reload_shaders()
