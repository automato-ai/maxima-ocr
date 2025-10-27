import yaml
import os

current_directory = os.getcwd()
print(f"The current working directory is: {current_directory}")

config = yaml.safe_load(open(current_directory + "\config.yaml"))
