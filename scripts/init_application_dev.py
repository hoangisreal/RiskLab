"""Create ignored, permission-restricted local database secrets without printing values."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def write_once(path: Path, value: str) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w") as stream:
        stream.write(value + "\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1] / ".secrets"
    root.mkdir(mode=0o700, exist_ok=True)
    for kind in ("owner", "api", "worker"):
        password = root / f"local_{kind}_password"
        write_once(password, secrets.token_urlsafe(32))
        login = "risklab_migrator" if kind == "owner" else f"risklab_{kind}_login"
        url = f"postgresql+psycopg://{login}:{password.read_text().strip()}@127.0.0.1:15432/risklab"
        write_once(root / f"local_{kind}_url", url)
    print("Local secret files ready under .secrets/. Existing values were not overwritten.")


if __name__ == "__main__":
    main()
