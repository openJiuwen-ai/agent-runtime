"""Kubernetes AtomicWriter semantics, not ordinary in-place JSON edits."""

import json
import os
import shutil

import pytest
from openjiuwen_runtime.foundation.security.link_profile import (
    LinkProfile,
    LinkProfileError,
)

from ..link_mtls_helpers import provision


def publish(root, original, number, **updates):
    generation = root / f"generation-{number}"
    shutil.copytree(original, generation)
    path = generation / "profile.json"
    data = json.loads(path.read_text())
    data.update(updates)
    path.write_text(json.dumps(data))
    pending = root / "pending"
    pending.symlink_to(generation.name, target_is_directory=True)
    os.replace(pending, root / "..data")
    return generation


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("JIUWENSWARM_LINK_"):
            monkeypatch.delenv(name)
    bundle = tmp_path / "bundle"
    provision(bundle, mtls_deployment_id="atomic-test", endpoints={})
    root = tmp_path / "mount"
    root.mkdir()
    for name in ("profile.json", "ca.crt", "tls.crt", "tls.key"):
        (root / name).symlink_to("..data/" + name)
    original = bundle / "agentserver"
    publish(root, original, 1)
    return root, original


def test_atomic_writer_revocation_visible_to_loaded_profile(mounted):
    root, original = mounted
    profile = LinkProfile.load(str(root / "profile.json"))
    publish(root, original, 2, status="revoked")
    with pytest.raises(LinkProfileError, match="not active"):
        profile.current()


def test_trust_only_update_survives_collected_old_generation(mounted):
    root, original = mounted
    profile = LinkProfile.load(str(root / "profile.json"))
    peers = profile.current()["peers"]
    peers["gateway"] = ["a" * 64]
    publish(root, original, 2, peers=peers)
    shutil.rmtree(root / "generation-1")
    assert profile.current()["peers"] == peers
    profile.ssl_context()
    assert "generation-2" in profile.server_kwargs()["ssl_certfile"]


def test_tls_replacement_requires_explicit_reload(mounted):
    root, original = mounted
    profile = LinkProfile.load(str(root / "profile.json"))
    second = publish(root, original, 2)
    with (second / "ca.crt").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(LinkProfileError, match="TLS material changed"):
        profile.current()
    assert (
        LinkProfile.load(str(root / "profile.json")).mtls_deployment_id
        == profile.mtls_deployment_id
    )


def test_atomic_epoch_change_does_not_grant_new_binding(mounted):
    root, original = mounted
    profile = LinkProfile.load(str(root / "profile.json"))
    publish(root, original, 2, mtls_binding_epoch=2)
    with pytest.raises(LinkProfileError, match="binding changed"):
        profile.current()
