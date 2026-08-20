from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_linux_installer_uses_universal_bundle_and_three_services() -> None:
    installer = _read("deploy/product/linux/install-knoa-bundle.sh")

    assert '--role all' in installer
    assert 'host-state.json' in installer
    assert 'knoa-host-lifecycle.service' in installer
    assert 'systemctl enable --now knoa-host-lifecycle.service' in installer
    assert 'systemctl disable --now knoa-hub.service knoa-node.service' in installer
    assert 'product-role' not in installer


def test_source_installers_expose_the_same_lifecycle_channel() -> None:
    linux = _read("deploy/linux/install-knoa.sh")
    linux_lifecycle = _read("deploy/linux/knoa-host-lifecycle.service")
    windows = _read("deploy/windows/Install-Knoa.ps1")
    windows_lifecycle = _read("deploy/windows/Run-KnoaHostLifecycle.ps1")

    assert "--channel-source" in linux
    assert "installed_commit" in linux
    assert "EFFECTIVE_ROLE" in linux
    assert "--mode source" in linux_lifecycle
    assert "KNOA_LIFECYCLE_TOKEN_FILE" in _read("deploy/linux/knoa-node.service")
    assert "ChannelSourcePath" in windows
    assert "installed_commit" in windows
    assert "KnoaHostLifecycle" in windows
    assert "--mode source" in windows_lifecycle


def test_linux_console_and_broker_ports_are_loopback_only() -> None:
    hub = _read("deploy/product/linux/knoa-hub.service")
    lifecycle = _read("deploy/product/linux/knoa-host-lifecycle.service")

    assert '--console-host 127.0.0.1 --console-port 9532' in hub
    assert '--host 127.0.0.1 --port 9533' in lifecycle
    assert '@LIFECYCLE_TOKEN@' in lifecycle


def test_windows_installer_uses_universal_bundle_and_three_winsw_services() -> None:
    installer = _read("deploy/product/windows/Install-KnoaBundle.ps1")

    assert '--role all' in installer
    assert 'host-state.json' in installer
    assert 'Install-WinSWService "KnoaHostLifecycle"' in installer
    assert 'Install-WinSWService "KnoaHostedHub"' in installer
    assert 'Install-WinSWService "KnoaNode"' in installer
    assert 'product-role' not in installer


def test_windows_node_installs_interactive_desktop_companion() -> None:
    installer = _read("deploy/product/windows/Install-KnoaBundle.ps1")
    node_runner = _read("deploy/product/windows/Run-KnoaNode.ps1")
    companion_runner = _read("deploy/product/windows/Run-KnoaDesktopCompanion.ps1")

    assert 'HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' in installer
    assert 'KnoaDesktopCompanion' in installer
    assert '*S-1-5-32-545:(OI)(CI)RX' in installer
    assert 'KNOA_DESKTOP_COMPANION_TOKEN_FILE' in node_runner
    assert 'bin/knoa-desktop-companion.cmd' in companion_runner


def test_windows_hub_console_public_url_is_explicit() -> None:
    runner = _read("deploy/product/windows/Run-KnoaHub.ps1")

    assert '--console-host 127.0.0.1' in runner
    assert '--console-port $ConsolePort' in runner
    assert '--public-url $PublicUrl' in runner


def test_windows_setup_is_a_single_gui_bootstrap_with_role_selection() -> None:
    setup = _read("deploy/product/windows/KnoaSetup.iss")

    assert 'WizardStyle=modern' in setup
    assert 'Name: "node"; Description: "Knoa Node（推荐）"' in setup
    assert 'Name: "hub"; Description: "Knoa Hub"' in setup
    assert 'Name: "all"; Description: "Knoa Hub + Node"' in setup
    assert 'Flags: runhidden waituntilterminated' in setup
    assert 'Uninstall-KnoaHost.ps1' in setup


def test_linux_deb_builder_creates_one_native_package(tmp_path: Path) -> None:
    bundle = tmp_path / "knoa-host.zip"
    updater = tmp_path / "knoa-update"
    trust = tmp_path / "release-trust.json"
    bundle.write_bytes(b"bundle")
    updater.write_bytes(b"updater")
    updater.chmod(0o755)
    trust.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "output"

    subprocess.run(
        [
            str(ROOT / "scripts/build_linux_deb.sh"),
            "1.2.3",
            "x86_64",
            str(bundle),
            str(updater),
            str(trust),
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    package = output / "knoa_1.2.3_amd64.deb"
    assert package.is_file()
    listing = subprocess.run(
        ["dpkg-deb", "--contents", str(package)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "usr/lib/knoa/bootstrap/knoa-host.zip" in listing
