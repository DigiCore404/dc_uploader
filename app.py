import configparser
import json
import logging
import os
import platform
import shlex
import sqlite3
import subprocess
import threading
import time
from collections import OrderedDict
from datetime import datetime
from functools import wraps
from operator import itemgetter

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from setuptools.errors import PlatformError
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

DATABASE = 'data/uploads.db'
DIRDATABASE = 'data/directories.db'
TERMDATABASE = 'data/terminal_output.db'

# Set the custom location for __pycache__
os.environ['PYTHONPYCACHEPREFIX'] = 'tmp/'

# Initialize the Flask app
app = Flask(__name__)
app.secret_key = 'digitalcoreclubdoeswhat'

# Initialize logging to output to the console. Will change log level later to user selected
logging.basicConfig(
    level=logging.DEBUG,  # Set logging level to DEBUG to capture all messages
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]  # Output logs to console (stdout)
)

# Custom ConfigParser to preserve case sensitivity
class CaseConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr):
        return optionstr  # Override to preserve case

# Load the config file
config = CaseConfigParser()
read_config = config.read('config.ini')
if not read_config:
    logging.error('Configuration file config.ini not found.')
    exit(1)

# Set log level
try:
    log_level = config['LOG'].get('level', 'DEBUG').upper()
except KeyError:
    logging.error('Log setting not found in config.ini, defaulting to DEBUG. You should not have deleted that line, '
                  'tsk tsk')
    log_level = 'DEBUG'

if log_level != 'DEBUG':
    if log_level == 'INFO':
        logging.getLogger().setLevel(logging.INFO)
    elif log_level == 'WARNING':
        logging.getLogger().setLevel(logging.WARNING)
    elif log_level == 'ERROR':
        logging.getLogger().setLevel(logging.ERROR)
    elif log_level == 'CRITICAL':
        logging.getLogger().setLevel(logging.CRITICAL)
    else:
        logging.error(f'Invalid log level {log_level} in config.ini, defaulting to DEBUG.')
        logging.getLogger().setLevel(logging.DEBUG)

# Read authentication details from config
auth_user = config['AUTH'].get('user', 'admin')
auth_password = config['AUTH'].get('password', 'password')
app_port = int(config['AUTH'].get('port', '5000'))
hostname = config['AUTH'].get('hostname', 'localhost')

if not auth_user:
    logging.error('No user found in config.ini, please set a user in the AUTH section.')
    exit(1)
if not auth_password:
    logging.error('No password found in config.ini, please set a password in the AUTH section.')
    exit(1)
if not app_port:
    logging.error('No port found in config.ini, please set a port in the AUTH section.')
    exit(1)
if not hostname:
    hostname = 'localhost'  # Default to localhost if not set

# Load the path to the JSON file from config.ini
json_file_path = config['Paths'].get('FILTERS', 'filters.json')
if not os.path.exists(json_file_path):
    logging.error(f"Error: filters.json not found at {json_file_path}, this setting should not have been changed.")
    exit(1)

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Initialize database only if it doesn't exist
def init_db():
    logging.debug('Checking if the database needs initialization...')
    if not os.path.exists(DIRDATABASE):
        logging.info(f'{DIRDATABASE} does not exist. Initializing the database.')
        conn = sqlite3.connect(DIRDATABASE)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS directories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                status TEXT,
                creation_date TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logging.debug('Database initialized.')
        # If DB didn't exist, load all directories
        return True
    else:
        logging.info('Database already exists. No need to reload all directories.')
        return False

#################################### CLEANUP FUNCTION #####################################################

# Directory cleanup function to compare database with actual root directory
def cleanup_orphaned_directories():
    while True:
        data_dir = config['Paths'].get('DATADIR', '').strip()
        if not data_dir:
            logging.error('DATADIR is not set in the configuration file.')
            return
        logging.info("Starting cleanup of orphaned directories...")
        # Connect to the SQLite database
        conn = sqlite3.connect(DIRDATABASE)
        c = conn.cursor()

        # Get all directory names from the database
        c.execute('SELECT name FROM directories')
        db_directories = [row[0] for row in c.fetchall()]

        # Get all actual directories from the root directory
        actual_directories = [d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))]

        # Compare the two lists and find directories in the database that don't exist in the root folder
        orphaned_directories = set(db_directories) - set(actual_directories)
        if len(orphaned_directories) == 0:
            logging.info(f"No orphaned directories found.")
        else:
            # If orphaned directories are found, delete them from the database
            for directory in orphaned_directories:
                logging.info(f"Removing orphaned directory: {directory} from database")
                c.execute('DELETE FROM directories WHERE name = ?', (directory,))

                # Commit changes and close the database connection
                conn.commit()

        conn.close()
        logging.info("Cleanup of orphaned directories completed.")

        # Sleep for 5 minutes before running the check again
        time.sleep(300)

# Function to start the cleanup daemon
def initiate_cleanup_daemon():
    cleanup_thread = threading.Thread(target=cleanup_orphaned_directories, daemon=True)
    cleanup_thread.start()

#################################### LOGIN/LOGOUT #####################################################

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == auth_user and password == auth_password:
            session['username'] = username
            return redirect(url_for('home'))
        flash('Invalid username or password')
    return render_template('login.html')

# Logout route
@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))


#################################### HOME PAGE #####################################################

# Initialize directory data (check for new/missed directories and update their status)
def initialize_directory_data():
    data_dir = config['Paths'].get('DATADIR', '').strip()
    if not data_dir:
        logging.error('DATADIR is not set in the configuration file.')
        exit(1)

    # Load all directories and check status (this runs once on app startup)
    logging.debug(f'Loading all directories and checking status from: {data_dir}')
    load_directories_into_db(data_dir)
    return True

# Load only new or modified directory data into the SQLite database
def load_directories_into_db(data_dir=None, single_directory=None):
    if single_directory:
        logging.debug(f'Loading single directory: {single_directory}')
    elif data_dir:
        logging.debug(f'Starting to load directories from: {data_dir}')
    else:
        logging.error('Neither datadir nor single_directory were provided.')
        return

    # If loading a single directory, skip the datadir check
    if single_directory:
        directories_to_load = [single_directory]
    else:
        if not os.path.exists(data_dir):
            logging.error(f'Data directory does not exist: {data_dir}')
            return
        directories_to_load = os.scandir(data_dir)

    conn = sqlite3.connect(DIRDATABASE)
    c = conn.cursor()

    directory_count = 0  # To count how many directories are being loaded

    for entry in directories_to_load:
        if isinstance(entry, str):
            dir_name = os.path.basename(entry)
            dir_path = entry
        else:
            dir_name = entry.name
            dir_path = entry.path

        # Exclude status directories and directories that start with a dot
        if dir_name.startswith('.') or dir_name == 'COMPLETE':
            continue

        if os.path.isdir(dir_path):
            status = 'none'
            
            # Check the status based on hidden files
            if os.path.exists(os.path.join(dir_path, '.dupe')):
                status = 'dupe'
            elif os.path.exists(os.path.join(dir_path, '.uploading')):
                status = 'uploading'
            elif os.path.exists(os.path.join(dir_path, '.uploaded')):
                status = 'uploaded'
            elif os.path.exists(os.path.join(dir_path, '.failed')):
                status = 'failed'
            # Get the creation date of the directory
            creation_time = os.path.getctime(dir_path)
            creation_date = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d %H:%M:%S')

            # Check if the directory already exists in the database
            c.execute('SELECT status FROM directories WHERE name = ?', (dir_name,))
            existing_status = c.fetchone()

            if existing_status:
                # Update the status only if it has changed
                if existing_status[0] != status:
                    logging.debug(f'Updating directory: {dir_name}, Old Status: {existing_status[0]}, New Status: {status}')
                    c.execute('''
                        UPDATE directories SET status = ?, creation_date = ? WHERE name = ?
                    ''', (status, creation_date, dir_name))
            else:
                # Insert the new directory if it doesn't exist
                logging.debug(f'Inserting new directory: {dir_name}, Status: {status}, Creation Date: {creation_date}')
                c.execute('''
                    INSERT INTO directories (name, status, creation_date)
                    VALUES (?, ?, ?)
                ''', (dir_name, status, creation_date))

            directory_count += 1

    conn.commit()
    conn.close()

    logging.info(f'{directory_count} directories processed in the database.')


# Dictionary to track active subdirectory observers
active_observers = {}

class SubdirectoryEventHandler(FileSystemEventHandler):
    def __init__(self, local_observer, dir_name):
        self.observer = local_observer
        self.dir_name = dir_name

    def on_created(self, event):
        # Check if a hidden file related to status is created
        if event.src_path.endswith('.dupe'):
            logging.debug(f'.dupe file detected in {self.dir_name}')
            update_directory_status(self.dir_name, 'dupe')
        elif event.src_path.endswith('.uploading'):
            logging.debug(f'.uploading file detected in {self.dir_name}')
            update_directory_status(self.dir_name, 'uploading')
        elif event.src_path.endswith('.failed'):
            logging.debug(f'.failed file detected in {self.dir_name}')
            update_directory_status(self.dir_name, 'failed')
            # Stop monitoring this directory once the upload fails
            self.stop_observer()
        elif event.src_path.endswith('.uploaded'):
            logging.debug(f'.uploaded file detected in {self.dir_name}')
            update_directory_status(self.dir_name, 'uploaded')
            # Stop monitoring this directory once the upload is complete
            self.stop_observer()

    def stop_observer(self):
        logging.debug(f"Stopping observer for {self.dir_name}")
        if self.observer:
            self.observer.stop()
            # Use a separate thread or defer the join() call
            threading.Thread(target=self.join_observer).start()

    def join_observer(self):
        if self.observer:
            self.observer.join()
            logging.debug(f"Observer for {self.dir_name} stopped successfully.")


# Event handler for root directory
class RootDirectoryEventHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            dir_name = os.path.basename(event.src_path)
            logging.debug(f'New directory created: {dir_name}')
            load_directories_into_db(single_directory=event.src_path)
            logging.debug(f'Directory {dir_name} created and added to the database.')
            
            # Start monitoring the new subdirectory for hidden files
            self.start_subdirectory_watcher(event.src_path)

    def on_deleted(self, event):
        if event.is_directory:
            dir_name = os.path.basename(event.src_path)
            logging.debug(f'Directory deleted: {dir_name}')
            conn = sqlite3.connect(DIRDATABASE)
            c = conn.cursor()
            c.execute('DELETE FROM directories WHERE name = ?', (dir_name,))
            conn.commit()
            conn.close()
            logging.debug(f'Directory {dir_name} deleted from the database.')

            # Stop the corresponding observer if it exists
            self.stop_subdirectory_watcher(dir_name)

    def start_subdirectory_watcher(self, subdirectory_path):
        # Check if the subdirectory already has an observer
        if subdirectory_path in active_observers:
            logging.debug(f'Already monitoring {subdirectory_path}')
            return
        
        # Create a new observer for the subdirectory
        local_observer = Observer()
        event_handler = SubdirectoryEventHandler(local_observer, os.path.basename(subdirectory_path))
        local_observer.schedule(event_handler, path=subdirectory_path, recursive=False)
        local_observer.start()
        
        active_observers[subdirectory_path] = local_observer
        logging.info(f'Started monitoring {subdirectory_path}')

    def stop_subdirectory_watcher(self, subdirectory_name):
        # Stop the observer for the subdirectory if it exists
        for path, local_observer in list(active_observers.items()):
            if subdirectory_name in path:
                local_observer.stop()
                local_observer.join()
                del active_observers[path]
                logging.debug(f'Stopped monitoring {subdirectory_name}')


# Start the root directory watcher
def start_directory_watcher(data_dir):
    logging.debug(f'Starting root directory watcher on: {data_dir}')
    event_handler = RootDirectoryEventHandler()
    local_observer = Observer()
    local_observer.schedule(event_handler, path=data_dir, recursive=False)
    local_observer.start()
    logging.info(f'Root directory watcher started for: {data_dir}')
    return local_observer

# Function to update the directory status in the database
def update_directory_status(dir_name, new_status):
    logging.info(f"Updating status for {dir_name} to {new_status}")

    # Connect to the SQLite database
    conn = sqlite3.connect(DIRDATABASE)
    c = conn.cursor()

    # Update the status only if it has changed
    c.execute('SELECT status FROM directories WHERE name = ?', (dir_name,))
    existing_status = c.fetchone()

    if existing_status:
        if existing_status[0] != new_status:
            logging.debug(f"Updating directory: {dir_name}, Old Status: {existing_status[0]}, New Status: {new_status}")
            c.execute('''
                UPDATE directories SET status = ? WHERE name = ?
            ''', (new_status, dir_name))
    else:
        logging.warning(f"Directory {dir_name} not found in the database.")

    conn.commit()
    conn.close()
    logging.debug(f"Status for {dir_name} updated to {new_status}")

# Route to get directory data from the SQLite database
@app.route('/get_directories_json')
@login_required
def get_directories_json():
    logging.debug('Fetching directories from the database...')
    conn = sqlite3.connect(DIRDATABASE)
    c = conn.cursor()

    # Ensure the correct order: name, status, creation_date
    c.execute('SELECT name, status, creation_date FROM directories')

    # Use OrderedDict to explicitly preserve the order of keys in the JSON response
    directories = [
        OrderedDict([
            ('name', row[0]),
            ('status', row[1]),
            ('date', row[2])
        ]) 
        for row in c.fetchall()
    ]

    conn.close()
    #logging.debug(f'Returning {len(directories)} directories as JSON.')

    # Print the response before returning it
    json_response = jsonify({'data': directories})
    #logging.debug(f"JSON response: {json.dumps({'data': directories}, indent=4)}")  # Pretty-print the JSON
    
    # Return the JSON response with the explicit order
    return json_response

# Home route to render the index.html template
@app.route('/')
@login_required
def home():
    logging.debug('Accessing the home page')
    return render_template('index.html')

# Upload route
@app.route('/upload', methods=['POST'])
@login_required
def upload():
    data = request.get_json()
    directory_name = data.get('directory_name')

    if not directory_name:
        return "Directory name not provided", 400  # Return 400 if directory_name is missing

    # Start upload in a subprocess to avoid blocking
    subprocess.Popen(['/venv/bin/python3', 'backend.py', f'{shlex.quote(str(directory_name))}'])

    return "Upload started", 200

# Reset status route
@app.route('/reset_status', methods=['POST'])
@login_required
def reset_status():
    directory_name = request.form['directory_name']
    logging.info(f'Resetting status for directory: {directory_name}')

    data_dir = config['Paths'].get('DATADIR', '').strip()

    if not data_dir:
        logging.warning('Data Directory is not set in the configuration file.')
        flash('Data Directory is not set in the configuration file.', 'warning')
        return redirect(url_for('home'))

    dir_path = os.path.join(data_dir, directory_name)

    # Remove status directories
    for status in ['uploading', 'uploaded', 'dupe', 'failed']:
        status_dir = os.path.join(dir_path, f'.{status}')
        if os.path.isdir(status_dir):
            logging.debug(f'Removing status directory: {status_dir}')
            os.rmdir(status_dir)


    return redirect(url_for('home'))

# Set as uploaded route
@app.route('/set_uploaded', methods=['POST'])
@login_required
def set_uploaded():
    directory_name = request.form['directory_name']
    logging.info(f'Setting directory as uploaded: {directory_name}')

    data_dir = config['Paths'].get('DATADIR', '').strip()

    if not data_dir:
        logging.warning('Data Directory is not set in the configuration file.')
        flash('Data Directory is not set in the configuration file.', 'warning')
        return redirect(url_for('home'))

    dir_path = os.path.join(data_dir, directory_name)

    # Remove any existing status folders
    for status in ['uploading', 'uploaded', 'dupe', 'failed']:
        remove_status_file(dir_path, status)

    # Create .uploaded status folder
    create_status_file(dir_path, 'uploaded')

    return redirect(url_for('home'))

# Helper functions to remove and create status files
def remove_status_file(dir_path, status):
    status_path = os.path.join(dir_path, f'.{status}')
    if os.path.exists(status_path):
        logging.debug(f'Removing status file: {status_path}')
        os.rmdir(status_path)

def create_status_file(dir_path, status):
    status_path = os.path.join(dir_path, f'.{status}')
    logging.debug(f'Creating status file: {status_path}')
    os.makedirs(status_path, exist_ok=True)

#################################### SETTINGS PAGE #####################################################

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        for section in config.sections():
            for key in request.form:
                if key.startswith(f'{section}_'):
                    value = request.form[key]

                    # Only for the 'UploadForm' section: Handle 1/0 as True/False
                    if section == 'UploadForm' and value in ['1', '0']:
                        value = '1' if value == '1' else '0'  # Keep '1'/'0' as strings

                    # Convert 'true'/'false' to boolean for non-UploadForm sections
                    elif value.lower() in ['true', 'false']:
                        value = 'true' if value.lower() == 'true' else 'false'  # Keep as 'true'/'false' strings

                    # Update the config value (strip the section part from the key)
                    config[section][key[len(section) + 1:]] = value

        # Write the updated config to the file
        with open('config.ini', 'w') as configfile:
            config.write(configfile)

        flash('Settings updated successfully!')
        return redirect(url_for('settings'))

    # Load current settings from config.ini into the template
    settings_template = {
        'Header': dict(config['Header']),
        'Website': dict(config['Website']),
        'UploadForm': dict(config['UploadForm']),
        'TMDB': dict(config['TMDB']),
        'IGBD': dict(config['IGDB']),
        'ImageHost': dict(config['ImageHost']),
        'Torrent': dict(config['Torrent']),
        'Paths': dict(config['Paths']),
        'Settings': dict(config['Settings']),
        'MediaTools': dict(config['MediaTools'])
    }
    return render_template('settings.html', settings=settings_template)


#################################### CATEGORY PAGE #####################################################


@app.route('/edit_categories', methods=['GET', 'POST'])
@login_required
def edit_categories():
    try:
        with open(json_file_path) as f:
            categories = json.load(f)
    except Exception as e:
        logging.error(f"Error loading categories: {e}")
        categories = {}

    if request.method == 'POST':
        form_data = request.form
        #logging.info(f"Form data: {form_data}")

        try:
            updated_categories = {}
            for category_name, category_data in categories.items():
                updated_categories[category_name] = {
                    'patterns': {
                        'initial': [pattern.strip() for pattern in form_data.get(f"{category_name}_initial_patterns", "").split(',')],  # Strip spaces
                        'exclude_patterns': [pattern.strip() for pattern in form_data.get(f"{category_name}_exclude_patterns", "").split(',')]  # Strip spaces
                    },
                    'categories': []
                }

                subcat_index = 1
                for subcategory in category_data['categories']:
                    subcategory_data = {
                        'name': form_data.get(f"{category_name}_subcat_name_{subcat_index}", subcategory['name']),
                        'cat_id': form_data.get(f"{category_name}_subcat_id_{subcat_index}", subcategory['cat_id']),
                        'patterns': [pattern.strip() for pattern in form_data.get(f"{category_name}_patterns_{subcat_index}", '').split(',')],  # Strip spaces
                        'exclude_patterns': [pattern.strip() for pattern in form_data.get(f"{category_name}_exclude_patterns_{subcat_index}", '').split(',')]  # Strip spaces
                    }
                    updated_categories[category_name]['categories'].append(subcategory_data)
                    subcat_index += 1

            with open(json_file_path, 'w') as f:
                json.dump(updated_categories, f, indent=4)

            # Flash success message
            flash('Categories updated successfully!', 'success')
            return redirect(url_for('edit_categories'))

        except KeyError as e:
            logging.error(f"KeyError updating categories: {e}")
            flash(f"Error: Missing form data for key {e}", 'danger')
            return redirect(url_for('edit_categories'))

    return render_template('edit_categories.html', categories=categories)



# Route to provide directory status updates via AJAX
@app.route('/get_status_updates', methods=['GET'])
@login_required
def get_status_updates():
    data_dir = config['Paths'].get('DATADIR', '').strip()
    directories = []

    # Loop through directories in the data directory and gather statuses
    for dir_name in os.listdir(data_dir):
        dir_path = os.path.join(data_dir, dir_name)
        if os.path.isdir(dir_path):
            status = 'none'
            if os.path.exists(os.path.join(dir_path, '.dupe')):
                status = 'dupe'
            elif os.path.exists(os.path.join(dir_path, '.uploading')):
                status = 'uploading'
            elif os.path.exists(os.path.join(dir_path, '.uploaded')):
                status = 'uploaded'
            creation_time = os.path.getctime(dir_path)
            creation_date = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d %H:%M:%S')
            directories.append({'name': dir_name, 'status': status, 'date': creation_date})

    # Sort directories by date in descending order
    directories = sorted(directories, key=itemgetter('date'), reverse=True)

    return jsonify(directories)

#################################### MONITOR PAGE #####################################################

# Monitor page route
@app.route('/monitor')
@login_required
def monitor():
    return render_template('monitor.html')

@app.route('/get_terminal_output', methods=['GET'])
@login_required
def get_terminal_output():
    last_id = request.args.get('last_id', 0, type=int)  # Get the last shown log's id from the frontend
    new_lines = []
    max_log_lines = 1000  # Limit to 1000 log lines

    if os.path.exists(TERMDATABASE):
        try:
            # Connect to SQLite database
            conn = sqlite3.connect(TERMDATABASE)
            c = conn.cursor()

            # Query new lines with id greater than last_id
            c.execute('''
                SELECT id, log_line FROM terminal_logs
                WHERE id > ? 
                ORDER BY id ASC
            ''', (last_id,))
            rows = c.fetchall()

            # Format the new lines
            new_lines = [{'id': row[0], 'line': row[1]} for row in rows]

            # Keep the last 1000 lines, delete older lines if necessary
            c.execute('SELECT COUNT(*) FROM terminal_logs')
            total_lines = c.fetchone()[0]

            if total_lines > max_log_lines:
                # Calculate the number of excess lines
                excess_lines = total_lines - max_log_lines
                
                # Delete the oldest lines to maintain the max_log_lines limit
                c.execute('''
                    DELETE FROM terminal_logs
                    WHERE id IN (
                        SELECT id FROM terminal_logs
                        ORDER BY id ASC
                        LIMIT ?
                    )
                ''', (excess_lines,))
                logging.info(f"Deleted {excess_lines} old log entries to maintain the log size.")

            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            logging.error(f"SQLite error: {e}")
            return jsonify({'data': []})
    else:
        logging.error(f"Database not found: {TERMDATABASE}")
        return jsonify({'data': []})

    return jsonify({'data': new_lines})

#################################### LOG PAGE #####################################################

# Route to render log page
@app.route('/logs')
@login_required
def logs():
    return render_template('log.html')

# Route to provide log data via AJAX
@app.route('/get_logs', methods=['GET'])
@login_required
def get_logs():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT name, category, date, status, size, imdb_url, screenshot_url, image_url FROM uploads')
    existing_logs = cursor.fetchall()
    
    conn.close()

    # Format the logs for DataTables
    log_data = [
        {
            'name': row[0],
            'category': row[1],
            'date': row[2],
            'status': row[3],
            'size': row[4],
            'imdb_url': row[5],
            'screenshot_url': row[6],
            'image_url': row[7]
        }
        for row in existing_logs
    ]

    return jsonify({'data': log_data})

if __name__ == '__main__':
    if platform.system() != 'Linux':
        raise PlatformError("This tool is designed only for Linux")
    logging.info('Starting application...')

    # Initialize the SQLite database
    init_db()

    # Load directory data into the database on startup
    logging.info('Initializing directory data...')
    initialize_directory_data()

    # Start the directory watcher
    datadir = config['Paths'].get('DATADIR', '').strip()
    logging.info(f'Starting directory watcher for {datadir}')
    observer = start_directory_watcher(datadir)

    # Start the cleanup task to remove orphaned directories from the database
    initiate_cleanup_daemon()

    # Path to your SSL certificate and key
    ssl_cert_path = 'certificates/cert.pem'
    ssl_key_path = 'certificates/key.pem'

    try:
        # Run Flask app with SSL support if certs exist
        if os.path.isfile(ssl_cert_path) and os.path.isfile(ssl_key_path) and hostname != 'localhost':
            # SSL certs exist, try to run with SSL if they can be read
            logging.info('Starting Flask app with SSL support...')
            if not os.access(ssl_cert_path, os.R_OK):
                logging.error(f"SSL certificate not readable: {ssl_cert_path}")
                exit(1)
            if not os.access(ssl_key_path, os.R_OK):
                logging.error(f"SSL key not readable: {ssl_key_path}")
                exit(1)
            app.run(host='0.0.0.0', port=app_port, ssl_context=(ssl_cert_path, ssl_key_path), SERVER_NAME=hostname)
        else:
            logging.info('No certificates found, starting Flask app without SSL support...')
            app.run(host='0.0.0.0', port=app_port)
    except KeyboardInterrupt:
        logging.error('Server interrupted by user.')
    except Exception as e:
        logging.error(f'Error starting Flask app: {e}')
    finally:
        if observer and datadir:
            logging.info(f'Stopping directory watcher for {datadir}')
            observer.stop()
            observer.join()
            logging.info('Directory watcher stopped.')
