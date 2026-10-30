"""Allowlisted tool registry. The AI and the UI can only invoke tools defined here.

Nothing accepts a free-form command. Parameters are validated against a strict spec and are
passed to PowerShell as base64-encoded JSON, never interpolated into script text.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Risk(str, Enum):
    READ_ONLY = "read_only"
    LOW = "low"
    HIGH = "high"


class ToolError(ValueError):
    pass


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+\-]{0,95}$")


@dataclass(frozen=True)
class Param:
    kind: str  # "name" | "int"
    min: int = 0
    max: int = 0
    required: bool = True

    def check(self, key: str, v: Any) -> Any:
        if self.kind == "name":
            if not isinstance(v, str) or not NAME_RE.match(v):
                raise ToolError(f"parameter {key!r} has an invalid value")
            return v
        if self.kind == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not (self.min <= v <= self.max):
                raise ToolError(f"parameter {key!r} must be an integer in [{self.min},{self.max}]")
            return v
        raise ToolError("unknown parameter kind")


@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    risk: Risk
    kind: str  # diagnostic | repair | console
    script: str
    timeout_s: int = 60
    params: dict[str, Param] = field(default_factory=dict)
    verify_module: str | None = None  # diagnostic module re-run after a repair
    verify_check: str | None = None  # check name in diagnostics.CHECKS

    def validate(self, supplied: dict[str, Any] | None) -> dict[str, Any]:
        supplied = supplied or {}
        extra = set(supplied) - set(self.params)
        if extra:
            raise ToolError(f"unexpected parameters: {sorted(extra)}")
        out = {}
        for k, spec in self.params.items():
            if k not in supplied:
                if spec.required:
                    raise ToolError(f"missing parameter {k!r}")
                continue
            out[k] = spec.check(k, supplied[k])
        return out

    def render(self, params: dict[str, Any]) -> str:
        """Full PowerShell text for a real transport. Params travel as base64 JSON."""
        b64 = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
        pre = ("$ErrorActionPreference='Stop'\n$ProgressPreference='SilentlyContinue'\n"
               f"$P=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}'))|ConvertFrom-Json\n")
        return pre + self.script


REGISTRY: dict[str, Tool] = {}


def _reg(t: Tool) -> None:
    REGISTRY[t.id] = t


def diag(id_: str, title: str, script: str, timeout_s: int = 60) -> None:
    _reg(Tool(id_, title, Risk.READ_ONLY, "diagnostic", script.strip() + " | ConvertTo-Json -Depth 5 -Compress", timeout_s))


# ---------------------------------------------------------------- read-only diagnostics
diag("cpu_ram", "CPU and memory", r"""
$os=Get-CimInstance Win32_OperatingSystem
$cpu=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average
$tot=[math]::Round($os.TotalVisibleMemorySize/1MB,2);$free=[math]::Round($os.FreePhysicalMemory/1MB,2)
[pscustomobject]@{cpu_percent=[int]$cpu;ram_total_gb=$tot;ram_used_gb=[math]::Round($tot-$free,2);ram_percent=[int](100*($tot-$free)/$tot)}""")
diag("performance", "Windows performance", r"""
$os=Get-CimInstance Win32_OperatingSystem
$top=Get-Process|Sort-Object WorkingSet64 -Descending|Select-Object -First 5|ForEach-Object{[pscustomobject]@{name=$_.ProcessName;ws_mb=[int]($_.WorkingSet64/1MB)}}
$rb=Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
[pscustomobject]@{uptime_hours=[int]((Get-Date)-$os.LastBootUpTime).TotalHours;pending_reboot=$rb;top_processes=@($top)}""")
diag("startup", "Startup applications", r"""
$i=Get-CimInstance Win32_StartupCommand|ForEach-Object{[pscustomobject]@{name=$_.Name;command=$_.Command;location=$_.Location}}
[pscustomobject]@{items=@($i)}""")
diag("disk_health", "Disk health / SMART", r"""
$d=Get-PhysicalDisk|ForEach-Object{[pscustomobject]@{model=$_.FriendlyName;health=[string]$_.HealthStatus;operational=[string]$_.OperationalStatus;media=[string]$_.MediaType}}
$s=$null;try{$s=@(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus|Where-Object{$_.PredictFailure}).Count -gt 0}catch{}
[pscustomobject]@{disks=@($d);smart_predict_failure=$s}""")
diag("windows_update", "Windows Update", r"""
$svc=@{};foreach($n in 'wuauserv','bits'){$svc[$n]=[string](Get-Service $n).Status}
$h=Get-HotFix|Sort-Object InstalledOn -Descending|Select-Object -First 1
$days=if($h.InstalledOn){[int]((Get-Date)-$h.InstalledOn).TotalDays}else{$null}
$f=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WindowsUpdateClient';Id=20} -MaxEvents 5 -ErrorAction SilentlyContinue|ForEach-Object{[pscustomobject]@{message=$_.Message;date=$_.TimeCreated}})
[pscustomobject]@{services=$svc;last_update_days=$days;failures=@($f)}""", 90)
diag("drivers", "Drivers and Device Manager", r"""
$p=Get-CimInstance Win32_PnPEntity|Where-Object{$_.ConfigManagerErrorCode -ne 0}|ForEach-Object{[pscustomobject]@{name=$_.Name;class=$_.PNPClass;error_code=$_.ConfigManagerErrorCode}}
[pscustomobject]@{problem_devices=@($p)}""")
diag("network", "Network connectivity", r"""
$a=Get-NetIPConfiguration|Where-Object{$_.NetAdapter.Status -eq 'Up'}|ForEach-Object{[pscustomobject]@{name=$_.InterfaceAlias;ip=[string]($_.IPv4Address.IPAddress);gateway=[string]($_.IPv4DefaultGateway.NextHop)}}
$gw=@($a)[0].gateway
$g=if($gw){(Test-Connection $gw -Count 2 -Quiet)}else{$false}
$i=Test-Connection 1.1.1.1 -Count 2 -Quiet
$d=try{[bool](Resolve-DnsName example.com -ErrorAction Stop)}catch{$false}
[pscustomobject]@{adapters=@($a);gateway_reachable=$g;internet_reachable=$i;dns_resolves=$d}""", 90)
diag("dns_dhcp", "DNS and DHCP", r"""
$a=Get-NetIPConfiguration|Where-Object{$_.NetAdapter.Status -eq 'Up'}|ForEach-Object{$ip=[string]($_.IPv4Address.IPAddress);[pscustomobject]@{name=$_.InterfaceAlias;dhcp=($_.NetIPv4Interface.Dhcp -eq 'Enabled');ip=$ip;apipa=$ip.StartsWith('169.254.');dns=@($_.DNSServer.ServerAddresses)}}
$d=try{[bool](Resolve-DnsName example.com -ErrorAction Stop)}catch{$false}
[pscustomobject]@{adapters=@($a);dns_resolves=$d}""", 60)
diag("printers", "Printers and spooler", r"""
$sp=[string](Get-Service Spooler).Status
$p=Get-Printer|ForEach-Object{$j=@(Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue).Count;[pscustomobject]@{name=$_.Name;status=[string]$_.PrinterStatus;jobs=$j}}
[pscustomobject]@{spooler=$sp;printers=@($p)}""")
diag("event_log", "Windows Event Log (24h)", r"""
$e=@(Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;StartTime=(Get-Date).AddHours(-24)} -MaxEvents 500 -ErrorAction SilentlyContinue)
$top=$e|Group-Object ProviderName|Sort-Object Count -Descending|Select-Object -First 5|ForEach-Object{[pscustomobject]@{source=$_.Name;count=$_.Count;sample=($_.Group[0].Message -split "`n")[0]}}
[pscustomobject]@{critical=@($e|Where-Object Level -eq 1).Count;errors=@($e|Where-Object Level -eq 2).Count;top_sources=@($top)}""", 120)
diag("integrity", "System integrity", r"""
$mp=Get-MpComputerStatus
$dism=(& dism.exe /Online /Cleanup-Image /CheckHealth) -join ' '
$h=if($dism -match 'No component store corruption'){'healthy'}elseif($dism -match 'repairable'){'repairable'}else{'unknown'}
[pscustomobject]@{dism_checkhealth=$h;defender_realtime=[bool]$mp.RealTimeProtectionEnabled;signature_age_days=[int]$mp.AntivirusSignatureAge}""", 180)
diag("software", "Installed software", r"""
$k='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
$a=Get-ItemProperty $k -ErrorAction SilentlyContinue|Where-Object DisplayName|ForEach-Object{[pscustomobject]@{name=$_.DisplayName;version=$_.DisplayVersion;publisher=$_.Publisher}}
[pscustomobject]@{apps=@($a)}""", 90)
diag("storage", "Storage usage", r"""
$v=Get-Volume|Where-Object{$_.DriveLetter -and $_.DriveType -eq 'Fixed'}|ForEach-Object{[pscustomobject]@{drive=[string]$_.DriveLetter;size_gb=[math]::Round($_.Size/1GB,1);free_gb=[math]::Round($_.SizeRemaining/1GB,1)}}
$t=[math]::Round((Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue|Measure-Object Length -Sum).Sum/1MB)
[pscustomobject]@{volumes=@($v);temp_mb=[int]$t}""", 180)

# ---------------------------------------------------------------- repairs
def repair(id_, title, risk, script, verify_module, verify_check, params=None, timeout_s=120):
    _reg(Tool(id_, title, risk, "repair", script.strip(), timeout_s, params or {}, verify_module, verify_check))


repair("flush_dns", "Flush DNS resolver cache", Risk.LOW, "Clear-DnsClientCache;'ok'|ConvertTo-Json", "dns_dhcp", "dns_ok")
repair("renew_dhcp", "Renew DHCP lease", Risk.LOW, "ipconfig /renew|Out-Null;'ok'|ConvertTo-Json", "dns_dhcp", "no_apipa", timeout_s=60)
repair("restart_spooler", "Restart Print Spooler", Risk.LOW, "Restart-Service Spooler -Force;'ok'|ConvertTo-Json", "printers", "spooler_running")
repair("clear_print_queue", "Clear stuck print jobs", Risk.LOW,
       "Stop-Service Spooler -Force;Remove-Item \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" -Force -ErrorAction SilentlyContinue;Start-Service Spooler;'ok'|ConvertTo-Json",
       "printers", "no_stuck_jobs")
repair("restart_update_services", "Restart Windows Update services", Risk.LOW,
       "foreach($n in 'bits','wuauserv'){Restart-Service $n -Force};'ok'|ConvertTo-Json", "windows_update", "update_services_running")
repair("disable_startup_item", "Disable a startup application", Risk.LOW,
       "Get-CimInstance Win32_StartupCommand|Where-Object Name -eq $P.name|ForEach-Object{$loc=$_.Location;if($loc -match 'Run'){$k=($loc -replace '^HKU\\\\','Registry::HKEY_USERS\\' -replace '^HKLM\\\\','HKLM:\\' );Remove-ItemProperty -Path $k -Name $P.name -ErrorAction Stop}};'ok'|ConvertTo-Json",
       "startup", "startup_item_gone", {"name": Param("name")})
repair("run_dism_restorehealth", "Repair component store (DISM)", Risk.LOW,
       "& dism.exe /Online /Cleanup-Image /RestoreHealth|Out-Null;'ok'|ConvertTo-Json", "integrity", "dism_healthy", timeout_s=1800)
repair("clear_temp_files", "Delete temporary files", Risk.HIGH,
       "Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue|Remove-Item -Recurse -Force -ErrorAction SilentlyContinue;'ok'|ConvertTo-Json",
       "storage", "temp_reduced", timeout_s=300)
repair("reset_winsock", "Reset Winsock / network stack", Risk.HIGH,
       "netsh winsock reset|Out-Null;netsh int ip reset|Out-Null;'ok'|ConvertTo-Json", "network", "internet_ok", timeout_s=60)
repair("uninstall_software", "Uninstall a program", Risk.HIGH,
       "$app=Get-Package -Name $P.name -ErrorAction Stop;$app|Uninstall-Package -Force|Out-Null;'ok'|ConvertTo-Json",
       "software", "software_gone", {"name": Param("name")}, timeout_s=600)
repair("enable_defender_realtime", "Enable Defender real-time protection", Risk.HIGH,
       "Set-MpPreference -DisableRealtimeMonitoring $false;'ok'|ConvertTo-Json", "integrity", "defender_on")

# ---------------------------------------------------------------- restricted console
CONSOLE_CMDLETS = {
    "Get-Process", "Get-Service", "Get-NetAdapter", "Get-NetIPConfiguration", "Get-Disk", "Get-Volume",
    "Get-PhysicalDisk", "Get-HotFix", "Get-Printer", "Get-ComputerInfo", "Get-Date", "Get-Uptime",
}
_CONSOLE_RE = re.compile(r"^(Get-[A-Za-z]+)((?: -[A-Za-z]+(?: [A-Za-z0-9_.*:\-]+)?)*)$")


def validate_console(cmd: str) -> str:
    """Only a single allowlisted Get-* cmdlet with simple switch/value tokens. No pipes, quotes, $, ;, &, `."""
    cmd = (cmd or "").strip()
    if len(cmd) > 200:
        raise ToolError("command too long")
    m = _CONSOLE_RE.match(cmd)
    if not m or m.group(1) not in CONSOLE_CMDLETS:
        raise ToolError(f"only these read-only cmdlets are allowed: {sorted(CONSOLE_CMDLETS)}")
    return cmd


def console_script(cmd: str) -> str:
    return f"{validate_console(cmd)}|Select-Object -First 100|Out-String -Width 200"


def get(tool_id: str) -> Tool:
    try:
        return REGISTRY[tool_id]
    except KeyError:
        raise ToolError(f"tool {tool_id!r} is not in the allowlist") from None
