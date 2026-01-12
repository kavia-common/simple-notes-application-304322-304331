from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


def _parse_allowed_origins(raw: str) -> list[str]:
    """Parse ALLOWED_ORIGINS env var into a list."""
    if not raw:
        return ["*"]
    # Allow comma-separated origins.
    return [o.strip() for o in raw.split(",") if o.strip()]


def _get_db_path() -> str:
    """
    Resolve the SQLite DB path.

    Per `database/db_connection.txt`, the SQLite DB file path is:
      /home/kavia/workspace/code-generation/simple-notes-application-304322-304333/database/myapp.db

    This service will use that DB by default to ensure the backend and the database container
    are aligned. You may override this behavior by setting NOTES_DB_PATH.
    """
    default_path = (
        "/home/kavia/workspace/code-generation/"
        "simple-notes-application-304322-304333/database/myapp.db"
    )
    return os.getenv("NOTES_DB_PATH", default_path)


def _get_conn() -> sqlite3.Connection:
    """Create a sqlite connection with row_factory for dict-like access."""
    conn = sqlite3.connect(_get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """Initialize tables if not present."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


class Note(BaseModel):
    """A note resource."""

    id: int = Field(..., description="Unique note identifier.")
    title: str = Field(..., description="Note title.")
    content: str = Field(..., description="Note content/body.")
    created_at: datetime = Field(..., description="Creation timestamp (UTC).")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC).")


class NoteCreate(BaseModel):
    """Payload to create a note."""

    title: str = Field(..., min_length=1, max_length=200, description="Note title.")
    content: str = Field(..., description="Note content/body.")


class NoteUpdate(BaseModel):
    """Payload to update a note (partial)."""

    title: Optional[str] = Field(None, min_length=1, max_length=200, description="Updated note title.")
    content: Optional[str] = Field(None, description="Updated note content/body.")


def _row_to_note(row: sqlite3.Row) -> Note:
    """Convert sqlite row to Note model."""
    return Note(
        id=int(row["id"]),
        title=str(row["title"]),
        content=str(row["content"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


openapi_tags = [
    {"name": "System", "description": "Health and service metadata."},
    {"name": "Notes", "description": "CRUD operations for notes."},
]

app = FastAPI(
    title="Simple Notes API",
    description="A minimal Notes API supporting create/read/update/delete operations with SQLite storage.",
    version="0.1.0",
    openapi_tags=openapi_tags,
)

# Always allow the frontend dev server, per requirement.
# You can add more origins via ALLOWED_ORIGINS (comma-separated).
allowed_origins = sorted(
    {
        "http://localhost:3000",
        *_parse_allowed_origins(os.getenv("ALLOWED_ORIGINS", "")),
    }
)

allow_headers = (
    [h.strip() for h in os.getenv("ALLOWED_HEADERS", "*").split(",")]
    if os.getenv("ALLOWED_HEADERS")
    else ["*"]
)
allow_methods = (
    [m.strip() for m in os.getenv("ALLOWED_METHODS", "*").split(",")]
    if os.getenv("ALLOWED_METHODS")
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
    max_age=int(os.getenv("CORS_MAX_AGE", "3600")),
)


@app.on_event("startup")
def _on_startup() -> None:
    """Initialize the SQLite schema."""
    _init_db()


# PUBLIC_INTERFACE
@app.get(
    "/",
    tags=["System"],
    summary="Health check",
    description="Simple health check endpoint to verify the service is running.",
    operation_id="healthCheck",
)
def health_check() -> dict:
    """Return a basic health indicator."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/notes",
    response_model=List[Note],
    tags=["Notes"],
    summary="List notes",
    description="Return all notes ordered by updated_at descending.",
    operation_id="listNotes",
)
def list_notes() -> List[Note]:
    """List all notes."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC, id DESC")
        rows = cur.fetchall()
        return [_row_to_note(r) for r in rows]
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.get(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Get a note",
    description="Return a single note by id.",
    operation_id="getNote",
)
def get_note(note_id: int) -> Note:
    """Get a note by its id."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return _row_to_note(row)
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.post(
    "/notes",
    response_model=Note,
    status_code=201,
    tags=["Notes"],
    summary="Create a note",
    description="Create a new note and return it.",
    operation_id="createNote",
)
def create_note(payload: NoteCreate) -> Note:
    """Create a new note."""
    now = datetime.utcnow().replace(microsecond=0).isoformat()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (payload.title, payload.content, now, now),
        )
        conn.commit()
        note_id = int(cur.lastrowid)
        cur = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        return _row_to_note(row)
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.put(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Update a note",
    description="Update an existing note by id. Supports partial updates (title/content).",
    operation_id="updateNote",
)
def update_note(note_id: int, payload: NoteUpdate) -> Note:
    """Update an existing note."""
    if payload.title is None and payload.content is None:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    conn = _get_conn()
    try:
        cur = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Note not found")

        new_title = payload.title if payload.title is not None else row["title"]
        new_content = payload.content if payload.content is not None else row["content"]
        now = datetime.utcnow().replace(microsecond=0).isoformat()

        conn.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
            (new_title, new_content, now, note_id),
        )
        conn.commit()

        cur = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row2 = cur.fetchone()
        return _row_to_note(row2)
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.delete(
    "/notes/{note_id}",
    status_code=204,
    tags=["Notes"],
    summary="Delete a note",
    description="Delete a note by id.",
    operation_id="deleteNote",
)
def delete_note(note_id: int) -> Response:
    """Delete a note."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Note not found")

        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return Response(status_code=204)
    finally:
        conn.close()
