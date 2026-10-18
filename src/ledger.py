"""承認台帳: 人間の判断を、判断者・理由・ファイルの状態とともに、改ざんが分かる形で残す。

- 追記のみの JSONL（logs/ledger.jsonl）。各行は前の行のハッシュを持つ（ハッシュ連鎖）。
- 台帳に載るのは人間の判断だけ（accept・前提の見直し・退役・知見の承認・目的の改訂・取り込み・封印・再実行・鍵の信任）。
- 鍵を作ると（keygen）、以後の判断は Ed25519 で署名される。秘密鍵はリポジトリの外（~/.config/hypnet/）に置き、
  Claude Code のサンドボックスから読めないようにする。公開鍵の信任も台帳に載り、新しい鍵は既存の鍵の署名でしか信任できない。
- validate は、保護対象のファイルの現在の中身が台帳の最後の記録と一致するかを見る。
  一致しなければ「承認後に書き換えられた」、記録がなければ「承認記録のない書き込み」。

これは偽造を不可能にはしない（秘密鍵に触れられる者、台帳ごと作り直す者は防げない）。
偽造を「黙ってできること」から「痕跡が残る、意図的な行為」に変えるための仕組み。
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

LEDGER = Path("logs") / "ledger.jsonl"
# 保護するファイル（台帳と照合するファイル）。プロデューサーは自分のものを渡す
DEFAULT_GLOBS = ["hypnet.yaml", "**/*.yaml"]
EXCLUDE_DIRS = {"inbox", "logs", "build", ".git"}
HASHED = ("seq", "at", "actor", "action", "node", "path", "sha", "reason", "prev", "pub", "extra")


# ---------------- 基本 ----------------

def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else ""


def actor() -> str:
    for k in ("HYPNET_ACTOR", "HG_ACTOR"):
        if os.environ.get(k):
            return os.environ[k]
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5).stdout.strip()
        if name:
            return name
    except (OSError, subprocess.SubprocessError):
        pass
    return getpass.getuser()


def _entry_hash(e: dict) -> str:
    body = {k: e.get(k) for k in HASHED}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(root: Path) -> list[dict]:
    p = root / LEDGER
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def protected_files(root: Path, globs: list[str] | None = None) -> list[Path]:
    out: set[Path] = set()
    for g in globs or DEFAULT_GLOBS:
        out |= {p for p in root.glob(g) if p.is_file() and not set(p.relative_to(root).parts[:-1]) & EXCLUDE_DIRS}
    return sorted(out)


# ---------------- 鍵 ----------------

def key_path() -> Path:
    for k in ("HYPNET_SIGNING_KEY", "HG_SIGNING_KEY"):
        if os.environ.get(k):
            return Path(os.environ[k])
    return Path.home() / ".config" / "hypnet" / "signing_key"


def _load_private():
    p = key_path()
    try:
        data = p.read_bytes()
    except OSError:
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        return serialization.load_pem_private_key(data, password=None)
    except Exception:  # 鍵が壊れている・ライブラリがない
        return None


def _raw_pub(priv) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(raw_pub: bytes) -> str:
    return hashlib.sha256(raw_pub).hexdigest()[:16]


def _verify_sig(raw_pub_b64: str, h: str, sig_b64: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(base64.b64decode(raw_pub_b64)).verify(base64.b64decode(sig_b64), h.encode())
        return True
    except Exception:
        return False


# ---------------- 記録 ----------------

def record(root: Path, action: str, path: Path | None = None, node: str = "", reason: str = "",
           extra: dict | None = None, pub: str | None = None) -> dict:
    entries = read(root)
    rel = str(path.relative_to(root)) if path is not None else ""
    e = {
        "seq": len(entries) + 1,
        "at": datetime.now().isoformat(timespec="seconds"),
        "actor": actor(),
        "action": action,
        "node": node,
        "path": rel,
        "sha": file_sha(path) if path is not None else "",
        "reason": reason,
        "prev": entries[-1]["h"] if entries else "",
        "pub": pub,
        "extra": extra or {},
    }
    e["h"] = _entry_hash(e)
    priv = _load_private()
    if priv is not None:
        e["key"] = fingerprint(_raw_pub(priv))
        e["sig"] = base64.b64encode(priv.sign(e["h"].encode())).decode()
    p = root / LEDGER
    p.parent.mkdir(exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def keygen(root: Path, reason: str) -> tuple[str, bool]:
    """秘密鍵がなければ作り、公開鍵を台帳で信任する。(指紋, 新規作成したか)"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    p = key_path()
    created = False
    priv = _load_private()
    if priv is None:
        p.parent.mkdir(parents=True, exist_ok=True)
        priv = Ed25519PrivateKey.generate()
        p.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
        os.chmod(p, 0o600)
        created = True
    raw = _raw_pub(priv)
    fp = fingerprint(raw)
    if fp not in trusted_keys(root):
        record(root, "trust", node=fp, reason=reason, pub=base64.b64encode(raw).decode())
    return fp, created


def adopt(root: Path, paths: list[Path], reason: str) -> list[dict]:
    """手で直したファイルや、台帳導入前のファイルを、今の状態で承認し直す。"""
    if not reason:
        raise SystemExit("取り込みには理由（--reason）が要ります")
    return [record(root, "adopt", p, reason=reason) for p in paths]


def pending(root: Path, globs: list[str] | None = None) -> list[Path]:
    """台帳の記録と一致しない保護ファイル。"""
    last = latest_by_path(root)
    return [p for p in protected_files(root, globs)
            if last.get(str(p.relative_to(root)), {}).get("sha") != file_sha(p)]


def latest_by_path(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in read(root):
        if e.get("path"):
            out[e["path"]] = e
    return out


def trusted_keys(root: Path) -> dict[str, str]:
    return _walk(read(root))[0]


def _walk(entries: list[dict]) -> tuple[dict[str, str], list[tuple[str, int, str]]]:
    """(信任された鍵 {指紋: 公開鍵}, 問題 [(level, seq, msg)])"""
    trusted: dict[str, str] = {}
    problems: list[tuple[str, int, str]] = []
    prev = ""
    for e in entries:
        seq = e.get("seq", 0)
        if e.get("prev", "") != prev or _entry_hash(e) != e.get("h"):
            problems.append(("error", seq, "台帳の連鎖が壊れている（行の改ざん・削除・挿入）"))
        prev = e.get("h", "")
        signed_by = None
        if e.get("sig") and e.get("key"):
            pub = trusted.get(e["key"])
            if e.get("action") == "trust" and not trusted and e.get("pub") and fingerprint(base64.b64decode(e["pub"])) == e["key"]:
                pub = e["pub"]   # 最初の鍵だけは自己署名で信任（初回信任）
            if pub and _verify_sig(pub, e.get("h", ""), e["sig"]):
                signed_by = e["key"]
            else:
                problems.append(("error", seq, "署名が検証できない"))
        if e.get("action") == "trust":
            fp = e.get("node", "")
            if signed_by is not None and (not trusted or signed_by in trusted):
                trusted[fp] = e["pub"]
            else:
                problems.append(("error", seq, f"鍵 {fp} の信任に、既存の鍵の署名がない"))
            continue
        if trusted and signed_by is None:
            problems.append(("error", seq, f"署名のない判断（{e.get('action')} {e.get('node') or e.get('path')} by {e.get('actor')}）"))
    return trusted, problems


def verify(root: Path, level: str = "error", globs: list[str] | None = None,
           hint: str = "accept を通っていない。手で直したなら ledger adopt") -> list[tuple[str, str, str]]:
    """(level, where, msg) のリスト。"""
    out: list[tuple[str, str, str]] = []
    entries = read(root)
    _, problems = _walk(entries)
    out += [(lv, f"ledger#{seq}", msg) for lv, seq, msg in problems]
    last = latest_by_path(root)
    for p in protected_files(root, globs):
        rel = str(p.relative_to(root))
        e = last.get(rel)
        if e is None:
            out.append((level, rel, f"承認記録のない書き込み（{hint}）"))
        elif e.get("sha") != file_sha(p):
            out.append((level, rel, f"承認後に書き換えられた（最後の承認: #{e['seq']} {e['action']} by {e['actor']} {e['at'][:10]}）"))
    for rel, e in last.items():
        if e.get("sha") and not (root / rel).exists():
            out.append(("warn", rel, f"承認後に削除された（#{e['seq']}）"))
    return out


def history(root: Path, target: str | None = None) -> list[dict]:
    es = read(root)
    if target:
        es = [e for e in es if target in (e.get("node", ""), e.get("path", "")) or target in e.get("path", "")]
    return es
