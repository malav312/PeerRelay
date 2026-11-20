"""Durable requester questions; caller supplies task/role authorization and transaction."""

import json
import uuid

from .schemas import payload_hash


class Questions:
    def __init__(self, db):
        self.db = db
        db.executescript("""
        CREATE TABLE IF NOT EXISTS questions(
          id TEXT PRIMARY KEY,task TEXT,operation TEXT,hash TEXT,message TEXT,
          choices TEXT,state TEXT,action TEXT DEFAULT '',answer TEXT DEFAULT '',
          UNIQUE(task,operation));
        UPDATE questions SET state='cancelled',action='cancel'
          WHERE state='pending';
        """)

    def pending(self, task):
        return [
            self.result(row)
            for row in self.db.execute(
                "SELECT * FROM questions WHERE task=? AND state='pending' ORDER BY rowid", (task,)
            )
        ]

    @staticmethod
    def result(row):
        return dict(
            question_id=row["id"],
            message=row["message"],
            choices=json.loads(row["choices"]),
            state=row["state"],
            action=row["action"],
            answer=row["answer"],
        )

    def get(self, task, ident):
        row = self.db.execute(
            "SELECT * FROM questions WHERE task=? AND id=?", (task, ident)
        ).fetchone()
        if not row:
            raise ValueError("Question not in task")
        return self.result(row)

    def ask(self, task, operation, message, choices):
        fingerprint = payload_hash([message, choices])
        old = self.db.execute(
            "SELECT * FROM questions WHERE task=? AND operation=?", (task, operation)
        ).fetchone()
        if old:
            if old["hash"] != fingerprint:
                raise ValueError("Question operation reused with changed content")
            return self.result(old)
        if (
            self.pending(task)
            or self.db.execute("SELECT COUNT(*) FROM questions WHERE task=?", (task,)).fetchone()[0]
            >= 32
        ):
            raise ValueError("Resolve the pending question or start a new task")
        ident = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO questions(id,task,operation,hash,message,choices,state) "
            "VALUES (?,?,?,?,?,?,'pending')",
            (ident, task, operation, fingerprint, message, json.dumps(choices)),
        )
        return self.get(task, ident)

    def answer(self, task, ident, action, answer):
        old = self.get(task, ident)
        if old["state"] != "pending":
            if old["action"] != action or old["answer"] != answer:
                raise ValueError("Question already resolved differently")
            return old
        state = {"accept": "answered", "decline": "declined", "cancel": "cancelled"}[action]
        self.db.execute(
            "UPDATE questions SET state=?,action=?,answer=? WHERE id=?",
            (state, action, answer, ident),
        )
        return self.get(task, ident)

    def revoke(self):
        self.db.execute("""UPDATE questions SET state='cancelled',action='cancel'
            WHERE state='pending' AND task NOT IN (
              SELECT t.id FROM tasks t JOIN modes m ON m.id=t.mode JOIN lease l ON l.id=t.lease
              WHERE t.state='running' AND m.active=1 AND l.active=1)""")
