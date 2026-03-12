from __future__ import annotations

import hashlib
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    IntegerField,
    Model,
    OperationalError,
    SqliteDatabase,
    TextField,
)

from config import CATEGORIES
from utils import expand_with_synonyms, sanitize_content, tokenize_query


@dataclass
class SearchResult:
    id: int
    category: str
    title: str
    content: str
    source: str
    last_updated: datetime
    score: float


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeBase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db = SqliteDatabase(
            str(db_path),
            pragmas={
                "journal_mode": "wal",
                "foreign_keys": 1,
                "cache_size": -64 * 1000,
                "synchronous": "normal",
            },
            check_same_thread=False,
        )
        self._write_lock = threading.Lock()
        self._write_queue: "queue.Queue[tuple]" = queue.Queue()
        self._writer_running = False
        self._writer_thread: Optional[threading.Thread] = None
        self._bind_models()
        self._init_schema()
        self._start_writer()

    def _bind_models(self) -> None:
        db_ref = self.db

        class BaseModel(Model):
            class Meta:
                database = db_ref

        class Knowledge(BaseModel):
            id = AutoField()
            category = CharField(index=True)
            title = CharField(unique=True)
            content = TextField()
            source = CharField(default="manual")
            content_hash = CharField(index=True)
            revision = IntegerField(default=1)
            last_updated = DateTimeField(default=utc_now)

        class KnowledgeVersion(BaseModel):
            id = AutoField()
            knowledge = IntegerField(index=True)
            revision = IntegerField()
            old_content = TextField()
            old_hash = CharField()
            changed_at = DateTimeField(default=utc_now)

        class UpdateLog(BaseModel):
            id = AutoField()
            timestamp = DateTimeField(default=utc_now)
            source = CharField()
            count = IntegerField(default=0)
            status = CharField(default="success")

        self.BaseModel = BaseModel
        self.Knowledge = Knowledge
        self.KnowledgeVersion = KnowledgeVersion
        self.UpdateLog = UpdateLog

    def _init_schema(self) -> None:
        self.db.connect(reuse_if_open=True)
        self.db.create_tables([self.Knowledge, self.KnowledgeVersion, self.UpdateLog], safe=True)
        self._migrate_existing_schema()
        self.db.execute_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                title,
                content,
                category,
                content='knowledge',
                content_rowid='id'
            )
            """
        )
        self.db.execute_sql(
            """
            DROP TRIGGER IF EXISTS knowledge_ai;
            """
        )
        self.db.execute_sql(
            """
            DROP TRIGGER IF EXISTS knowledge_au;
            """
        )
        self.db.execute_sql(
            """
            DROP TRIGGER IF EXISTS knowledge_ad;
            """
        )
        self.db.execute_sql(
            """
            CREATE TRIGGER knowledge_ai AFTER INSERT ON knowledge BEGIN
                INSERT INTO knowledge_fts(rowid, title, content, category)
                VALUES (new.id, new.title, new.content, new.category);
            END;
            """
        )
        self.db.execute_sql(
            """
            CREATE TRIGGER knowledge_au AFTER UPDATE ON knowledge BEGIN
                INSERT INTO knowledge_fts(rowid, title, content, category)
                VALUES (new.id, new.title, new.content, new.category);
            END;
            """
        )
        self.db.execute_sql("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')")

    def _table_columns(self, table_name: str) -> set[str]:
        rows = self.db.execute_sql(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}

    def _migrate_existing_schema(self) -> None:
        knowledge_columns = self._table_columns("knowledge")

        if "source" not in knowledge_columns:
            self.db.execute_sql("ALTER TABLE knowledge ADD COLUMN source TEXT DEFAULT 'manual'")
        if "content_hash" not in knowledge_columns:
            self.db.execute_sql("ALTER TABLE knowledge ADD COLUMN content_hash TEXT")
        if "revision" not in knowledge_columns:
            self.db.execute_sql("ALTER TABLE knowledge ADD COLUMN revision INTEGER DEFAULT 1")
        if "last_updated" not in knowledge_columns:
            self.db.execute_sql("ALTER TABLE knowledge ADD COLUMN last_updated TIMESTAMP")

        self.db.execute_sql("UPDATE knowledge SET revision = 1 WHERE revision IS NULL")
        self.db.execute_sql("UPDATE knowledge SET source = 'manual' WHERE source IS NULL OR source = ''")
        self.db.execute_sql("UPDATE knowledge SET content_hash = '' WHERE content_hash IS NULL")

        update_columns = self._table_columns("updatelog") if "updatelog" in self.db.get_tables() else set()
        if update_columns:
            if "timestamp" not in update_columns:
                self.db.execute_sql("ALTER TABLE updatelog ADD COLUMN timestamp TIMESTAMP")
            if "source" not in update_columns:
                self.db.execute_sql("ALTER TABLE updatelog ADD COLUMN source TEXT")
            if "count" not in update_columns:
                self.db.execute_sql("ALTER TABLE updatelog ADD COLUMN count INTEGER DEFAULT 0")
            if "status" not in update_columns:
                self.db.execute_sql("ALTER TABLE updatelog ADD COLUMN status TEXT DEFAULT 'success'")

    def close(self) -> None:
        self._writer_running = False
        if self._writer_thread and self._writer_thread.is_alive():
            self._write_queue.put((None, None, None))
            self._writer_thread.join(timeout=2)
        if not self.db.is_closed():
            self.db.close()

    def _start_writer(self) -> None:
        self._writer_running = True
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        while self._writer_running:
            op_name, payload, done_event = self._write_queue.get()
            if op_name is None:
                break
            try:
                if op_name == "add":
                    self._upsert_knowledge(**payload)
            finally:
                if done_event is not None:
                    done_event.set()

    def queue_add_knowledge(self, category: str, title: str, content: str, source: str = "manual") -> None:
        done = threading.Event()
        self._write_queue.put(
            (
                "add",
                {"category": category, "title": title, "content": content, "source": source},
                done,
            )
        )
        done.wait(timeout=2)

    def add_knowledge(self, category: str, title: str, content: str, source: str = "manual") -> bool:
        return self._upsert_knowledge(category=category, title=title, content=content, source=source)

    def _upsert_knowledge(self, category: str, title: str, content: str, source: str) -> bool:
        if category not in CATEGORIES:
            return False
        cleaned = sanitize_content(content)
        content_hash = hashlib.md5(cleaned.encode("utf-8")).hexdigest()
        with self._write_lock, self.db.atomic():
            try:
                existing = self.Knowledge.get_or_none(self.Knowledge.title == title)
            except OperationalError as exc:
                if "no such column" not in str(exc).lower():
                    raise
                self._migrate_existing_schema()
                existing = self.Knowledge.get_or_none(self.Knowledge.title == title)
            if existing and existing.content_hash == content_hash:
                return False
            if existing:
                existing_revision = existing.revision or 1
                existing_hash = existing.content_hash or ""
                self.KnowledgeVersion.create(
                    knowledge=existing.id,
                    revision=existing_revision,
                    old_content=existing.content,
                    old_hash=existing_hash,
                )
                existing.category = category
                existing.content = cleaned
                existing.source = source
                existing.content_hash = content_hash
                existing.revision = existing_revision + 1
                existing.last_updated = utc_now()
                existing.save()
                return True

            self.Knowledge.create(
                category=category,
                title=title,
                content=cleaned,
                source=source,
                content_hash=content_hash,
            )
            return True

    def get_by_category(self, category: str) -> List[Dict]:
        query = (
            self.Knowledge.select()
            .where(self.Knowledge.category == category)
            .order_by(self.Knowledge.last_updated.desc())
        )
        return [model_to_dict(row) for row in query]

    def get_all(self) -> List[Dict]:
        query = self.Knowledge.select().order_by(self.Knowledge.last_updated.desc())
        return [model_to_dict(row) for row in query]

    def search(self, query: str, limit: int = 25) -> List[Dict]:
        query_text = (query or "").strip()
        if not query_text:
            return []

        tokens = tokenize_query(query_text)
        expanded_tokens = expand_with_synonyms(tokens)

        results: List[Dict] = []
        if expanded_tokens:
            safe_terms = []
            for token in expanded_tokens:
                sanitized = "".join(ch for ch in token if ch.isalnum())
                if sanitized:
                    safe_terms.append(f'"{sanitized}"*')

            if safe_terms:
                match_expr = " OR ".join(safe_terms)
                try:
                    cursor = self.db.execute_sql(
                        """
                        SELECT k.id, k.category, k.title, k.content, k.source, k.last_updated,
                               bm25(knowledge_fts, 2.0, 1.0, 0.6) AS score
                        FROM knowledge_fts
                        JOIN knowledge k ON k.id = knowledge_fts.rowid
                        WHERE knowledge_fts MATCH ?
                        ORDER BY score ASC, k.last_updated DESC
                        LIMIT ?
                        """,
                        (match_expr, limit),
                    )
                    rows = cursor.fetchall()
                    results = [
                        {
                            "id": row[0],
                            "category": row[1],
                            "title": row[2],
                            "content": row[3],
                            "source": row[4],
                            "last_updated": row[5],
                            "score": float(row[6]),
                        }
                        for row in rows
                    ]
                except OperationalError:
                    results = []

        if results:
            return results

        fallback_terms = expanded_tokens or [query_text.lower()]
        return self._search_like_fallback(fallback_terms, limit)

    def _search_like_fallback(self, terms: List[str], limit: int) -> List[Dict]:
        clauses = []
        params: List[str | int] = []
        for term in terms:
            value = (term or "").strip().lower()
            if not value:
                continue
            clauses.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(category) LIKE ?)")
            like_value = f"%{value}%"
            params.extend([like_value, like_value, like_value])

        if not clauses:
            return []

        sql = (
            f"SELECT id, category, title, content, source, last_updated, 9999.0 AS score "
            f"FROM knowledge WHERE {' OR '.join(clauses)} "
            f"ORDER BY last_updated DESC LIMIT ?"
        )
        params.append(limit)
        rows = self.db.execute_sql(sql, params).fetchall()
        return [
            {
                "id": row[0],
                "category": row[1],
                "title": row[2],
                "content": row[3],
                "source": row[4],
                "last_updated": row[5],
                "score": float(row[6]),
            }
            for row in rows
        ]

    def rollback_title_to_revision(self, title: str, revision: int) -> bool:
        with self._write_lock, self.db.atomic():
            entry = self.Knowledge.get_or_none(self.Knowledge.title == title)
            if not entry:
                return False
            version = self.KnowledgeVersion.get_or_none(
                (self.KnowledgeVersion.knowledge == entry.id)
                & (self.KnowledgeVersion.revision == revision)
            )
            if not version:
                return False
            self.KnowledgeVersion.create(
                knowledge=entry.id,
                revision=entry.revision,
                old_content=entry.content,
                old_hash=entry.content_hash,
            )
            entry.content = version.old_content
            entry.content_hash = version.old_hash
            entry.revision += 1
            entry.last_updated = utc_now()
            entry.save()
            return True

    def list_versions(self, title: str) -> List[Dict]:
        entry = self.Knowledge.get_or_none(self.Knowledge.title == title)
        if not entry:
            return []
        query = (
            self.KnowledgeVersion.select()
            .where(self.KnowledgeVersion.knowledge == entry.id)
            .order_by(self.KnowledgeVersion.revision.desc())
        )
        return [model_to_dict(row) for row in query]

    def log_update(self, source: str, count: int, status: str = "success") -> None:
        self.UpdateLog.create(source=source, count=count, status=status)


def model_to_dict(instance: Model) -> Dict:
    data = {}
    for field_name in instance._meta.fields:
        data[field_name] = getattr(instance, field_name)
    return data
