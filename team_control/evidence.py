import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

from .contracts import validate_record
from .errors import BoundaryError, ReconciliationError
from .store import utc_now


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _relative_path(root, candidate):
    root = Path(root).resolve(strict=True)
    raw = Path(candidate)
    if raw.is_absolute():
        lexical = Path(os.path.abspath(str(raw)))
        anchor = lexical
        while True:
            try:
                if anchor.exists() and os.path.samefile(str(anchor), str(root)):
                    relative = lexical.relative_to(anchor)
                    break
            except OSError:
                pass
            if anchor.parent == anchor:
                raise BoundaryError(
                    "evidence path escapes repository: %s" % lexical
                )
            anchor = anchor.parent
    else:
        lexical = Path(os.path.abspath(str(root / raw)))
        try:
            relative = lexical.relative_to(root)
        except ValueError as error:
            raise BoundaryError(
                "evidence path escapes repository: %s" % lexical
            ) from error
    if not relative.parts:
        raise BoundaryError("evidence path must name a regular file")
    return root, relative


def _read_regular_file(root, candidate):
    root, relative = _relative_path(root, candidate)
    descriptors = []
    try:
        current = os.open(str(root), _DIRECTORY_FLAGS)
        descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(
            relative.parts[-1], _FILE_FLAGS, dir_fd=current
        )
        descriptors.append(file_descriptor)
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BoundaryError("evidence path must be a regular file")
        chunks = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return relative, b"".join(chunks)
    except BoundaryError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError) as error:
        raise BoundaryError(
            "evidence path must be an existing non-symlink regular file"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _write_regular_file(root, relative_parent, filename, payload):
    root = Path(root).resolve(strict=True)
    descriptors = []
    temporary_name = ".%s.%s.tmp" % (filename, uuid.uuid4().hex)
    temporary_created = False
    try:
        current = os.open(str(root), _DIRECTORY_FLAGS)
        descriptors.append(current)
        for part in relative_parent.parts:
            try:
                os.mkdir(part, mode=0o755, dir_fd=current)
            except FileExistsError:
                pass
            current = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(current)

        try:
            existing = os.stat(filename, dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise BoundaryError("summary target must be a regular file")

        output = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o644,
            dir_fd=current,
        )
        temporary_created = True
        try:
            view = memoryview(payload)
            while view:
                written = os.write(output, view)
                view = view[written:]
            os.fsync(output)
        finally:
            os.close(output)
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=current,
            dst_dir_fd=current,
        )
        temporary_created = False
    except BoundaryError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError) as error:
        raise BoundaryError("summary path must stay under the repository") from error
    finally:
        if temporary_created and descriptors:
            try:
                os.unlink(temporary_name, dir_fd=descriptors[-1])
            except FileNotFoundError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class EvidenceManager:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def record(self, dispatch_id, kind, path, source_sha=None):
        if self.store.get_task(dispatch_id) is None:
            raise ReconciliationError("task is missing: %s" % dispatch_id)
        relative, contents = _read_regular_file(self.context.root, path)
        record = {
            "schema_version": 1,
            "evidence_id": str(uuid.uuid4()),
            "dispatch_id": dispatch_id,
            "kind": kind,
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(contents).hexdigest(),
            "source_sha": source_sha,
            "created_at": utc_now(),
        }
        validate_record("evidence", record)
        return self.store.add_evidence(record)

    def write_summary(self, dispatch_id):
        if self.store.get_task(dispatch_id) is None:
            raise ReconciliationError("task is missing: %s" % dispatch_id)
        evidence = self.store.list_evidence(dispatch_id)
        for record in evidence:
            validate_record("evidence", record)
            _, contents = _read_regular_file(self.context.root, record["path"])
            digest = hashlib.sha256(contents).hexdigest()
            if digest != record["sha256"]:
                raise ReconciliationError(
                    "evidence hash no longer matches file: %s" % record["path"]
                )

        payload = {
            "schema_version": 1,
            "dispatch_id": dispatch_id,
            "generated_at": utc_now(),
            "evidence": evidence,
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        parent = Path("artifacts") / "dispatches" / dispatch_id
        _write_regular_file(
            self.context.root, parent, "evidence-index.json", encoded
        )
        return Path(self.context.root) / parent / "evidence-index.json"
