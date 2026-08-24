from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_linux_installer_has_independent_roles() -> None:
    script = _read("deploy/linux/install-knoa.sh")

    assert 'ROLE="all"' in script
    assert "hub|node|all" in script
    assert "install_hub=0" in script
    assert "install_node=0" in script
    assert "systemctl --user enable --now knoa-hosted-hub.service" in script
    assert "systemctl --user enable --now knoa-node.service" in script
    assert "systemctl --user disable --now knoa.service" in script
    assert "legacy_pids=" in script
    assert "sudo loginctl enable-linger" in script
    assert 'package_spec="$SOURCE_PATH[semantic]"' in script
    assert "-m knoa_agent.semantic_health --provision" in script
    assert "Environment=KNOA_BGE_PRELOAD=1" in _read(
        "deploy/linux/knoa-node.service"
    )


def test_linux_services_keep_hub_and_node_processes_separate() -> None:
    hub = _read("deploy/linux/knoa-hosted-hub.service")
    node = _read("deploy/linux/knoa-node.service")

    assert "knoa-hub --deployment-mode hosted_single_node" in hub
    assert "knoa_platform.service" not in hub
    assert "knoa_platform.service" in node
    assert "knoa-hub" not in node
    assert "Restart=on-failure" in hub
    assert "Restart=on-failure" in node


def test_linux_hosted_hub_can_publish_the_android_app() -> None:
    script = _read("deploy/linux/publish-knoa-app.sh")

    assert "knoa_platform.hub.admin mobile-publish" in script
    assert "knoa_platform.hub.admin mobile-latest" in script
    assert "/downloads/android/latest.apk" in script

    remote = _read("scripts/publish-hosted-mobile-apk.sh")
    combined = _read("scripts/build-and-publish-mobile-apk.sh")
    assert "mobile-upload" in remote
    assert "hosted-hub-release-publisher.token" in remote
    assert "build-mobile-apk.sh" in combined
    assert "publish-hosted-mobile-apk.sh" in combined


def test_mobile_build_creates_a_self_describing_windows_release_bundle() -> None:
    build = _read("scripts/build-mobile-apk.sh")
    package = _read("scripts/package-mobile-release.sh")

    assert '"$SCRIPT_DIR/package-mobile-release.sh"' in build
    assert 'publisher_name="Publish-Knoa-$version_name.cmd"' in package
    assert "knoa-$version_name.release.json" in package
    assert "--version-name" in package
    assert "--version-code" in package
