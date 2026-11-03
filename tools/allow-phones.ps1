# Lets phones on your Wi-Fi reach DiQuizBuzz running on THIS laptop.
# It adds one Windows Firewall rule: allow incoming TCP connections on the DiQuizBuzz port (default 8001).
# Windows will ask you to approve running as administrator. Undo it any time with:
#     Remove-NetFirewallRule -DisplayName "DiQuizBuzz 8001"
param([int]$Port = 8001)

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Asking Windows for administrator approval (click Yes)..."
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port"
    exit
}

$name = "DiQuizBuzz $Port"
Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private, Public | Out-Null

$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254)' -and $_.InterfaceAlias -notmatch 'vEthernet|Ethernet 2|VirtualBox|VMware' }
Write-Host ""
Write-Host "Done. Phones on the same Wi-Fi can now reach DiQuizBuzz on port $Port." -ForegroundColor Green
Write-Host "Test on the phone:  http://$($ips[0].IPAddress):$Port/healthz   (it should show {""ok"":true})"
Write-Host ""
Read-Host "Press Enter to close"
