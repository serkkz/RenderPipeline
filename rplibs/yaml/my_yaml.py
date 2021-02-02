from .yaml_py3 import load as yaml_load
from .yaml_py3 import YAMLError, SafeLoader

def load_yaml_file_my(filename):
    try:
        with open(filename, "r") as handle:
            parsed_yaml = yaml_load(handle, Loader=SafeLoader)

    except IOError as msg:
        raise Exception("Failed to load YAML file: File not found")
        
    except YAMLError as msg:
        raise Exception("Failed to load YAML file: Invalid syntax")
    return parsed_yaml