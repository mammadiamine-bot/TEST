import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "tools/prepare-usb.ps1").read_text()
CODE = "\n".join(l for l in SCRIPT.splitlines() if not l.strip().startswith("#"))


def test_usb_script_contains_no_destructive_commands():
    for bad in ("Format-Volume", "Clear-Disk", "Initialize-Disk", "Remove-Partition", "New-Partition", "Set-Disk", "Set-Partition", "diskpart", "format.com", "Remove-Item", "Remove-ItemProperty",
                "rd ", "rmdir", " del ", "erase", "-Force", "Set-ExecutionPolicy", "Set-NetFirewall", "New-NetFirewall", "Enable-PSRemoting", "Set-Service", "reg add"):
        assert bad.lower() not in CODE.lower(), bad


def test_usb_script_only_copies_after_explicit_yes_and_never_overwrites():
    assert "Read-Host \"Type YES to copy\"" in CODE and "-ne 'YES'" in CODE
    assert "Test-Path -LiteralPath $dest" in CODE and "exit 1" in CODE  # refuses when the target folder exists
    assert re.search(r"DriveType -eq 'Removable'", CODE)  # only removable drives are offered; internal disks can never be chosen
    assert "Get-FileHash" in CODE  # copy is verified, not assumed
