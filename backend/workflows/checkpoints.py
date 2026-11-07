"""Persistent SQLite checkpoints and process-safe per-run exclusion."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def checkpoint_store(path):
    connection = sqlite3.connect(path, check_same_thread=False, timeout=20)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        saver = SqliteSaver(connection)
        saver.setup()
        yield saver
    finally:
        connection.close()


@contextmanager
def run_lock(folder, run_id):
    # run_id comes from the repository, never from a supplied filesystem path.
    path = Path(folder) / (run_id + '.lock')
    handle = open(path, 'a+b')
    if path.stat().st_size == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError('This run already has an active worker.') from error
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('This run already has an active worker.') from error
        acquired = True
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
