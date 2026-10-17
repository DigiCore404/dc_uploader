"""Screenshot generation using the configured FFmpeg or MTN engine."""
import json
import os
import subprocess
from pathlib import Path
from utils.config_loader import ConfigLoader
from utils.mediainfo_utils import iter_media_files, media_priority, is_sample_path


def generate_screenshots(directory, category_id):
    config = ConfigLoader().get_config()
    files = sorted(iter_media_files(directory), key=media_priority)
    main_files = [p for p in files if not is_sample_path(p)]
    if main_files:
        files = main_files
    if not main_files and not config.getboolean('Settings','SCREENSHOT_SAMPLE_FALLBACK',fallback=True):
        print('Only sample videos found; sample fallback is disabled.')
        return
    if not files:
        print('No video found; screenshots skipped.')
        return
    source = files[0]
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'json', str(source)], capture_output=True, text=True,
                           check=True, timeout=60)
    duration = float(json.loads(probe.stdout)['format']['duration'])
    if duration <= 0:
        raise ValueError('Video duration is not valid.')
    target = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid()) / 'screens'
    target.mkdir(parents=True, exist_ok=True)
    engine=config.get('MediaTools','SCREENSHOT_ENGINE',fallback='ffmpeg').strip().lower()
    if engine=='mtn':
        from utils.mtn_screenshots import generate
        return generate(source,target,duration,config)
    if engine!='ffmpeg':raise ValueError('Choose ffmpeg or mtn as the screenshot engine.')
    count = min(12, max(1, config.getint('MediaTools', 'SCREENSHOT_SINGLES', fallback=5)))
    for i in range(1, count + 1):
        output = target / f'screenshot-{i:02d}.jpg'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-ss', str(duration * i / (count + 1)), '-i', str(source),
                        '-frames:v', '1', '-vf', 'scale=min(1280\\,iw):-2',
                        '-q:v', '3', str(output)], check=True, timeout=90)
        if not output.exists() or not output.stat().st_size:
            raise RuntimeError(f'Screenshot {i} could not be generated.')
        print(f'Screenshots: {i}/{count}', flush=True)
