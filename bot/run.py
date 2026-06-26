import os
import subprocess
import sys

from bot.main import main


def run_tests_if_enabled() -> None:
    if os.getenv("RUN_TESTS_ON_STARTUP", "").lower() not in {"1", "true", "yes"}:
        return

    subprocess.run([sys.executable, "-m", "pytest"], check=True)


if __name__ == "__main__":
    run_tests_if_enabled()
    main()
