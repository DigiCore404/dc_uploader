import sqlite3
from datetime import datetime
from pathlib import Path

UPLOADS_DB = 'data/uploads.db'
MAX_UPLOAD_HISTORY_ROWS = 5000

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
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_uploads_name ON uploads(name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_uploads_date ON uploads(date)')
    conn.commit()
    conn.close()




def initialize_upload_database():
    """Initialize the CLI upload record table; existing legacy databases are left untouched."""
    # Ensure data directory for databases exists
    Path("data").mkdir(parents=False, mode=0o775, exist_ok=True)
    create_uploads_table()
    print("Upload database ready.")

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

    cursor.execute(
        '''
        DELETE FROM uploads
        WHERE id NOT IN (
            SELECT id FROM uploads
            ORDER BY id DESC
            LIMIT ?
        )
        ''',
        (MAX_UPLOAD_HISTORY_ROWS,),
    )
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
        fields_to_update.append("nfo_content = ?")
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
