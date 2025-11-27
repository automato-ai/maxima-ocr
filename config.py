import yaml
import os

def read_config():
    current_directory = os.getcwd()
    print(f"The current working directory is: {current_directory}")

    return yaml.safe_load(open(current_directory + "/config.yaml"))
