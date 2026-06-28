"""
Provenance Guard - Audit Log Service
services/audit_log.py: Reads and writes a persistent JSON audit log.

Log file location: logs/audit_log.json
The log is stored as a top-level JSON array where each element is one entry dict.
"""

import json
import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR  = "logs"
LOG_FILE = os.path.join(LOG_DIR, "audit_log.json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_log_file() -> None:
    """
    Create the log directory and file if they do not already exist.
    Initialises the file with an empty JSON array so it is always valid JSON.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def _read_log() -> list:
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        _write_log_to_disk([])
        return []


def _write_log_to_disk(entries: list) -> None:
    """Serialise *entries* back to disk with readable indentation."""
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_log(entry: dict) -> None:
    """
    Append *entry* to the audit log.

    Args:
        entry: A dict representing a single audit record.
               Typical keys: creator_id, text, classification, score, timestamp.
               The caller is responsible for including any fields it needs.
    """
    _ensure_log_file()

    entries = _read_log()
    entries.append(entry)
    _write_log_to_disk(entries)


def get_log() -> list:
    """
    Return every entry in the audit log as a Python list.

    Returns:
        A (possibly empty) list of dicts, one per logged submission.
    """
    _ensure_log_file()
    return _read_log()


def update_entry_by_content_id(content_id: str, updates: dict) -> bool:
    """
    Find the first entry whose ``content_id`` matches and apply *updates* to it
    in-place, then persist the full log back to disk.

    Args:
        content_id: The UUID string that identifies the target log entry.
        updates:    A dict of key/value pairs to merge into the matched entry.
                    Existing keys are overwritten; new keys are added.

    Returns:
        ``True``  if a matching entry was found and updated.
        ``False`` if no entry with the given ``content_id`` exists.
    """
    _ensure_log_file()
    entries = _read_log()

    for entry in entries:
        if entry.get("content_id") == content_id:
            entry.update(updates)
            _write_log_to_disk(entries)
            return True

    return False  # content_id not found — caller decides how to handle this