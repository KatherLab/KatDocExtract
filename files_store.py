# files_store.py

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException


@dataclass(frozen=True)
class StoredFile:
    meta: Dict
    path: Path


class FileStore:
    def save_upload(
        self,
        filename: str,
        content: bytes,
        mimetype: Optional[str],
        purpose: str = "ocr",
        sample_type: Optional[str] = None,
        source: str = "upload",
    ) -> Dict:
        raise NotImplementedError

    def list_files(self) -> List[Dict]:
        raise NotImplementedError

    def get(self, file_id: str) -> StoredFile:
        raise NotImplementedError

    def delete(self, file_id: str) -> Dict:
        raise NotImplementedError

    def read_bytes(self, file_id: str) -> Tuple[bytes, Dict]:
        sf = self.get(file_id)
        if sf.meta.get("deleted"):
            raise HTTPException(status_code=404, detail="File was deleted.")
        try:
            return sf.path.read_bytes(), sf.meta
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File content not found.")


class LocalDirFileStore(FileStore):
    """
    On delete:
      - mark meta.deleted=true
      - remove content.bin from disk (hard delete bytes)
      - keep meta.json as a tombstone so DELETE remains idempotent + retrievable state is clear
    """

    def __init__(self, root_dir: str):
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

        # optional behavior toggles
        self.hard_delete_bytes = os.getenv("FILES_HARD_DELETE_BYTES", "true").lower() in {"1", "true", "yes"}
        self.purge_tombstones = os.getenv("FILES_PURGE_TOMBSTONES", "false").lower() in {"1", "true", "yes"}

    def _dir_for(self, file_id: str) -> Path:
        return self.root / file_id

    def _meta_path(self, file_id: str) -> Path:
        return self._dir_for(file_id) / "meta.json"

    def _content_path(self, file_id: str) -> Path:
        return self._dir_for(file_id) / "content.bin"

    def _count_lines_if_jsonl(self, filename: str, mimetype: Optional[str], content: bytes) -> Optional[int]:
        name_l = (filename or "").lower()
        mt = (mimetype or "").lower()
        if name_l.endswith(".jsonl") or mt in {"application/jsonl", "application/x-jsonlines", "application/jsonlines"}:
            if not content:
                return 0
            return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)
        return None

    def _signature(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def save_upload(
        self,
        filename: str,
        content: bytes,
        mimetype: Optional[str],
        purpose: str = "ocr",
        sample_type: Optional[str] = None,
        source: str = "upload",
    ) -> Dict:
        if not content:
            raise HTTPException(status_code=400, detail="Empty file uploaded.")

        file_id = str(uuid.uuid4())
        d = self._dir_for(file_id)
        d.mkdir(parents=True, exist_ok=False)

        created_at = int(time.time())
        sig = self._signature(content)
        num_lines = self._count_lines_if_jsonl(filename, mimetype, content)

        meta = {
            "id": file_id,
            "object": "file",
            "bytes": len(content),
            "created_at": created_at,
            "filename": filename or "uploaded.file",
            "purpose": purpose or "ocr",
            "sample_type": sample_type,
            "source": source or "upload",
            "num_lines": num_lines,
            "mimetype": mimetype,
            "signature": sig,
            "deleted": False,
            "deleted_at": None,
        }

        self._content_path(file_id).write_bytes(content)
        self._meta_path(file_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def list_files(self) -> List[Dict]:
        out: List[Dict] = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            mp = child / "meta.json"
            if not mp.exists():
                continue
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if meta.get("deleted"):
                continue
            out.append(meta)

        out.sort(key=lambda m: int(m.get("created_at") or 0), reverse=True)
        return out

    def get(self, file_id: str) -> StoredFile:
        mp = self._meta_path(file_id)
        cp = self._content_path(file_id)
        if not mp.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        meta = json.loads(mp.read_text(encoding="utf-8"))
        return StoredFile(meta=meta, path=cp)

    def delete(self, file_id: str) -> Dict:
        sf = self.get(file_id)

        # idempotent
        if sf.meta.get("deleted") is True:
            return {"id": file_id, "object": "file", "deleted": True}

        # mark tombstone
        sf.meta["deleted"] = True
        sf.meta["deleted_at"] = int(time.time())
        self._meta_path(file_id).write_text(json.dumps(sf.meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # hard-delete bytes
        if self.hard_delete_bytes:
            try:
                sf.path.unlink(missing_ok=True)
            except Exception:
                # If unlink fails, keep tombstone; content might still exist.
                pass

        # optional: purge whole directory (including tombstone) if you truly want it gone
        if self.purge_tombstones:
            try:
                # remove meta.json too
                self._meta_path(file_id).unlink(missing_ok=True)
                # remove dir if empty
                d = self._dir_for(file_id)
                if d.exists():
                    for p in d.iterdir():
                        # if anything left, don't delete blindly
                        return {"id": file_id, "object": "file", "deleted": True}
                    d.rmdir()
            except Exception:
                pass

        return {"id": file_id, "object": "file", "deleted": True}
