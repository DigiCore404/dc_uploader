# Still under active development, things might change/break

## What is DC Uploader?

A simple python tool built for ubuntu to create and upload torrents. Debian is unsupported at the moment but hopefully will be soon.

- Checks for duplicates.
- Creates torrents.
- Automatically adds uploaded torrent to client by copying the created .torrent file to client watch directory.
- Takes screenshots with movie thumbnailer and uploads to image host.
  - Works on rar'd content as well. Only done when no sample is found.
- Added the mediainfo to the uploaded torrent.
- Automatically enable HTTPS with either certbot (installed via pip) or a self-signed certificate.
    - Support for cloudflare DNS challenge via cloudflare API token.
    - Sets up automatic certbot cert renewal and certbot updates, if not already existing.
- Gets video game information from IGDB (API key required).
- Gets movie/show information from TMDB (API key required).
- Automatically edit .torrent from source content tracker instead of generating new .torrent with torf.

## Install

1. Download the release tarball.

2. Untar to current directory with `tar -xvzf dc_uploader-[RELEASE_VERSION].tar.gz`.

3. cd into the unzipped directory (not strictly necessary, but allows you to call install with just `./install.sh`)

4. Run install.sh, which will install the necessary packages and python virtual environments. Run `install.sh -h` or `install.sh --help` for help. Running install.sh with no arguments will install in interactive mode, but the only thing the script will ask for is a domain name. The domain name can be provided with args:
   - `install.sh -d [fully qualified domain name]` or 
   - `install.sh --domain [fully qualified domain name]`
   - Run `chmod +x install.sh` to make install script executable if you're getting permission errors when trying to run `install.sh`

5. Modify config.ini with required settings:
    - USERNAME
    - PASSWORD
    - CAPTCHA_PASSKEY
    - LOGINTXT
    - APIKEY
    - CLIENT_ID
    - CLIENT_SECRET
    - ETORFPATH
    - ANNOUNCEURL
    - WATCHFOLDER
    - DATADIR
6. You're done.

### config.ini settings

Do not change the headers or location of the settings. If it is not mentioned below, just leave it its default.

- user: If you are exposing the web app to the wider Internet, you should choose a more secure username.
- password: If you are exposing the web app to the wider Internet, you absolutely must choose a more secure password.
- port: If you want to run the web app on a different port than 5000.
- USERNAME: Your site username.
- PASSWORD: Your site password.
- CAPTCHA_PASSKEY: This is your passkey for the site.
- LOGINTXT: Your site username (again).
- APIKEY: Your TMDB API key to search for meta info.
- CLIENT_ID: Your IGDB client ID to search for video game info.
- CLIENT_SECRET: Your IGDB client secret to search for video game info
- ETORFPATH: directory where .torrent files from source torrent site are downloaded to. If you are always rehashing new .torrent files when you upload (e.g. EDIT_TORRENT is set to false), this directory is largely irrelevant and can be just set to `tmp/`.
    - If EDIT_TORRENT is set to true, it will edit the torrent instead of creating a new one, which saves time.
- ANNOUNCEURL: Your personal announce URL.
- WATCHFOLDER: Path to the directory where .torrent file for the uploaded torrent is placed for the client to import, e.g., /uploaders/torrentwatch.
- DATADIR: Path to where the downloaded torrent data is stored, e.g., /uploaders/complete. If you like to sort your downloads into tracker/category/etc specific directory (e.g. due to using an *arr stack), see [discrete directories](https://github.com/FinHv/dc_uploader/tree/main?tab=readme-ov-file#discrete-directories).

TMDB:
- See [here](https://developer.themoviedb.org/docs/getting-started#:~:text=To%20register%20for%20an%20API,to%20our%20terms%20of%20use.) on how to get an API key.

IGDB:
- See [here](https://api-docs.igdb.com/#getting-started) on how to generate a client ID and secret.

## Usage

### upload.sh usage for manual uploading via command line

#### upload.sh "/full/path/to/torrent/directory" [OPTION]

If one of the required settings in config.ini aren't set, script will fail and tell you what isn't set.

Let's say you have a directory you would like to upload:

/home/torrentdata/this.is.a.nice.movie-grp

With the actual movie inside being:

/home/torrentdata/this.is.a.nice.movie-grp/this.is.a.nice.movie-grp.mkv

The DATADIR would be /home/torrentdata

You would run: `upload.sh "/home/torrentdata/tracker1/this.is.a.nice.movie-grp"`

By default, the program assumes that the data to be uploaded already exists in DATADIR. See [discrete directories](https://github.com/FinHv/dc_uploader/tree/main?tab=readme-ov-file#discrete-directories) if you want to upload directories that are outside DATADIR.

#### Optional arguments:
-h, --help: Prints help. Called via `upload.sh -h` or `upload.sh --help`.

Following arguments are primarily used when user is using discrete directories.

-l, --ln: Hardlinks provided directory to DATADIR. If hardlink fails, fallback to symlink.

-c, --cp: Copies provided directory to DATADIR

-m, --mv: Moves provided directory to DATADIR

### Upload queue

If you have many directories you want to upload at once, but don't want to manually run `upload.sh` so many times, use `queue_upload.sh`. Create a file and write the same directory path you'd normally use for `upload.sh`, with each directory on a separate line. Then, just call `queue_upload.sh [QUEUE FILE] [OPTION]`. 

Use `queue_upload.sh` exactly the same as you would `upload.sh`, just that instead of providing a directory, you provide a queue file. The only difference is that link/copy/move argument is mandatory, not optional. Don't worry, if the directory already exists in DATADIR, the argument is ignore for that directory, as `upload.sh` is what is called under the hood and already has such checks.

The queue file provided to `queue_upload.sh` can either be a full absolute path or a relative path relative to `queue_upload.sh`. e.g. `queue_upload.sh some_queue_file.txt [OPTION]` if queue file is located in the same folder as `queue_upload.sh`.

If you want to see a log of what was successful during the last run, the success/failure of each queue item is logged in `files/queue_upload.log`. This log is overwritten each time `queue_upload.sh` is run.

Since this might take a while, it might be a good idea to execute this as a detached screen. See [FAQ](https://github.com/FinHv/dc_uploader/tree/main?tab=readme-ov-file#faq).

### Web app usage

#### Web app is currently unsupported and its functionality cannot be guaranteed.

Note: Web app usage is entirely optional. The upload tool can be used entirely from the command line.

start.sh will start the web server on the specified port specified during install. Starts in a detatched screen session named "dc-uploader". Web app is not necessary - you can never start it and use this tool entirely from the command line via `upload.sh`.

shutdown.sh shuts down the web server and ends the screen session.
    
1. Run `start.sh`. If one of the required settings in config.ini aren't set, it will fail to start and tell you what isn't set.
2. Navigate to `https://[hostname.domain]:5000`, assuming you used the default port. If you specified a different port during install, use that. If it's just running locally, `localhost:5000` will work as well - the app binds to `0.0.0.0`.
3. Login with username/password found in config.ini (default: admin/p@ssword).
4. Update the relevant settings as needed.

If using discrete directories, web app requires users to manually copy/hardlink/symlink/move to DATADIR. Hopefully future versions will automate the copy/hardlink/symlink process when using discrete directory.

You can connect to the screen session with `screen -r dc-uploader`. Detatch from the screen with `CTRL + A` then `D`.

## Discrete directories

If you just download all your torrents to one directory, just set DATADIR to that directory and ignore this section. For those who keep their torrent directories neat, read on.

Let's say you have sorted your downloaded torrents into neat little discrete directories, such as based on category, tracker, etc. You'll need to use one of the args (e.g. --ln) when calling `upload.sh`.

What this means is that you will need to create an upload specific directory to use as the "base" DATADIR. For example, you have your torrents sorted into two directories:

`/data/movie/someneatmovie/someneatmovie.mkv`

`/data/tv/someneatshow/someneatshows01e01.mkv,someneatshows01e02.mkv,etc`

DATADIR can't be both /data/movie *and* /data/tv at the same time, so what do you do? You create:

`/data/uploads`

Now, you set DATADIR to `/data/uploads`, and --ln/cp/mv become non-optional arguments. You *must* pick one. Then, if you run `upload.sh "/data/movie/someneatmovie" --ln/cp/mv`, it will use the selected option to re-create the directory in `/data/uploads`, resulting in a final directory of `/data/uploads/someneatmovie`. If you use --ln, this second copy of the directory takes no additional disk space. Also known as black magic.

The alternative is to update config.ini's DATADIR every time you want to upload from a different directory, but who wants to do that?

## Permissions

The install.sh script must be run as root, since it has to install prerequisites. The rest of the scripts can be run as a non-root user and without sudo, provided that the user running those scripts has read and write permissions to:

- Program's entire root directory (e.g. dc_uploader)
  - `chown -R [USER] dc_uploader` if you want to be sure, but usually not necessary if this is the same user that downloaded dc_uploader.
- DATADIR
- WATCHFOLDER

## Uninstall :(

To remove just the program, simply delete the program folder. The python virtual environment is inside this folder, so don't worry about removing it manually

To remove the dependencies installed via apt, run apt remove. Double check if there are things that you don't want to uninstall, especially fuse3. Packages possibly installed by this script: 
- build-essential mtn mediainfo fuse3 libfuse-dev screen software-properties-common autoconf gpg

To remove rar2fs:

`rm /usr/local/bin/rar2fs`

Any installed repo's besides the standard apt repo's, which should be just mtn, are stored in `/etc/apt/sources.list.d`, with their gpg keys stored in `/etc/apt/trusted.gpg.d`

## FAQ

#### Q: I don't want the script to take over my session, I have other stuff to do. What do I do?

A: Luckily, screen is installed by the script! You can simply execute the command as a detached screen session:

`screen -dmS [SCREEN_NAME] ./queue_upload.sh testqueue.txt --ln`

`screen -dmS [SCREEN_NAME] ./upload.sh /some/directory/ --ln`

etc.

Essentially, just add `screen -dmS [SCREEN_NAME]` to the start of your command and it'll execute that command in a detached screen. The screen will automatically terminate once the command finishes. You can omit the `S [SCREEN_NAME]` if you don't want to name your screen.

`screen -ls` to list all your screen sessions and that the screen you just created is actually running.  Do be careful to know that if there is an error in running the requested command, the detached screen immediately terminates, since the command exited, and it will NOT print any errors. When in doubt, run the screen command in attached mode first by omitting the `d` argument, then detaching with `CTRL + A` then `D`.

#### Q: After my torrent is uploaded, where does the actual torrent in the client expect the data to be?

A: This depends on how you set up the watch directory. By using a watch directory, the client adds the .torrent file to the client similar to a user adding a torrent, so it depends on how you've set your client up.

#### Q: I use discrete directories, but I want to add it to a download-then-uploadToDCC automation pathway, how do I do that?

A: Assuming you have created an upload directory as directed by the [discrete directories](https://github.com/FinHv/dc_uploader/tree/main?tab=readme-ov-file#discrete-directories) section, just pass the torrent content path to upload.sh with one of the relevant arguments. The script is the one that handles the linking/copying/moving.

**Example with rtorrent:**

```
schedule2 = watch_directory_source,5,5,load.start=/uploaders/sourcewatch/*.torrent
schedule2 = watch_directory,5,5,load.start=/uploaders/dcwatch/*.torrent
schedule2 = tied_directory,6,5,start_tied=
schedule2 = untied_directory,7,5,stop_untied=
schedule2 = untied_directory,8,5,remove_untied=

method.set_key = event.download.finished,upload_torrent,"execute=bash,/your/path/to/dc_uploader/upload.sh,$d.name="
```

Here, sourcewatch is ETORFPATH, dcwatch is WATCHFOLDER, DATADIR would be whatever folder rtorrent downloads torrents into, and $d.name= is the directory to be uploaded. This example does not use discrete folders, modify as needed for discrete folders.

**Example with qBittorrent:**

Navigate to "Downloads" in settings. Scroll to bottom, check "Run external program on torrent finished". Input:

`/path/to/dc_uploader/upload.sh %F [ARG as needed]`

`--mv` shouldn't be used since the torrent you just downloaded needs the data to stay in place.

If you already have some command executing, just chain the command:

`[some pre-existing command, e.g. cross-seed] && /path/to/dc_uploader/upload.sh %F [ARG as needed]`

`&&` will only execute the upload if the previous command is successful. If you want upload regardless of the success of the previous command, replace `&&` with `;`

#### Q: What happens if I pass the script a file instead of a directory?

A: A polar bear mauls you. Assuming you survive, the script will fail - this tool does not support file uploads. The tool does not create subdirectories within the .torrent file.

#### Q: What happens if I pass the script the path of something already in DATADIR?

A: Everything will proceed as normal. It'll just use the pre-existing directory rather than link/copy/move anything.

#### Q: When does the tool fallback to symlinking?

A: The primary limitation of hardlinking is that the source and intended destination must be on the same filesystem - e.g. it can't hardlink from one hard drive to another, from one hard drive to a SMB/NFS mount, etc. When the tool detects that a hardlink can't be done, it tries again using symlinks.

### Q: Can I use this tool on windows?

A: No you'll be missing a lot of the required tools. Instead, look into [WSL](https://learn.microsoft.com/en-us/windows/wsl/install). Once WSL is running, just mount your torrent parent folder to the WSL environment through /etc/fstab and you should be fine. Treat it like a second computer.