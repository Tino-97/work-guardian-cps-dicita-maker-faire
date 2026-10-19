<#
.SYNOPSIS
    Prepara questo PC come stazione centrale della demo di WORK-GUARDIAN.

.DESCRIPTION
    Installa cio' che manca e configura la rete per l'orologio:
      1. Python 3.12, la versione di riferimento del progetto;
      2. le librerie di requirements.txt;
      3. PyTorch per la GPU, se c'e' una scheda NVIDIA;
      4. il broker MQTT Mosquitto, con il servizio di Windows su avvio manuale
         (ascolta solo il PC stesso: il broker lo avvia la demo);
      5. la rete: l'indirizzo che l'orologio usa per il broker (MQTT_BROKER_IP
         in wearable\smartwatch\network_config.h) sulla scheda Ethernet
         collegata al router, e il firewall per broker, drone e Python;
      6. la verifica che tutte le librerie si importino.

    Quello che e' gia' a posto viene saltato: lo script si puo' rieseguire
    senza danni, per esempio dopo aver collegato il cavo Ethernet.
    Con -Ripristina la scheda Ethernet torna a ricevere l'indirizzo dal router.

.EXAMPLE
    Doppio clic su demo\prepara_pc.bat (chiede i permessi di amministratore).
    Per riportare la scheda all'uso normale: demo\prepara_pc.bat ripristina
#>
[CmdletBinding()]
param(
    [switch]$Ripristina,
    [string]$Scheda,
    [string]$Indirizzo,
    [switch]$Pausa
)

# Continue: pip e python scrivono avvisi su stderr che con "Stop" PowerShell 5
# tratterebbe come errori. I comandi che devono fermare lo script hanno
# -ErrorAction Stop.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # download molto piu' veloci
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$MOSQUITTO_URL = "https://mosquitto.org/files/binary/win64/mosquitto-2.1.2-install-windows-x64.exe"
$MOSQUITTO_EXE = "C:\Program Files\mosquitto\mosquitto.exe"

function Fine([int]$codice) {
    if ($Pausa) { [void](Read-Host "Premere Invio per chiudere") }
    exit $codice
}
function Titolo([string]$testo) { Write-Host ""; Write-Host $testo -ForegroundColor Cyan }
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

$radice = Split-Path -Parent $PSScriptRoot

# Indirizzo del broker, letto dal firmware dell'orologio.
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

# Scheda Ethernet collegata al router (nessuna: $null).
function TrovaScheda {
    if ($Scheda) {
        $nic = Get-NetAdapter -Name $Scheda -ErrorAction SilentlyContinue
        if (-not $nic) { Errore "Scheda '$Scheda' non trovata. Schede presenti: $((Get-NetAdapter).Name -join ', ')" }
        return $nic
    }
    $cablate = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.MediaType -eq "802.3" })
    if ($cablate.Count -le 1) { return $cablate | Select-Object -First 1 }
    $sulRouter = $cablate | Where-Object {
        (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress -like "$rete.*"
    } | Select-Object -First 1
    if (-not $sulRouter) {
        Errore "Ci sono piu' schede Ethernet collegate ($($cablate.Name -join ', ')): indicare quella del router con -Scheda ""Nome""."
    }
    return $sulRouter
}

Write-Host ""
Write-Host "WORK-GUARDIAN - preparazione della stazione centrale" -ForegroundColor Cyan

if ($Ripristina) {
    $nic = TrovaScheda
    if (-not $nic) { Errore "Nessuna scheda Ethernet collegata." }
    $indice = $nic.ifIndex
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

$requisiti = Join-Path $radice "requirements.txt"
if (-not (Test-Path $requisiti)) {
    Errore "requirements.txt non trovato in $radice : estrarre tutto il progetto, non solo la cartella demo."
}
$temp = Join-Path $env:TEMP "workguardian"
New-Item -ItemType Directory -Force -Path $temp | Out-Null

function Scarica([string]$url) {
    $file = Join-Path $temp (Split-Path -Leaf $url)
    if (-not (Test-Path $file)) {
        Info "Download di $(Split-Path -Leaf $url)..."
        try {
            Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing -ErrorAction Stop
        } catch {
            Remove-Item $file -ErrorAction SilentlyContinue
            Errore "Download non riuscito ($url): controllare la connessione a Internet."
        }
    }
    return $file
}

# 1. Python 3.12 ---------------------------------------------------------------
Titolo "1/6  Python 3.12"
function TrovaPy {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fisso = Join-Path $env:SystemRoot "py.exe"
    if (Test-Path $fisso) { return $fisso }
    return $null
}
function HaPython312 {
    $py = TrovaPy
    if (-not $py) { return $false }
    & $py -3.12 --version *> $null
    return ($LASTEXITCODE -eq 0)
}
if (HaPython312) {
    Ok "Python 3.12 gia' installato."
} else {
    $setup = Scarica $PYTHON_URL
    Info "Installazione di Python 3.12 (qualche minuto)..."
    $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_launcher=1", "Include_test=0")
    if ($p.ExitCode -ne 0 -or -not (HaPython312)) {
        Errore "Installazione di Python non riuscita (codice $($p.ExitCode)). Installarlo a mano da python.org (3.12) e rieseguire."
    }
    Ok "Python 3.12 installato."
}
$py = TrovaPy
$pythonExe = "$(& $py -3.12 -c 'import sys; print(sys.executable)')".Trim()
function Pip([string[]]$argomenti) {
    # Out-Host: l'output di pip va a schermo e non nel valore restituito.
    & $py -3.12 -m pip @argomenti --disable-pip-version-check --no-warn-script-location | Out-Host
    return ($LASTEXITCODE -eq 0)
}
function GpuAttiva {
    & $py -3.12 -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" *> $null
    return ($LASTEXITCODE -eq 0)
}

# 2. Librerie del progetto -------------------------------------------------------
Titolo "2/6  Librerie Python (requirements.txt)"
[void](Pip @("install", "--upgrade", "pip", "--quiet"))
Info "Installazione delle librerie: con PyTorch servono alcuni minuti, anche senza nuove scritte."
if (-not (Pip @("install", "-r", $requisiti))) {
    Errore "pip non e' riuscito a installare le librerie: leggere l'errore qui sopra."
}
Ok "Librerie installate."

# 3. PyTorch per la GPU ----------------------------------------------------------
Titolo "3/6  Scheda video"
$nvidia = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
if (-not $nvidia) {
    Avviso "Nessuna scheda NVIDIA: il riconoscimento girera' su CPU (usare --imgsz 480 se e' lento)."
} elseif (GpuAttiva) {
    Ok "PyTorch usa gia' la GPU ($($nvidia.Name))."
} else {
    Info "Scheda: $($nvidia.Name)"
    # Stesse righe di requirements.txt (con o senza versione), prese
    # dall'indice di PyTorch per CUDA.
    $versioni = @(Get-Content $requisiti | ForEach-Object { ($_ -split '#')[0].Trim() } |
        Where-Object { $_ -match '^(torch|torchvision)\b' })
    if ($versioni.Count -eq 0) { $versioni = @("torch", "torchvision") }
    foreach ($cuda in @("cu126", "cu128")) {
        Info "Installazione di PyTorch per GPU ($cuda)..."
        if (Pip (@("install", "--force-reinstall", "--no-deps") + $versioni +
                 @("--index-url", "https://download.pytorch.org/whl/$cuda"))) { break }
    }
    if (GpuAttiva) {
        Ok "PyTorch usa la GPU."
    } else {
        Avviso "PyTorch non vede la GPU: aggiornare il driver NVIDIA e rieseguire. La demo funziona comunque su CPU."
    }
}

# 4. Mosquitto -------------------------------------------------------------------
Titolo "4/6  Broker MQTT Mosquitto"
if ((Test-Path $MOSQUITTO_EXE) -or (Get-Command mosquitto -ErrorAction SilentlyContinue)) {
    Ok "Mosquitto gia' installato."
} else {
    $setup = Scarica $MOSQUITTO_URL
    Info "Installazione di Mosquitto..."
    $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList "/S"
    if (-not (Test-Path $MOSQUITTO_EXE)) {
        Errore "Installazione di Mosquitto non riuscita (codice $($p.ExitCode)). Installarlo a mano da mosquitto.org nella cartella predefinita."
    }
    Ok "Mosquitto installato."
}
$servizio = Get-Service -Name "mosquitto" -ErrorAction SilentlyContinue
if ($servizio) {
    if ($servizio.Status -eq "Running") { Stop-Service -Name "mosquitto" -Force }
    Set-Service -Name "mosquitto" -StartupType Manual
    Ok "Servizio Mosquitto fermo e su avvio manuale: il broker lo avvia la demo."
}

# 5. Rete e firewall -------------------------------------------------------------
Titolo "5/6  Rete dell'orologio e firewall"
$nic = TrovaScheda
if (-not $nic) {
    Avviso "Nessuna scheda Ethernet collegata: collegare il cavo a una porta LAN del router e rieseguire prepara_pc.bat."
} else {
    $indice = $nic.ifIndex
    Info "Scheda Ethernet: $($nic.Name) - $($nic.InterfaceDescription)"
    $attuali = @(Get-NetIPAddress -InterfaceIndex $indice -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    $dhcp = (Get-NetIPInterface -InterfaceIndex $indice -AddressFamily IPv4).Dhcp
    if (($attuali.IPAddress -contains $Indirizzo) -and ("$dhcp" -eq "Disabled")) {
        Ok "La scheda ha gia' l'indirizzo fisso $Indirizzo."
    } else {
        if (($attuali.IPAddress -notcontains $Indirizzo) -and (Test-Connection $Indirizzo -Count 1 -Quiet -ErrorAction SilentlyContinue)) {
            Errore "L'indirizzo $Indirizzo risponde gia' sulla rete: un altro PC fa da stazione centrale. Scollegarlo dal router, oppure eseguire su di lui prepara_pc.bat ripristina."
        }
        try {
            $attuali | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
            Get-NetRoute -InterfaceIndex $indice -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
                Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
            # Senza gateway: la rete del router serve solo a PC, orologio e drone.
            Set-NetIPInterface -InterfaceIndex $indice -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop
            New-NetIPAddress -InterfaceIndex $indice -AddressFamily IPv4 -IPAddress $Indirizzo -PrefixLength 24 -ErrorAction Stop | Out-Null
        } catch {
            Errore "Impossibile assegnare $Indirizzo alla scheda $($nic.Name): $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 3
        $stato = (Get-NetIPAddress -InterfaceIndex $indice -IPAddress $Indirizzo -ErrorAction SilentlyContinue).AddressState
        if ("$stato" -eq "Duplicate") {
            Errore "Windows segnala che $Indirizzo e' gia' usato da un altro dispositivo della rete."
        }
        Ok "Indirizzo $Indirizzo assegnato alla scheda $($nic.Name)."
    }
}

function Regola([string]$nome, [hashtable]$parametri) {
    if (-not (Get-NetFirewallRule -DisplayName $nome -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $nome -Direction Inbound -Action Allow -Profile Any @parametri | Out-Null
    }
}
Regola "WorkGuardian - Mosquitto" @{ Protocol = "TCP"; LocalPort = 1883; RemoteAddress = "$rete.0/24" }
Regola "WorkGuardian - Tello" @{ Protocol = "UDP"; LocalPort = @(8890, 11111); RemoteAddress = "192.168.10.1" }
Regola "WorkGuardian - Tello sul router" @{ Protocol = "UDP"; LocalPort = @(8890, 11111); RemoteAddress = "$rete.0/24" }
# Alla prima esecuzione Windows chiede se consentire Python: una risposta data
# in fretta crea regole di blocco, che vincono su quelle di apertura e tagliano
# fuori il drone (la rete TELLO e' considerata pubblica).
$blocchi = @(Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue | Where-Object {
    ($_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program -match '\\python[w]?(3[0-9.]*)?\.exe$'
})
if ($blocchi.Count -gt 0) { $blocchi | Remove-NetFirewallRule }
if ($pythonExe) { Regola "WorkGuardian - Python ($pythonExe)" @{ Program = $pythonExe } }
Ok "Firewall aperto per broker, drone e Python."

# 6. Verifica ----------------------------------------------------------------------
Titolo "6/6  Verifica"
$verifica = "import cv2, numpy, ultralytics, torch, pyapriltags, djitellopy, paho.mqtt, pygame, PIL; " +
    "print('GPU' if torch.cuda.is_available() else 'CPU')"
$esito = & $py -3.12 -c $verifica 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ($esito | Out-String)
    Errore "Una libreria non si importa: leggere l'errore qui sopra."
}
Ok "Tutte le librerie si importano. Riconoscimento su $(($esito | Select-Object -Last 1))."

Write-Host ""
Ok "PC pronto."
Info "Avvio della demo:    demo\avvia_demo.bat"
Info "Chiusura missione:   demo\chiudi_missione.bat"
Info "Uso normale del PC:  demo\prepara_pc.bat ripristina"
Write-Host ""
Fine 0
