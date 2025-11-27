import os
import time
import subprocess
import logging
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_MINUTES = int(os.getenv('REFRESH_INTERVAL_MINUTES', 5))
PROJECT_ROOT = r'C:\Users\Jlowe\Desktop\PlatPursuit'
VENV_PYTHON = r'C:\Users\Jlowe\Desktop\PlatPursuit\venv\Scripts\python.exe'
DJANGO_SETTINGS_MODULE = os.getenv('DJANGO_SETTINGS_MODULE', 'plat_pursuit.settings')

def run_management_command():
    """Run Django management command in a subprocess. (Simulates cron)"""
    try:
        result = subprocess.run(
            [VENV_PYTHON, os.path.join(PROJECT_ROOT, 'manage.py'), 'refresh_verified_profiles'],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'DJANGO_SETTINGS_MODULE': DJANGO_SETTINGS_MODULE}
        )
        logger.info(f"Command output: {result.stdout.strip()}")
        if result.stderr:
            logger.error(f"Command error: {result.stderr.strip()}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}: {e.stderr}")
    except FileNotFoundError as e:
        logger.error(f"Path error. Check VENV_PYTHON or PROJECT_ROOT: {e}")

if __name__ == '__main__':
    logger.info(f"Starting refresh loop every {REFRESH_INTERVAL_MINUTES} minutes. Press Ctrl+C to stop.")
    try:
        while True:
            run_management_command()
            time.sleep(timedelta(minutes=REFRESH_INTERVAL_MINUTES).total_seconds())
    except KeyboardInterrupt:
        logger.info("Loop stopped gracefully.")