"""Start a Mateo service with settings resolved by asm-exec."""

import json
import os
import sys


def main():
    try:
        settings = json.loads(os.environ.pop("MATEO_RUNTIME_SETTINGS"))
        if not isinstance(settings, dict):
            raise ValueError
        required = {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
        if not all(isinstance(settings.get(key), str) and settings[key] for key in required):
            raise ValueError
        if not all(isinstance(value, str) for value in settings.values()):
            raise ValueError
    except (KeyError, ValueError):
        sys.exit("Invalid Mateo runtime configuration")

    os.environ.pop("AWS_TOKEN", None)
    os.environ.update(settings)
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
