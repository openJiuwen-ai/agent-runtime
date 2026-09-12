"""Exercise the stdlib helper as a non-root local process; no cluster needed."""

import json
import os

from openjiuwen_runtime.foundation.security.link_material_sync import FILES, invalidate, synchronize


def publish(root, sequence, status):
    generation = root / str(sequence)
    generation.mkdir()
    for name in FILES:
        (generation / name).write_text(json.dumps({"status": status}) if name == "profile.json" else name)
    pending = root / "next"
    pending.symlink_to(str(sequence), target_is_directory=True)
    os.replace(pending, root / "..data")


def test_private_atomic_copy_and_updates(tmp_path):
    source, target = tmp_path / "source", tmp_path / "owned" / "private"
    source.mkdir()
    publish(source, 1, "active")
    assert synchronize(source, target)
    assert not synchronize(source, target)
    assert target.stat().st_mode & 0o777 == 0o700
    for name in FILES:
        path = target / "identity" / name
        assert path.stat().st_mode & 0o777 == 0o400
        assert path.stat().st_uid == os.getuid()
    original = (target / "identity").resolve()
    publish(source, 2, "revoked")
    assert synchronize(source, target)
    assert json.loads((target / "identity" / "profile.json").read_text())["status"] == "revoked"
    assert original.exists()
    publish(source, 3, "active-again")
    assert synchronize(source, target)
    assert not original.exists()
    invalidate(target)
    assert not (target / "identity" / "profile.json").exists()
    assert synchronize(source, target)  # recovery retries the same generation
