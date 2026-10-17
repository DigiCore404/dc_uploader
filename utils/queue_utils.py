import sqlite3
import time

import psutil

DB_PATH = 'data/upload_queue.db'

def init_db():
    """Initialize the SQLite database to store the queue."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pid INTEGER,
            directory_name TEXT,
            status TEXT,  -- queued, running, completed, failed
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_to_queue(pid, directory_name):
    """Add a task (process) to the upload queue."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO upload_queue (pid, directory_name, status, timestamp)
        VALUES (?, ?, 'running', ?)
    ''', (pid, directory_name, time.strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    
    # Debug: Print added tasks
    print(f"Task for directory '{directory_name}' (PID {pid}) added to queue as running.")

def get_running_tasks():
    """Get tasks that are in the 'running' state."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT pid, directory_name FROM upload_queue WHERE status = 'running'")
    tasks = c.fetchall()
    conn.close()
    
    # Debug: Print running tasks
    if tasks:
        print(f"Running tasks in the queue: {tasks}")
    else:
        print("No running tasks in the queue.")
    
    return tasks

def update_task_status(pid, status):
    """Update the status of a task in the queue."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE upload_queue SET status = ?, timestamp = ? WHERE pid = ?",
              (status, time.strftime('%Y-%m-%d %H:%M:%S'), pid))
    conn.commit()
    conn.close()

    # Debug: Print task status update
    print(f"Task for PID {pid} status updated to '{status}'.")

def process_exists(pid):
    """Check if a process with a given PID exists."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running()
    except psutil.NoSuchProcess:
        return False

def detect_running_processes():
    """Detect running 'backend.py' processes and add them to the queue."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        if 'python' in proc.info['name'] and 'backend.py' in proc.info['cmdline']:
            pid = proc.info['pid']
            # Extract directory name from command-line arguments
            if len(proc.info['cmdline']) > 1:
                directory_name = proc.info['cmdline'][1]
                if not task_in_queue(pid):
                    add_to_queue(pid, directory_name)

def task_in_queue(pid):
    """Check if a task with a given PID is already in the queue."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM upload_queue WHERE pid = ?", (pid,))
    count = c.fetchone()[0]
    conn.close()
    
    return count > 0

def cleanup_completed_tasks():
    """Check running tasks and update their status if they have completed."""
    running_tasks = get_running_tasks()
    
    for pid, directory_name in running_tasks:
        if not process_exists(pid):
            # Process has completed or terminated
            print(f"Process {pid} for '{directory_name}' has completed.")
            update_task_status(pid, 'completed')

def queue_manager():
    """Main function to manage the queue and detect processes."""
    init_db()
    
    while True:
        # Detect and add new running backend.py processes
        detect_running_processes()
        
        # Check running tasks and mark them as completed if they are no longer running
        cleanup_completed_tasks()
        
        time.sleep(5)  # Sleep before checking again to avoid busy-waiting

if __name__ == "__main__":
    # Start managing the queue
    queue_manager()
