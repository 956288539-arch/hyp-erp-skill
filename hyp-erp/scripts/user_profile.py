"""Read and update local HYP preferences bound to an exact ERP account."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit


FIELDS = {
    "employee_name", "company", "reimbursement_department", "archive_root",
    "payee_type", "payee_name", "payee_record_id", "bank_name", "bank_last4",
    "mailbox_account",
}


def identity(origin, account):
    url = urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("Use an HTTPS ERP origin without credentials, path, or query")
    if not account.strip() or len(account) > 256:
        raise ValueError("An observed ERP account is required")
    return {"origin": origin.rstrip("/"), "account": account.strip()}


def default_root():
    base = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return base / "user-data" / "hyp-erp" / "profiles"


def profile_path(root, owner):
    key = json.dumps(owner, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return Path(root) / (hashlib.sha256(key).hexdigest() + ".json")


def validate_field(name, entry, stored=False):
    if name not in FIELDS:
        raise ValueError("Unsupported preference field")
    if entry is None and not stored:
        return
    allowed = {"value", "source", "verified_at"} if stored else {"value", "source"}
    if not isinstance(entry, dict) or set(entry) != allowed:
        raise ValueError("Each field needs value and source; stored fields also need verified_at")
    value = entry["value"]
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("Preference values must be nonempty short strings")
    if entry["source"] not in ("user", "erp"):
        raise ValueError("Source must be user or erp")
    if stored and not isinstance(entry["verified_at"], str):
        raise ValueError("Invalid verification timestamp")
    if name == "payee_type" and value not in ("个人", "收款单位"):
        raise ValueError("Invalid payee type")
    if name == "bank_last4" and not re.fullmatch(r"[0-9]{4}", value):
        raise ValueError("Only the final four bank account digits may be stored")
    if name == "archive_root" and not Path(value).is_absolute():
        raise ValueError("Archive root must be an absolute local path")


def load_profile(root, owner):
    path = profile_path(root, owner)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("identity") != owner or not isinstance(data.get("fields"), dict)):
        raise ValueError("Profile version or identity mismatch; existing file left unchanged")
    for name, entry in data["fields"].items():
        validate_field(name, entry, stored=True)
    return data


@contextmanager
def profile_lock(root, owner):
    Path(root).mkdir(parents=True, exist_ok=True)
    lock = profile_path(root, owner).with_suffix(".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Profile is being updated; inspect the other task before retrying") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def save_profile(root, owner, patch):
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Supply a nonempty object of verified preferences")
    for name, entry in patch.items():
        validate_field(name, entry)
    with profile_lock(root, owner):
        data = load_profile(root, owner) or {"schema_version": 1, "identity": owner, "fields": {}}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for name, entry in patch.items():
            if entry is None:
                data["fields"].pop(name, None)
            else:
                data["fields"][name] = {**entry, "verified_at": now}
        data["updated_at"] = now
        path = profile_path(root, owner)
        fd, temp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return load_profile(root, owner)


def forget_profile(root, owner):
    with profile_lock(root, owner):
        path = profile_path(root, owner)
        if not path.exists():
            return False
        load_profile(root, owner)
        path.unlink()
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("load", "save", "forget"))
    parser.add_argument("--origin", default="https://erp.hyp-arch.com")
    parser.add_argument("--account", required=True)
    parser.add_argument("--input", help="UTF-8 JSON patch file; '-' reads stdin")
    parser.add_argument("--root", type=Path, help="Local profile directory override (also for isolated tests)")
    args = parser.parse_args()
    try:
        owner = identity(args.origin, args.account)
        root = (args.root or default_root()).expanduser().resolve()
        if args.action == "load":
            data = load_profile(root, owner)
            result = {"status": "found" if data else "missing", "profile": data}
        elif args.action == "save":
            if not args.input:
                raise ValueError("save requires --input")
            raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8-sig")
            data = save_profile(root, owner, json.loads(raw))
            result = {"status": "saved", "path": str(profile_path(root, owner)), "fields": sorted(data["fields"])}
        else:
            result = {"status": "forgotten" if forget_profile(root, owner) else "missing"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
