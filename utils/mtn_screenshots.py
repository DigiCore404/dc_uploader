"""Movie Thumbnailer contact sheets and separate frames, using the original native tool."""
from pathlib import Path
import re
import shlex
import subprocess
from utils.runtime_tools import ROOT, find_tool


def generate(source,target,duration,config):
    binary=find_tool('mtn',config)
    if not binary:raise RuntimeError('MTN is selected but not available for this host. Configure MediaTools.MTNBIN or choose ffmpeg.')
    width=config.get('MediaTools','MTNWIDTH',fallback='1280')
    width=int(re.search(r'\d+',width)[0])
    if not 320<=width<=7680:raise ValueError('MTN width must be between 320 and 7680.')
    font=config.get('MediaTools','MTNFONTFILE',fallback='').strip()
    font=Path(font).expanduser() if font else ROOT/'bin/mtn/DejaVuSans.ttf'
    if not font.is_absolute():font=ROOT/font
    common=[binary,'-w',str(width),'-h','150','-P','-q']
    if font.is_file():common+=['-f',str(font)]
    sheet=config.getboolean('MediaTools','SCREENSHOT_SHEET',fallback=True)
    count=min(12,max(0,config.getint('MediaTools','SCREENSHOT_SINGLES',fallback=5)))
    if not sheet and not count:raise ValueError('Enable the MTN contact sheet or at least one separate screenshot.')
    def run(options,suffix):
        command=common+options+['-o',suffix,'-O',str(target),str(source)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=120)
        output=target/(source.stem+suffix)
        jpeg = False
        if output.is_file() and output.stat().st_size >= 4:
            with output.open('rb') as image:
                starts = image.read(2)
                image.seek(-2, 2)
                jpeg = starts == b'\xff\xd8' and image.read(2) == b'\xff\xd9'
        # MTN 3.5 returns 1 when a short video cannot fill every requested tile.
        # This is a usable-image warning; exit 2 and invalid/missing output fail.
        if result.returncode not in (0, 1) or not jpeg:
            raise RuntimeError('MTN could not generate an image. Check the selected binary, font and video.')
        if result.returncode == 1:
            print('MTN warning: fewer frames were available than requested; review the generated image.', flush=True)
    if sheet:
        options=shlex.split(config.get('MediaTools','MTNSETTING',fallback='-c 4 -r 4'))
        footer=config.get('MediaTools','MTNPOSTBY',fallback='DigitalCore').strip()
        if footer:
            options+=shlex.split(footer) if footer.startswith('-T ') else ['-T',footer]
        run(options,'-sheet.jpg')
        print('MTN contact sheet generated.',flush=True)
    for index in range(1,count+1):
        position=duration*index/(count+1)
        run(['-c','1','-r','1','-B',str(max(0,int(position)-1)),'-E',str(max(0,int(duration-position)-1)),'-z','-i','-t'],f'-{index:02d}.jpg')
        print(f'MTN frames: {index}/{count}',flush=True)
