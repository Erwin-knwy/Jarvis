"""Notes and long-term memory."""
from __future__ import annotations

from anthropic import beta_tool

from .. import db

RISK = {"delete_note": "confirm", "forget": "confirm"}


@beta_tool(eager_input_streaming=True)
def add_note(text: str) -> str:
    """Save a note (shopping items, ideas, things to look up later).

    Args:
        text: The note content.
    """
    with db.tx() as c:
        cur = c.execute("INSERT INTO notes(text) VALUES (?)", (text.strip(),))
    return f"Saved note #{cur.lastrowid}."


@beta_tool(eager_input_streaming=True)
def list_notes(limit: int = 20, search: str = "") -> str:
    """List the user's notes, newest first, optionally filtered by a search word.

    Args:
        limit: Maximum number of notes to return.
        search: Optional word to filter notes by.
    """
    if search:
        rows = db.query(
            "SELECT * FROM notes WHERE text LIKE ? ORDER BY id DESC LIMIT ?", (f"%{search}%", limit)
        )
    else:
        rows = db.query("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))
    if not rows:
        return "No notes found."
    return "\n".join(f"#{r['id']} ({r['created_at'][:16]}): {r['text']}" for r in rows)


@beta_tool(eager_input_streaming=True)
def delete_note(note_id: int, confirmed: bool = False) -> str:
    """Delete a note by ID. Ask the user to confirm first, then call again with confirmed=true.

    Args:
        note_id: The note's ID.
        confirmed: True only after the user explicitly confirmed the deletion.
    """
    rows = db.query("SELECT text FROM notes WHERE id=?", (note_id,))
    if not rows:
        return f"No note #{note_id}."
    if not confirmed:
        return f"CONFIRMATION NEEDED: ask the user whether to delete note #{note_id} ('{rows[0]['text']}')."
    with db.tx() as c:
        c.execute("DELETE FROM notes WHERE id=?", (note_id,))
    return f"Deleted note #{note_id}."


@beta_tool(eager_input_streaming=True)
def remember(fact: str) -> str:
    """Store a long-term fact about the user or their world (preferences, people, plans,
    where things are). Use when the user says "remember ..." or shares a lasting personal detail.

    Args:
        fact: The fact as a full sentence, e.g. "The user's car is a blue 2019 Honda Civic."
    """
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO memories(fact, created_at) VALUES (?, datetime('now','localtime'))", (fact,)
        )
    return f"Remembered (memory #{cur.lastrowid})."


@beta_tool(eager_input_streaming=True)
def recall(query: str = "") -> str:
    """Search long-term memory for facts about the user. Use before answering personal
    questions ("what's my wife's name?", "where did I park?"). Empty query lists recent memories.

    Args:
        query: Keywords to search for.
    """
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in query).split() if len(w) > 1]
    if words:
        fts = " OR ".join(f'"{w}"' for w in words)
        rows = db.query(
            "SELECT rowid, fact, created_at FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT 15",
            (fts,),
        )
    else:
        rows = db.query("SELECT rowid, fact, created_at FROM memories ORDER BY rowid DESC LIMIT 15")
    if not rows:
        return "Nothing relevant in memory."
    return "\n".join(f"#{r['rowid']} ({r['created_at'][:10]}): {r['fact']}" for r in rows)


@beta_tool(eager_input_streaming=True)
def forget(memory_id: int, confirmed: bool = False) -> str:
    """Delete a long-term memory by ID. Ask the user to confirm first.

    Args:
        memory_id: The memory's ID (from recall).
        confirmed: True only after the user explicitly confirmed.
    """
    rows = db.query("SELECT fact FROM memories WHERE rowid=?", (memory_id,))
    if not rows:
        return f"No memory #{memory_id}."
    if not confirmed:
        return f"CONFIRMATION NEEDED: ask the user whether to forget '{rows[0]['fact']}'."
    with db.tx() as c:
        c.execute("DELETE FROM memories WHERE rowid=?", (memory_id,))
    return f"Forgot memory #{memory_id}."


TOOLS = [add_note, list_notes, delete_note, remember, recall, forget]
