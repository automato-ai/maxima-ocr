import yaml
import os
import logging

logger = logging.getLogger(__name__)

def read_config():
    current_directory = os.getcwd()
    logger.debug(f"Reading config from: {current_directory}")

    return yaml.safe_load(open(current_directory + "/config.yaml"))
