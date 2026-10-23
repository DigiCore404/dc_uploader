import sys,types,importlib.util,json,io,contextlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
class Colors:
    def __getattr__(self,key):return ''
utils=types.ModuleType('utils');utils.__path__=[];sys.modules['utils']=utils
bc=types.ModuleType('utils.bcolors');bc.bcolors=Colors();sys.modules['utils.bcolors']=bc
fl=types.ModuleType('utils.filters_utils');fl.load_filters=lambda:{};sys.modules['utils.filters_utils']=fl
spec=importlib.util.spec_from_file_location('category_under_test',root/'utils/category_utils.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
class Config:
    def get(self,*args):return '/not-a-media-scan'
engine.load_config=lambda:Config()
engine.check_for_mp3_files=lambda path:False
filters=json.loads((root/'files/filters.json').read_text())
engine.load_filters=lambda:filters
def classify(name):
    with contextlib.redirect_stdout(io.StringIO()):return engine.determine_category(name)[1]
cases=[
('Formule.1.2026.10.11.Velka.cena.Singapore.Zavod.CZECH.1080p.HDTV.x265-iCZi','15'),
('Formula.E.2026.Round.09.Monaco.E.Prix.1080p.WEB.h264-EPOWORKS','15'),
('Serie.A.2024.04.19.Cagliari.Vs.Juventus.720p.WEB.H264-B0UNCE','15'),
('World.Snooker.Championship.2025.Quarter.Final.1080p.WEB.h264-EPOWORKS','15'),
('Nate Diaz vs. Jorge Masvidal boxing full fight ppvHD','15'),
('WWE.2K24.Update.v1.08.incl.DLC-RUNE','25'),
('Cricket.26.The.Official.Game.of.the.Ashes.Update.v1.0.3315-RUNE','25'),
('Field.of.Glory.Kingdoms.Burghers.and.Bombards-RUNE','25'),
('NBA.2K23.PS5-UNLiMiTED','26'),
('Stellar.Blade.Complete.Edition.PS5-DUPLEX','26'),
('Example.Game.PS4-DUPLEX','26'),
('Example.Game.NSW-Example','26'),
('Example.Game.XBOX360-Example','26'),
('Example_Game_v1.2_MacOS-I_KnoW','27'),
('Slay_The_Princess_v1.1c_MacOS-STRANGE','27'),
('Amiga_CD32_Gamer_Vol_01-MiRAGE','43'),
('VonsRetrobatArcade.v3.rar','43'),
('Example_v1.2_MacOS-MiRAGE0DAY','21'),
('Zine_Bootstrap_Studio_Lab_Edition_v6.6.1_macOS_x64_Incl_Keyfile_and_Patch-DrMARIO','21'),
('Charles.Web.Debugging.Proxy.v5.0.MacOS.Incl.Keymaker-CORE','21'),
('Managed.Switch.Port.Mapping.Tool.v2.90.0.Incl.Keygen.and.Patch-RLTS','18'),
('NASA.Explores.Osiris-Rex.To.Bennu.And.Back.2025.NTSC.DVDR-13','1'),
('Microsoft_Windows_11_Enterprise_Version_26H2_X64_GERMAN-CYGiSO','20'),
('unRAIDServer-7.1.4-x86_64.zip','20'),
('Udemy.Learn.PowerShell.Scripting.TUTORiAL-101101','33'),
('Pluralsight.Troubleshooting.Startup.and.Security.in.Windows.11.2026.BOOKWARE-GETH','28'),
('FKY.Sample.Pack.01.WAV-CT','20'),
('Artist-Album-WEB-FLAC-2026-GROUP','23'),
('Author-Book-AUDiOBOOK-WEB-2026-GROUP','44'),
('Author_Book_AUDIOBOOK_WEB_2026-GROUP','44'),
('MaXXXine.2024.1080p.BluRay.x264-OFT','6'),
('Glory.1989.1080p.BluRay.x264-Example','6'),
('The.Mac.2025.1080p.WEB.H264-Example','6'),
('Movie.2025.GERMAN.WEB.H264-SPiRiTBOX','2'),
('Don.Camillos.Rueckkehr.1953.DUAL.COMPLETE.BLURAY.iNTERNAL-ARMO','3'),
('The.White.Lotusl.2021.S02D2.NTSC.DVDR-ToF','11'),
('Letterkenny.S11D1.NTSC.DVDR-ToF','11'),
('Show.S01E01.1080p.WEB.h264-Example','9'),
('Show.S01.1080p.WEB.h264-Example','12'),
('Movie.2025.1080p.BDRip.x264-Example','6'),
('Movie.2025.720p.DVDRip.x264-Example','5'),
('Movie.2025.2160p.WEB.h265-Example','4'),
('Example.26.10.22.XXX.1080p.MP4-Example','31'),
('Alabama-Mountain_Music_(2023_CMA_Fest)-DDC-720p-h264-2023-SRPx','24'),
]
cases += [
    ('Caroline.Flack.Search.for.the.Truth.S01E01.DOC.SUBFRENCH.1080p.WEB.H264-AZR', '9'),
    ('Caroline.Flack.Search.for.the.Truth.S01E02.FiNAL.DOC.SUBFRENCH.1080p.WEB.H264-AZR', '9'),
    ('Me_First_And_The_Gimme_Gimmes-Have_A_Ball-CD-FLAC-1997-FAiNT', '23'),
    ('What.Time.Is.It.There.2001.DVDRip.x264-RO', '2'),
    ('Eagles.Of.The.Republic.2025.BDRip.x264-SNOW', '2'),
    ('Three 6 Mafia Choices - The Movie (2001) NTSC DVD5', '1'),
    ('d-tool.v10.3.German-iND', '18'),
    ('Zwei.Maenner.und.ein.Baby.2004.GERMAN.WEB.H264-SPiRiTBOX', '2'),
    ('Jagen.auf.Franzoesisch.2023.GERMAN.WEB.H264-SPiRiTBOX', '2'),
    ('Alone.The.Beast.S01.1080p.AMZN.WEB-DL.DD+2.0.H.264-Cinefeel', '12'),
    ('Donalds.Dolby.Vision.Toolset.READ.NFO-iND', '18'),
    ('The_Jimi_Hendrix_Experience-Electric_Ladyland-BLURAY-FLAC-2025-401', '23'),
    ('Strawberry.Shortcake.Rockaberry.Roll.2008.REMUX-ARCHIVED', '2'),
    ('A.Cat.In.Paris.2010.COMPLETE.NTSC.DVDR-WaLMaRT', '1'),
    ('Ringu.1998.2160p.GBR.Blu-ray.UHD.HEVC.DTS-HD.MA.5.1', '38'),
    ('Time_Crisis_Razing_Storm_EUR_PS3-DARKFORCE', '26'),
    ('Uncharted_Kodaishin_no_Hihou_JPN_PS4-HR', '26'),
    ('God_of_War_Collection_JPN_JB_PS3-Caravan', '26'),
    ('Artist-Republic-CD-FLAC-2026-GROUP', '23'),
    ('Author-Book-EPUB-2026-GROUP', '28'),
    ('Caroline.Flack.Show.S01E01.720p.WEB.h264-Example', '8'),
    ('Movie.Republic.2025.2160p.WEB.h265-Example', '4'),
    ('Movie.2025.2160p.UHD.BluRay.REMUX.HEVC-Example', '4'),
]
failed=[]
for name,expected in cases:
    actual=classify(name)
    if actual!=expected:failed.append((name,expected,actual))
engine.check_for_mp3_files=lambda path:True
for name,expected in [('Author-Book-AUDiOBOOK-WEB-2026-GROUP','44'),('Author_Book_AUDIOBOOK_WEB_2026-GROUP','44'),('Artist-Album-2026-GROUP','22')]:
    actual=classify(name)
    if actual!=expected:failed.append((name+' [MP3 files]',expected,actual))
with contextlib.redirect_stdout(io.StringIO()):manual=engine.determine_category('WWE.2K24-RUNE','9')
assert manual[1]=='9'
for row in failed:print('FAIL',*row)
assert not failed
print('PASS: '+str(len(cases)+4)+' category regressions, including MP3 audiobooks and manual overrides.')
