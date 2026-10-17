import sqlite3
import sys
from datetime import datetime
from pathlib import Path

UPLOADS_DB = 'data/uploads.db'
TERMINAL_OUTPUT_DB = 'data/terminal_output.db'
DIRECTORIES_DB = 'data/directories.db'

def create_uploads_table():
    """Create the uploads table in SQLite if it doesn't exist."""
    conn = sqlite3.connect(UPLOADS_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL,
            size REAL,  -- Size in MB
            imdb_url TEXT,
            mediainfo TEXT,
            nfo_content TEXT,
            screenshot_url TEXT,
            image_url TEXT
        )
    ''')
    conn.commit()
    conn.close()

def create_terminal_output_table():
    """Create the terminal_output table in SQLite if it doesn't exist."""
    conn = sqlite3.connect(TERMINAL_OUTPUT_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS terminal_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            source TEXT,  -- Optional, can be used to differentiate uploaders
            log_line TEXT
        )
    ''')
    conn.commit()
    conn.close()

def create_directories_table():
    """Create the directories table in SQLite if it doesn't exist."""
    conn = sqlite3.connect(DIRECTORIES_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS directories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            status TEXT,
            creation_date TEXT
        )
    ''')
    conn.commit()
    conn.close()


def initialize_all_databases():
    """Initialize all required databases."""
    # Ensure data directory for databases exists
    Path("data").mkdir(parents=False, mode=0o775, exist_ok=True)
    create_uploads_table()
    create_terminal_output_table()
    create_directories_table()
    print("All databases initialized successfully.")

def insert_upload(name, category=None, status=None, size=None, imdb_url=None, mediainfo=None, nfo_content=None, screenshot_url=None, image_url=None):
    """Insert a new upload record into the SQLite database with only the fields provided."""
    conn = sqlite3.connect(UPLOADS_DB)
    cursor = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        INSERT INTO uploads (name, category, date, status, size, imdb_url, mediainfo, nfo_content, screenshot_url, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        name, 
        category if category else "Unknown",  # Default to "Unknown" if category not provided
        date, 
        status if status else "pending",  # Default to "pending" if status not provided
        size, 
        imdb_url, 
        mediainfo, 
        nfo_content, 
        screenshot_url, 
        image_url
    ))

    conn.commit()
    conn.close()

def update_upload_status(name, new_status=None, category=None, size=None, imdb_url=None, mediainfo=None, nfo=None,
                         screenshot_url=None, image_url=None):
    """Update the status and other details of an existing upload."""
    conn = sqlite3.connect(UPLOADS_DB)
    cursor = conn.cursor()

    # Build the SQL query dynamically based on provided fields
    fields_to_update = []
    values = []

    if new_status is not None:
        fields_to_update.append("status = ?")
        values.append(new_status)
    if category is not None:
        fields_to_update.append("category = ?")
        values.append(category)
    if size is not None:
        fields_to_update.append("size = ?")
        values.append(size)
    if imdb_url is not None:
        fields_to_update.append("imdb_url = ?")
        values.append(imdb_url)
    if mediainfo is not None:
        fields_to_update.append("mediainfo = ?")
        values.append(mediainfo)
    if nfo is not None:
        fields_to_update.append("nfo = ?")
        values.append(nfo)
    if screenshot_url is not None:
        fields_to_update.append("screenshot_url = ?")
        values.append(screenshot_url)
    if image_url is not None:
        fields_to_update.append("image_url = ?")
        values.append(image_url)

    # Ensure we only run an update if there are fields to update
    if fields_to_update:
        values.append(name)
        sql_query = f"UPDATE uploads SET {', '.join(fields_to_update)} WHERE name = ?"
        cursor.execute(sql_query, values)

    conn.commit()
    conn.close()


def fetch_all_uploads():
    """Fetch all uploads from the database."""
    conn = sqlite3.connect(UPLOADS_DB)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM uploads ORDER BY date DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows


def main():
    if len(sys.argv) < 2:
        print("Usage: python database_utils.py <function_name>")
        return
    
    function_name = sys.argv[1]
    
    if function_name == 'initialize_all_databases':
        initialize_all_databases()
    elif function_name == 'create_uploads_table':
        create_uploads_table()
    elif function_name == 'create_terminal_output_table':
        create_terminal_output_table()
    elif function_name == 'create_directories_table':
        create_directories_table()
    else:
        print(f"Unknown function: {function_name}")

if __name__ == '__main__':
    main()


