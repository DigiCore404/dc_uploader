#!/usr/bin/env python3
import logging
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import threading
import signal
from pathlib import Path
from typing import Iterable, List, Sequence

SOURCE = Path("/uploaders/LINKS")
COMPLETE = Path("/uploaders/COMPLETE")
LOGFILE = Path("/uploaders/symlink_errors.log")
DEBUG_LOGFILE = Path("/uploaders/symlink_debug.log")
INCOMING_ROOT = Path('/glftpd/site')
ARCHIVE_ROOT = Path('/glftpd')
PENDING_LINKS = Path('/uploaders/durable_game_links.json')
PENDING_LOCK = threading.RLock()

UPLOADERS = [
    {
        "name": "uploader1",
        "workdir": Path("/uploaders/dc_uploader1"),
        "command": ["scripts/upload.sh"],
    },
    {
        "name": "uploader2",
        "workdir": Path("/uploaders/dc_uploader2"),
        "command": ["scripts/upload.sh"],
    },
]

# Extra uploader for XXX releases. Not part of UPLOADERS on purpose: the
# round-robin pool (uploader1/uploader2) keeps working exactly as before for
# every release, and this one is launched ADDITIONALLY whenever the release
# matches is_xxx_release() below.
XXX_UPLOADER = {
    "name": "upper",
    "workdir": Path("/uploaders/upper"),
    "command": ["scripts/upload.sh"],
    # Route here when any of these tokens appear in the category or path.
    "match_tokens": ("XXX",),
}

SKIP_DIRS = {"HVA", "VA", "HV", "IMDB", "TVMAZE"}
IGNORE_FILES = {".message"}
# goftpd incomplete/status markers are symlinks; their names end in these
# suffixes. They are transient and must never be linked into COMPLETE.
STATUS_MARKER_SUFFIXES = ("-MISSING", "-OFFLINE", "-NOSFV", "-INCOMPLETE")
FAST_SETTLE_CATEGORIES = {"MP3", "FLAC", "0DAY", "XXX-IMAGESETS", "EBOOKS", "EBOOK"}
MAX_ATTEMPTS = 5
STABLE_CHECKS = 2
STABLE_DELAY = 2
DEBUG_SAMPLE_LIMIT = 10
WAIT_FOR_STABLE = os.environ.get("LINK_WAIT_FOR_STABLE", "0") == "1"
RETRY_DELAY = float(os.environ.get("LINK_RETRY_DELAY", "0"))
FAST_SETTLE_DELAY = float(os.environ.get("LINK_FAST_SETTLE_DELAY", "0.1"))
FAST_SETTLE_MAX = float(os.environ.get("LINK_FAST_SETTLE_MAX", "1.0"))
MP3_SETTLE_MAX = float(os.environ.get("LINK_MP3_SETTLE_MAX", "5.0"))
MP3_REQUIRE_COVER = os.environ.get("LINK_MP3_REQUIRE_COVER", "1") == "1"
MP3_AUDIO_EXTS = {".mp3", ".flac", ".m4a"}
MP3_REQUIRED_EXTS = {".nfo", ".sfv", ".m3u"}
MP3_COVER_EXTS = {".jpg", ".jpeg"}

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"


class ConsoleFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: DIM + WHITE,
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD + RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        color = self.LEVEL_COLORS.get(record.levelno, WHITE)
        level = f"{color}{record.levelname:<7}{RESET}"
        message = record.getMessage()
        return f"{DIM}{timestamp}{RESET} {level} {message}"


class UploaderPicker:
    def __init__(self, uploaders: Sequence[dict]) -> None:
        self._uploaders = list(uploaders)
        self._queue: List[dict] = []
        self._reshuffle()

    def _reshuffle(self) -> None:
        self._queue = self._uploaders[:]
        random.shuffle(self._queue)

    def next(self) -> dict:
        if not self._queue:
            self._reshuffle()
        return self._queue.pop(0)


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ConsoleFormatter())
    root.addHandler(handler)


def should_skip_dir(path: Path, src_root: Path) -> bool:
    del src_root
    name_upper = path.name.upper()
    if name_upper in SKIP_DIRS:
        return True
    return re.search(r"(^|[^A-Z0-9])(HVA|VA|HV|IMDB|TVMAZE)([^A-Z0-9]|$)", name_upper) is not None


def is_status_marker(name: str) -> bool:
    upper = name.upper()
    return any(upper.endswith(suffix) for suffix in STATUS_MARKER_SUFFIXES)


def debug_log(message: str) -> None:
    DEBUG_LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with DEBUG_LOGFILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def banner(title: str, color: str = BLUE) -> None:
    line = "=" * 72
    logging.info("%s%s%s", color + BOLD, line, RESET)
    logging.info("%s%s%s", color + BOLD, f"  {title}", RESET)
    logging.info("%s%s%s", color + BOLD, line, RESET)


def info_line(label: str, value: str, color: str = WHITE) -> None:
    logging.info("%s%-14s%s %s%s%s", BOLD, f"{label}:", RESET, color, value, RESET)


def success(message: str) -> None:
    logging.info("%s%s%s", GREEN + BOLD, message, RESET)


def warn(message: str) -> None:
    logging.warning("%s%s%s", YELLOW + BOLD, message, RESET)


def error(message: str) -> None:
    logging.error("%s%s%s", RED + BOLD, message, RESET)


def section(message: str, color: str = MAGENTA) -> None:
    logging.info("%s%s%s", color + BOLD, message, RESET)


def iter_expected_files(src_root: Path) -> List[Path]:
    expected: List[Path] = []
    skipped_dirs: List[str] = []
    if not src_root.is_dir():
        return expected
    for root, dirs, files in os.walk(src_root):
        original_dirs = dirs[:]
        dirs[:] = [
            dirname
            for dirname in dirs
            if not should_skip_dir(Path(root) / dirname, src_root)
        ]
        skipped_dirs.extend(
            str((Path(root) / dirname).relative_to(src_root))
            for dirname in original_dirs
            if should_skip_dir(Path(root) / dirname, src_root)
        )
        root_path = Path(root)
        for filename in files:
            if filename in IGNORE_FILES:
                continue
            full_path = root_path / filename
            # goftpd status markers (-MISSING etc.) are transient symlinks that
            # get cleared a moment after the release completes. Never link them:
            # a real release file is a regular file, every marker is a symlink.
            if full_path.is_symlink() or is_status_marker(filename):
                continue
            expected.append(full_path.relative_to(src_root))
    expected.sort()
    if skipped_dirs:
        debug_log(
            f"Skipped exact-match metadata directories under {src_root}: "
            + ", ".join(skipped_dirs[:DEBUG_SAMPLE_LIMIT])
            + (" ..." if len(skipped_dirs) > DEBUG_SAMPLE_LIMIT else "")
        )
    return expected


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def is_incoming_source(path: Path) -> bool:
    try:
        return len(path.relative_to(INCOMING_ROOT).parts) >= 2
    except (ValueError, IndexError):
        return False


def record_pending_archive(source: Path, destination: Path) -> None:
    """Track temporary hardlinks until the archive mover finishes moving the source."""
    if not is_incoming_source(source):
        return
    files = {}
    for rel in iter_expected_files(source):
        st = (destination / rel).stat()
        files[str(rel)] = {'size': st.st_size, 'inode': st.st_ino, 'device': st.st_dev}
    with PENDING_LOCK:
        pending = json.loads(PENDING_LINKS.read_text()) if PENDING_LINKS.exists() else {}
        pending[str(destination)] = {'source': str(source), 'files': files}
        _save_pending(pending)


def _save_pending(pending: dict) -> None:
    PENDING_LINKS.parent.mkdir(parents=True, exist_ok=True)
    temporary = PENDING_LINKS.with_suffix('.tmp')
    temporary.write_text(json.dumps(pending), encoding='utf8')
    temporary.replace(PENDING_LINKS)


def file_version(st: os.stat_result) -> tuple:
    # Reading may update atime; only identity and content-change timestamps matter.
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def archive_file_matches(destination: Path, archive: Path, original: dict) -> bool:
    """Accept the mover's completed copy, retaining files that differ or still change."""
    if not archive.is_file() or not destination.is_file():
        return False
    before = destination.stat()
    archived = archive.stat()
    if before.st_size != archived.st_size:
        return False
    if before.st_size == original['size']:
        return True
    # The completion event may precede the final writes. An old snapshot size
    # must not strand a completed hardlink, but a different archive is unsafe.
    with destination.open('rb') as left, archive.open('rb') as right:
        while True:
            block = left.read(1024 * 1024)
            if block != right.read(1024 * 1024):
                return False
            if not block:
                break
    return (file_version(before) == file_version(destination.stat())
            and file_version(archived) == file_version(archive.stat()))


def reconcile_archived_releases() -> None:
    """Rebind matching files independently; retain every unmatched original."""
    with PENDING_LOCK:
        if not PENDING_LINKS.exists():
            return
        pending = json.loads(PENDING_LINKS.read_text())
    for destination, item in list(pending.items()):
        source = Path(item['source'])
        if source.exists():
            continue
        try:
            relative = source.relative_to(INCOMING_ROOT)
            relatives = [relative]
            if relative.parts[0].upper() in {'FOREIGN', 'PRE', 'ARCHIVE'}:
                relatives.append(Path(*relative.parts[1:]))
            archives = [disk / rel for disk in ARCHIVE_ROOT.glob('DISK*')
                        for rel in relatives if (disk / rel).is_dir()]
            if not archives or not item['files']:
                continue
            for rel, original in item['files'].items():
                dest = Path(destination) / rel
                if dest.is_symlink() or not dest.exists():
                    continue
                st = dest.stat()
                if (st.st_dev, st.st_ino) != (original['device'], original['inode']):
                    logging.warning('Retaining externally changed incoming link: %s', dest)
                    continue
                for archive in archives:
                    target = archive / rel
                    if not archive_file_matches(dest, target, original):
                        continue
                    temporary = dest.with_name(dest.name + '.archive-link')
                    if temporary.exists() or temporary.is_symlink():
                        logging.warning('Retaining unexpected archive staging file: %s', temporary)
                        break
                    temporary.symlink_to(target.resolve(strict=True))
                    # Do not overwrite a link replaced by another process.
                    current = dest.stat()
                    if dest.is_symlink() or file_version(current) != file_version(st):
                        temporary.unlink()
                        break
                    temporary.replace(dest)
                    break
            if all((Path(destination) / rel).is_symlink() and (Path(destination) / rel).is_file()
                   for rel in item['files']):
                debug_log(f'Rebound archived release {source.name}')
                # Another completion event may have refreshed this record while
                # archive IO was in progress. Merge against the latest registry.
                with PENDING_LOCK:
                    latest = json.loads(PENDING_LINKS.read_text())
                    if latest.get(destination) == item:
                        del latest[destination]
                        _save_pending(latest)
        except (OSError, ValueError):
            # A single missing file/mount must not block all other releases.
            logging.exception('Could not reconcile archive for %s', source.name)

def maintain_archive_links() -> None:
    while True:
        try:
            reconcile_archived_releases()
        except Exception:
            logging.exception('Could not reconcile archived release links')
        time.sleep(30)


def sync_links(src_root: Path, dest_root: Path) -> None:
    for relpath in iter_expected_files(src_root):
        src_file = src_root / relpath
        stable_source = src_file.resolve(strict=True)
        dest_file = dest_root / relpath
        ensure_parent(dest_file)
        if dest_file.is_symlink():
            try:
                if os.readlink(dest_file) == str(stable_source) and not is_incoming_source(stable_source):
                    continue
            except FileNotFoundError:
                dest_file.unlink(missing_ok=True)
        elif dest_file.exists():
            continue
        if dest_file.exists() or dest_file.is_symlink():
            dest_file.unlink(missing_ok=True)
        # Incoming release directories are real directories, moved by fin-space.
        # A hardlink survives that move; the maintenance task releases it after
        # replacing it with a symlink to verified archive data.
        if is_incoming_source(stable_source):
            os.link(stable_source, dest_file)
        else:
            os.symlink(stable_source, dest_file)


def collect_missing_links(src_root: Path, dest_root: Path) -> List[Path]:
    missing: List[Path] = []
    for relpath in iter_expected_files(src_root):
        dest_file = dest_root / relpath
        if not dest_file.is_symlink() and not (
            dest_file.is_file() and os.path.samefile(src_root / relpath, dest_file)
        ):
            missing.append(relpath)
            continue
        if not dest_file.exists():
            missing.append(relpath)
    return missing


def collect_broken_links(dest_root: Path) -> List[Path]:
    broken: List[Path] = []
    if not dest_root.exists():
        return broken
    for root, _, files in os.walk(dest_root):
        root_path = Path(root)
        for filename in files:
            path = root_path / filename
            if path.is_symlink() and not path.exists():
                broken.append(path.relative_to(dest_root))
    broken.sort()
    return broken


def count_linked_files(dest_root: Path) -> int:
    count = 0
    if not dest_root.exists():
        return count
    for root, _, files in os.walk(dest_root):
        root_path = Path(root)
        for filename in files:
            if (root_path / filename).is_file():
                count += 1
    return count


def verify_links(src_root: Path, dest_root: Path) -> tuple[bool, List[Path], List[Path]]:
    expected = iter_expected_files(src_root)
    missing = collect_missing_links(src_root, dest_root)
    broken = collect_broken_links(dest_root)
    linked = count_linked_files(dest_root)
    if not expected or missing or broken:
        logging.warning(
            "Verification failed: expected=%s linked=%s missing=%s broken=%s",
            len(expected),
            linked,
            len(missing),
            len(broken),
        )
        if missing:
            debug_log(
                f"Missing link sample for {dest_root}: "
                + ", ".join(str(path) for path in missing[:DEBUG_SAMPLE_LIMIT])
                + (" ..." if len(missing) > DEBUG_SAMPLE_LIMIT else "")
            )
        if broken:
            debug_log(
                f"Broken link sample for {dest_root}: "
                + ", ".join(str(path) for path in broken[:DEBUG_SAMPLE_LIMIT])
                + (" ..." if len(broken) > DEBUG_SAMPLE_LIMIT else "")
            )
        return False, missing, broken
    if len(expected) != linked:
        logging.warning(
            "Verification failed: expected=%s linked=%s",
            len(expected),
            linked,
        )
        debug_log(
            f"Count mismatch for {dest_root}: expected={len(expected)} linked={linked}"
        )
        return False, [], []
    logging.info("Verification OK: expected=%s linked=%s broken=0", len(expected), linked)
    return True, [], []


def wait_for_source_stable(src_root: Path) -> bool:
    last_snapshot = None
    stable_rounds = 0
    for _ in range(10):
        if not src_root.is_dir():
            time.sleep(STABLE_DELAY)
            continue
        snapshot = tuple(iter_expected_files(src_root))
        if snapshot == last_snapshot:
            stable_rounds += 1
            if stable_rounds >= STABLE_CHECKS:
                return True
        else:
            stable_rounds = 0
            last_snapshot = snapshot
        time.sleep(STABLE_DELAY)
    return src_root.is_dir()


def category_uses_fast_settle(category: str) -> bool:
    category_upper = category.upper()
    return any(item in category_upper for item in FAST_SETTLE_CATEGORIES)


def has_required_release_files(category: str, files: Sequence[Path]) -> bool:
    suffixes = {path.suffix.lower() for path in files}
    category_upper = category.upper()
    if "MP3" in category_upper:
        if not MP3_REQUIRED_EXTS.issubset(suffixes):
            return False
        if not suffixes.intersection(MP3_AUDIO_EXTS):
            return False
        if MP3_REQUIRE_COVER and not suffixes.intersection(MP3_COVER_EXTS):
            return False
        return True
    if category_uses_fast_settle(category) and ".nfo" not in suffixes:
        return False
    return True


def fast_settle_max_for_category(category: str) -> float:
    if "MP3" in category.upper():
        return MP3_SETTLE_MAX
    return FAST_SETTLE_MAX


def missing_required_summary(category: str, files: Sequence[Path]) -> str:
    suffixes = {path.suffix.lower() for path in files}
    category_upper = category.upper()
    missing: List[str] = []
    if "MP3" in category_upper:
        missing.extend(sorted(MP3_REQUIRED_EXTS - suffixes))
        if not suffixes.intersection(MP3_AUDIO_EXTS):
            missing.append("audio")
        if MP3_REQUIRE_COVER and not suffixes.intersection(MP3_COVER_EXTS):
            missing.append("cover")
    elif category_uses_fast_settle(category) and ".nfo" not in suffixes:
        missing.append(".nfo")
    return ", ".join(missing) if missing else "none"


def fast_settle_source(src_root: Path, release: str, category: str) -> List[Path]:
    max_wait = fast_settle_max_for_category(category)
    deadline = time.monotonic() + max_wait
    last_files: List[Path] = []
    last_snapshot: tuple[Path, ...] | None = None
    stable_rounds = 0
    while True:
        files = iter_expected_files(src_root)
        snapshot = tuple(files)
        if snapshot == last_snapshot:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_snapshot = snapshot
        last_files = files
        if stable_rounds >= 1 and has_required_release_files(category, files):
            break
        if time.monotonic() >= deadline:
            debug_log(
                f"Fast settle timeout for {release}: category={category} "
                f"files={len(files)} required_ok={has_required_release_files(category, files)} "
                f"missing_required={missing_required_summary(category, files)}"
            )
            break
        time.sleep(FAST_SETTLE_DELAY)
    debug_log(
        f"Fast settle result for {release}: category={category} files={len(last_files)} "
        f"missing_required={missing_required_summary(category, last_files)}"
    )
    return last_files


def log_failure(
    release: str,
    src_root: Path,
    dest_root: Path,
    missing: Iterable[Path],
    broken: Iterable[Path],
) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    broken_list = list(broken)
    missing_list = list(missing)
    with LOGFILE.open("a", encoding="utf-8") as handle:
        handle.write("==================================================\n")
        handle.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.write(f"Release: {release}\n")
        handle.write(f"Source: {src_root}\n")
        handle.write(f"Destination: {dest_root}\n")
        handle.write(f"Expected files: {len(iter_expected_files(src_root))}\n")
        handle.write(f"Linked files: {count_linked_files(dest_root)}\n")
        handle.write("Missing links:\n")
        for relpath in missing_list:
            handle.write(f"  - {relpath}\n")
        handle.write("Broken links:\n")
        for relpath in broken_list:
            link_path = dest_root / relpath
            try:
                target = os.readlink(link_path)
            except OSError:
                target = "<unreadable>"
            handle.write(f"  - {link_path} -> {target}\n")
        handle.write("==================================================\n\n")


def parse_link_file(link_file: Path) -> tuple[str, Path] | None:
    try:
        raw_text = link_file.read_text(encoding="utf-8", errors="ignore").strip()
        parts = raw_text.split(maxsplit=2)
    except OSError as exc:
        logging.error("Could not read %s: %s", link_file, exc)
        return None
    if len(parts) < 3:
        logging.error("Unexpected format in %s", link_file)
        debug_log(f"Unexpected link file format in {link_file}: {raw_text!r}")
        return None
    debug_log(f"Parsed link file {link_file}: raw={raw_text!r}")
    return parts[1], Path(parts[2])


def resolve_source_release(source_path: Path, release: str) -> Path:
    # New link files store the full release path. Older ones may store the parent path.
    if source_path.name == release:
        return source_path
    return source_path / release


def is_xxx_release(category: str, src_release: Path, release: str) -> bool:
    """True when the extra XXX uploader should ALSO run for this release.

    Matches on the goftpd section/category and on the source path, so both
    'XXX' categories (XXX, XXX-IMAGESETS, ...) and releases living under an
    /XXX/ directory count.
    """
    haystack = f"{category} {src_release} {release}".upper()
    return any(token in haystack for token in XXX_UPLOADER["match_tokens"])


def launch_uploaders(uploaders: Sequence[dict], release: str) -> None:
    for uploader in uploaders:
        launch_uploader(uploader, release)


def launch_uploader(uploader: dict, release: str) -> None:
    command = uploader["command"] + [release]
    workdir = uploader["workdir"]
    section("Uploader Launch", GREEN)
    info_line("Uploader", uploader["name"], GREEN)
    info_line("Release", release, WHITE)
    info_line("Workdir", str(workdir), DIM + WHITE)
    # Preserve early backend failures as well as errors before an API request.
    log_path = workdir / "files" / "launcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'ab') as output:
        output.write((time.strftime("[%Y-%m-%d %H:%M:%S] ") + release + "\n").encode())
        output.flush()
        subprocess.Popen(
            command,
            cwd=workdir,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def process_release(release: str, picker: UploaderPicker) -> None:
    banner(f"New Release Detected: {release}", BLUE)
    debug_log(f"Start processing release {release}")
    uploader = picker.next()
    section("Uploader Selection", CYAN)
    info_line("Selected", uploader["name"], CYAN)
    link_file = SOURCE / release
    link_info = parse_link_file(link_file)
    if link_info is None:
        return
    category, source_path = link_info
    src_release = resolve_source_release(source_path, release)
    dest_release = COMPLETE / release
    # The round-robin uploader always runs, exactly as before. When the
    # release is XXX, the extra 'upper' uploader is launched AS WELL.
    uploaders_to_launch = [uploader]
    if is_xxx_release(category, src_release, release):
        uploaders_to_launch.append(XXX_UPLOADER)
    debug_log(
        f"Resolved release paths for {release}: category={category} source_path={source_path} "
        f"src_release={src_release} dest_release={dest_release}"
    )
    section("Resolved Paths", MAGENTA)
    info_line("Link file", str(link_file), DIM + WHITE)
    info_line("Category", category, CYAN)
    info_line("Source", str(src_release), WHITE)
    info_line("Dest", str(dest_release), WHITE)
    if XXX_UPLOADER in uploaders_to_launch:
        info_line("Extra", f"{XXX_UPLOADER['name']} (XXX match)", MAGENTA)
        debug_log(f"XXX match for {release}: also launching {XXX_UPLOADER['name']}")
    if WAIT_FOR_STABLE and not wait_for_source_stable(src_release):
        warn(f"Source directory did not stabilize: {src_release}")
        debug_log(f"Source directory did not fully stabilize for {release}: {src_release}")
    if category_uses_fast_settle(category):
        section("Fast Settle", YELLOW)
        info_line(
            "Enabled",
            f"{fast_settle_max_for_category(category)}s max / {FAST_SETTLE_DELAY}s poll",
            YELLOW,
        )
        expected_files = fast_settle_source(src_release, release, category)
    else:
        expected_files = iter_expected_files(src_release)
    debug_log(f"Expected source file count for {release}: {len(expected_files)}")
    if expected_files:
        debug_log(
            f"Expected file sample for {release}: "
            + ", ".join(str(path) for path in expected_files[:DEBUG_SAMPLE_LIMIT])
            + (" ..." if len(expected_files) > DEBUG_SAMPLE_LIMIT else "")
        )
    section("Source Scan", BLUE)
    info_line("Expected", str(len(expected_files)), CYAN)
    if not dest_release.exists():
        section("Link Mode", MAGENTA)
        info_line("Action", "create symlinks", GREEN)
    else:
        section("Link Mode", MAGENTA)
        info_line("Action", "verify and repair symlinks", YELLOW)
    missing: List[Path] = []
    broken: List[Path] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        section(f"Sync Attempt {attempt}/{MAX_ATTEMPTS}", CYAN)
        info_line("Release", release, WHITE)
        try:
            sync_links(src_release, dest_release)
        except OSError as exc:
            error(f"Symlink creation failed for {release}: {exc}")
            debug_log(f"Symlink creation failed for {release} on attempt {attempt}: {exc}")
            if RETRY_DELAY > 0:
                time.sleep(RETRY_DELAY)
            continue
        ok, missing, broken = verify_links(src_release, dest_release)
        if ok:
            total = count_linked_files(dest_release)
            success(f"All {total} symlinks OK for {release}")
            debug_log(f"Verification OK for {release}: linked={total}")
            record_pending_archive(src_release.resolve(strict=True), dest_release)
            launch_uploaders(uploaders_to_launch, release)
            return
        warn(
            f"Retrying verification for {release} "
            f"(missing={len(missing)} broken={len(broken)})"
        )
        if RETRY_DELAY > 0:
            time.sleep(RETRY_DELAY)
    error(f"Failed after {MAX_ATTEMPTS} attempts. Logging to {LOGFILE}")
    debug_log(f"Verification failed after {MAX_ATTEMPTS} attempts for {release}")
    log_failure(release, src_release, dest_release, missing, broken)
    # Keep the event available for a deliberate repair, never upload empty data.
    return


def watch_releases() -> None:
    if shutil.which("inotifywait") is None:
        raise RuntimeError("inotifywait not found in PATH")
    picker = UploaderPicker(UPLOADERS)
    threading.Thread(target=maintain_archive_links, daemon=True).start()
    banner("Link Watcher Started", GREEN)
    info_line("Watching", str(SOURCE), WHITE)
    info_line("Destination", str(COMPLETE), WHITE)
    info_line("Debug log", str(DEBUG_LOGFILE), DIM + WHITE)
    info_line("Error log", str(LOGFILE), DIM + WHITE)
    info_line("Uploaders", ", ".join(item["name"] for item in UPLOADERS), CYAN)
    info_line(
        "XXX extra",
        f"{XXX_UPLOADER['name']} ({XXX_UPLOADER['workdir']}) also runs on {'/'.join(XXX_UPLOADER['match_tokens'])}",
        MAGENTA,
    )
    info_line("Stable wait", "enabled" if WAIT_FOR_STABLE else "disabled", YELLOW)
    info_line("Retry delay", str(RETRY_DELAY), YELLOW)
    info_line("Fast settle", f"{FAST_SETTLE_MAX}s selected / {MP3_SETTLE_MAX}s MP3", YELLOW)
    section("Waiting for new releases...", BLUE)
    process = subprocess.Popen(
        [
            "inotifywait",
            "--monitor",
            "--format",
            "%f",
            "-e",
            "moved_to",
            "-e",
            "close_write",
            str(SOURCE),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            release = line.strip()
            if not release:
                continue
            try:
                process_release(release, picker)
            except Exception as exc:
                logging.exception("Unexpected error while processing %s: %s", release, exc)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> int:
    def request_stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, request_stop)
    configure_logging()
    try:
        watch_releases()
    except KeyboardInterrupt:
        section("Watcher stopped", YELLOW)
        return 0
    except Exception as exc:
        error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
