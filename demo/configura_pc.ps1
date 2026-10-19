<#
.SYNOPSIS
    Prepara questo PC come stazione centrale di WORK-GUARDIAN.

.DESCRIPTION
    L'orologio cerca il broker MQTT a un indirizzo fisso scritto nel firmware
    (MQTT_BROKER_IP in wearable\smartwatch\network_config.h, oggi 192.168.1.2).
    Invece di riservare quell'indirizzo a un solo PC dal pannello del router,
    lo script lo assegna alla scheda Ethernet del PC su cui viene eseguito:
    cosi' qualunque PC collegato con il cavo al router puo' fare da stazione
    centrale, uno alla volta.

    Lo script:
      1. trova la scheda Ethernet collegata al router;
      2. controlla che l'indirizzo non sia gia' usato da un altro dispositivo;
      3. assegna l'indirizzo alla scheda, senza gateway: la rete del router
         serve solo a PC, orologio e drone;
      4. apre nel firewall la porta del broker (TCP 1883) e quelle della
         camera del drone (UDP 8890 e 11111);
      5. imposta il servizio Mosquitto su avvio manuale e lo ferma: il broker
         lo avvia la demo con la configurazione del progetto.

    Con -Ripristina la scheda torna a ricevere l'indirizzo dal router (DHCP).

.EXAMPLE
    Doppio clic su demo\configura_pc.bat: chiede i permessi di amministratore.

.EXAMPLE
    Da PowerShell, nella cartella del progetto:
        powershell -ExecutionPolicy Bypass -File demo\configura_pc.ps1
        powershell -ExecutionPolicy Bypass -File demo\configura_pc.ps1 -Ripristina
#>
[CmdletBinding()]
param(
    [switch]$Ripristina,
    [string]$Scheda,
    [string]$Indirizzo,
    [switch]$Pausa
)

$ErrorActionPreference = "Stop"

function Fine([int]$codice) {
    if ($Pausa) { [void](Read-Host "Premere Invio per chiudere") }
    exit $codice
}
function Info([string]$testo)   { Write-Host "  $testo" }
function Ok([string]$testo)     { Write-Host "  [OK] $testo" -ForegroundColor Green }
function Avviso([string]$testo) { Write-Host "  [!]  $testo" -ForegroundColor Yellow }
function Errore([string]$testo) { Write-Host "  [X]  $testo" -ForegroundColor Red; Fine 1 }

# Senza permessi di amministratore lo script si riavvia chiedendoli.
$identita = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $identita.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argomenti = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Pausa")
    if ($Ripristina) { $argomenti += "-Ripristina" }
    if ($Scheda)     { $argomenti += @("-Scheda", "`"$Scheda`"") }
    if ($Indirizzo)  { $argomenti += @("-Indirizzo", $Indirizzo) }
    try {
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argomenti
    } catch {
        Write-Host "Servono i permessi di amministratore: richiesta annullata." -ForegroundColor Red
    }
    exit
}

Write-Host ""
Write-Host "WORK-GUARDIAN - stazione centrale" -ForegroundColor Cyan
Write-Host ""

# Indirizzo del broker, letto dal firmware dell'orologio.
$radice = Split-Path -Parent $PSScriptRoot
if (-not $Indirizzo) {
    $Indirizzo = "192.168.1.2"
    $configurazione = Join-Path $radice "wearable\smartwatch\network_config.h"
    if (Test-Path $configurazione) {
        $trovato = Select-String -Path $configurazione -Pattern 'MQTT_BROKER_IP\s*=\s*"([0-9.]+)"' |
            Select-Object -First 1
        if ($trovato) { $Indirizzo = $trovato.Matches[0].Groups[1].Value }
    }
}
$rete = ($Indirizzo.Split(".")[0..2]) -join "."

# Scheda Ethernet collegata al router.
if ($Scheda) {
    $nic = Get-NetAdapter -Name $Scheda -ErrorAction SilentlyContinue
    if (-not $nic) {
        Errore "Scheda '$Scheda' non trovata. Schede presenti: $((Get-NetAdapter).Name -join ', ')"
    }
} else {
    $cablate = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.MediaType -eq "802.3" })
    if ($cablate.Count -eq 0) {
        Errore "Nessuna scheda Ethernet collegata: collegare il cavo dal PC a una porta LAN del router acceso e riprovare."
    }
    $nic = $cablate[0]
    if ($cablate.Count -gt 1) {
        $sulRouter = $cablate | Where-Object {
            (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress -like "$rete.*"
        } | Select-Object -First 1
        if (-not $sulRouter) {
            Errore "Ci sono piu' schede Ethernet collegate ($($cablate.Name -join ', ')): indicare quella del router con -Scheda ""Nome""."
        }
        $nic = $sulRouter
    }
}
$indice = $nic.ifIndex
Info "Scheda Ethernet: $($nic.Name) - $($nic.InterfaceDescription)"

if ($Ripristina) {
    Get-NetIPAddress -InterfaceIndex $indice -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetRoute -InterfaceIndex $indice -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceIndex $indice -AddressFamily IPv4 -Dhcp Enabled
    Set-DnsClientServerAddress -InterfaceIndex $indice -ResetServerAddresses
    Restart-NetAdapter -Name $nic.Name
    Ok "La scheda $($nic.Name) riceve di nuovo l'indirizzo dal router (DHCP)."
    Fine 0
}

# Indirizzo fisso del broker sulla scheda Ethernet.
$attuali = @(Get-NetIPAddress -InterfaceIndex $indice -AddressFamily IPv4 -ErrorAction SilentlyContinue)
$dhcp = (Get-NetIPInterface -InterfaceIndex $indice -AddressFamily IPv4).Dhcp
if (($attuali.IPAddress -contains $Indirizzo) -and ("$dhcp" -eq "Disabled")) {
    Ok "La scheda ha gia' l'indirizzo fisso $Indirizzo."
} else {
    if (($attuali.IPAddress -notcontains $Indirizzo) -and (Test-Connection $Indirizzo -Count 1 -Quiet -ErrorAction SilentlyContinue)) {
        Errore "L'indirizzo $Indirizzo risponde gia' sulla rete: un altro PC fa da stazione centrale. Scollegarlo dal router, oppure eseguire su di lui configura_pc.bat ripristina."
    }
    $attuali | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetRoute -InterfaceIndex $indice -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceIndex $indice -AddressFamily IPv4 -Dhcp Disabled
    New-NetIPAddress -InterfaceIndex $indice -AddressFamily IPv4 -IPAddress $Indirizzo -PrefixLength 24 | Out-Null
    Start-Sleep -Seconds 3
    $stato = (Get-NetIPAddress -InterfaceIndex $indice -IPAddress $Indirizzo -ErrorAction SilentlyContinue).AddressState
    if ("$stato" -eq "Duplicate") {
        Errore "Windows segnala che $Indirizzo e' gia' usato da un altro dispositivo della rete."
    }
    Ok "Indirizzo $Indirizzo assegnato alla scheda $($nic.Name)."
}

# Firewall: broker per l'orologio, video e telemetria del drone.
function Regola([string]$nome, [hashtable]$parametri) {
    if (Get-NetFirewallRule -DisplayName $nome -ErrorAction SilentlyContinue) {
        Ok "Regola firewall gia' presente: $nome"
    } else {
        New-NetFirewallRule -DisplayName $nome -Direction Inbound -Action Allow @parametri | Out-Null
        Ok "Regola firewall aggiunta: $nome"
    }
}
Regola "WorkGuardian - Mosquitto" @{ Protocol = "TCP"; LocalPort = 1883; RemoteAddress = "$rete.0/24" }
Regola "WorkGuardian - Tello" @{ Protocol = "UDP"; LocalPort = @(8890, 11111); RemoteAddress = "192.168.10.1" }
Regola "WorkGuardian - Tello sul router" @{ Protocol = "UDP"; LocalPort = @(8890, 11111); RemoteAddress = "$rete.0/24" }

# Alla prima esecuzione Windows chiede se consentire Python: chi risponde in
# fretta crea regole di blocco, che vincono su quelle di apertura e tagliano
# fuori il drone (la rete TELLO e' considerata pubblica). Si tolgono e si
# consente Python su tutte le reti.
$blocchi = @(Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue | Where-Object {
    ($_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program -match '\\python[w]?(3[0-9.]*)?\.exe$'
})
if ($blocchi.Count -gt 0) {
    $blocchi | Remove-NetFirewallRule
    Ok "Tolte $($blocchi.Count) regole firewall che bloccavano Python."
}
$pythonExe = @()
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    try {
        $percorso = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $percorso) { $pythonExe += "$percorso".Trim() }
    } catch { }
}
foreach ($exe in $pythonExe) {
    $nome = "WorkGuardian - Python ($exe)"
    if (-not (Get-NetFirewallRule -DisplayName $nome -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $nome -Direction Inbound -Action Allow -Program $exe -Profile Any | Out-Null
    }
    Ok "Python consentito nel firewall: $exe"
}
if ($pythonExe.Count -eq 0) { Avviso "Python 3.12 non trovato: eseguire prima demo\installa_pc.bat." }

# Mosquitto: il servizio di Windows ascolta solo sul PC stesso, quindi va
# fermato; il broker con la configurazione del progetto lo avvia la demo.
$servizio = Get-Service -Name "mosquitto" -ErrorAction SilentlyContinue
if ($servizio) {
    if ($servizio.Status -eq "Running") { Stop-Service -Name "mosquitto" -Force }
    Set-Service -Name "mosquitto" -StartupType Manual
    Ok "Servizio Mosquitto fermo e su avvio manuale."
}
if ((Test-Path "C:\Program Files\mosquitto\mosquitto.exe") -or (Get-Command mosquitto -ErrorAction SilentlyContinue)) {
    Ok "Mosquitto installato."
} else {
    Avviso "Mosquitto non e' installato: eseguire demo\installa_pc.bat."
}

Write-Host ""
Ok "Questo PC e' la stazione centrale: l'orologio trovera' il broker su $Indirizzo."
Info "Avvio della demo:   demo\avvia_demo.bat"
Info "Uso normale del PC: demo\configura_pc.bat ripristina"
Write-Host ""
Fine 0
