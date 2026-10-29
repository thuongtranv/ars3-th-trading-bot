import os

import yaml
from dotenv import load_dotenv

load_dotenv()


def get_env(*names, default=None):
    """First non-empty environment variable among `names` (later names are legacy aliases)."""
    return next((os.environ[n] for n in names if os.environ.get(n)), default)


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
