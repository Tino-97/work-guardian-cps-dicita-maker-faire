<#
.SYNOPSIS
    Installa su questo PC tutto cio' che serve alla demo di WORK-GUARDIAN.

.DESCRIPTION
    Pensato per preparare in pochi minuti un PC nuovo come stazione centrale.
    Lo script:
      1. installa Python 3.12, la versione di riferimento del progetto, se manca;
      2. installa le librerie di requirements.txt;
      3. se c'e' una scheda NVIDIA, installa PyTorch per la GPU;
      4. installa il broker MQTT Mosquitto, se manca;
      5. controlla che tutto si importi;
      6. se si vuole, prepara la rete con configura_pc.ps1.

    Le installazioni gia' presenti vengono lasciate come sono: lo script si
    puo' rieseguire senza danni.

.EXAMPLE
    Doppio clic su demo\installa_pc.bat: chiede i permessi di amministratore.
#>
[CmdletBinding()]
param(
    [switch]$SenzaRete,
    [switch]$Pausa
)

# Continue: i programmi esterni (pip, python) scrivono avvisi su stderr che con
# "Stop" PowerShell 5 tratterebbe come errori. I comandi che devono fermare lo
# script hanno -ErrorAction Stop.
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
    if ($SenzaRete) { $argomenti += "-SenzaRete" }
    try {
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argomenti
    } catch {
        Write-Host "Servono i permessi di amministratore: richiesta annullata." -ForegroundColor Red
    }
    exit
}

$radice = Split-Path -Parent $PSScriptRoot
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
            Errore "Download non riuscito ($url): controllare la connessione a Internet."
        }
    }
    return $file
}

Write-Host ""
Write-Host "WORK-GUARDIAN - installazione della demo" -ForegroundColor Cyan

# 1. Python 3.12 -------------------------------------------------------------
Titolo "1/5  Python 3.12"
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
function Pip([string[]]$argomenti) {
    # Out-Host: l'output di pip va a schermo e non nel valore restituito.
    & $py -3.12 -m pip @argomenti | Out-Host
    return ($LASTEXITCODE -eq 0)
}

# 2. Librerie del progetto ----------------------------------------------------
Titolo "2/5  Librerie Python (requirements.txt)"
[void](Pip @("install", "--upgrade", "pip", "--quiet", "--disable-pip-version-check", "--no-warn-script-location"))
Info "Installazione delle librerie: con PyTorch servono alcuni minuti..."
if (-not (Pip @("install", "-r", $requisiti, "--disable-pip-version-check", "--no-warn-script-location"))) {
    Errore "pip non e' riuscito a installare le librerie: leggere l'errore qui sopra."
}
Ok "Librerie installate."

# 3. PyTorch per la GPU ------------------------------------------------------
Titolo "3/5  Scheda video"
$nvidia = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
if (-not $nvidia) {
    Avviso "Nessuna scheda NVIDIA: il riconoscimento girera' su CPU (usare --imgsz 480 se e' lento)."
} else {
    Info "Scheda: $($nvidia.Name)"
    & $py -3.12 -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
        Ok "PyTorch usa gia' la GPU."
    } else {
        # Stesse righe di requirements.txt (con o senza versione), prese
        # dall'indice di PyTorch per CUDA.
        $versioni = @(Get-Content $requisiti | ForEach-Object { ($_ -split '#')[0].Trim() } |
            Where-Object { $_ -match '^(torch|torchvision)\b' })
        if ($versioni.Count -eq 0) { $versioni = @("torch", "torchvision") }
        $installato = $false
        foreach ($cuda in @("cu126", "cu128")) {
            Info "Installazione di PyTorch per GPU ($cuda)..."
            $argomenti = @("install", "--force-reinstall", "--no-deps", "--disable-pip-version-check", "--no-warn-script-location") + $versioni +
                @("--index-url", "https://download.pytorch.org/whl/$cuda")
            if (Pip $argomenti) { $installato = $true; break }
        }
        if (-not $installato) {
            Info "Versioni del progetto non disponibili per GPU: installo le piu' recenti."
            $installato = Pip @("install", "--force-reinstall", "--disable-pip-version-check",
                "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu126")
        }
        & $py -3.12 -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            Ok "PyTorch usa la GPU."
        } else {
            Avviso "PyTorch non vede la GPU: aggiornare il driver NVIDIA e rieseguire. La demo funziona comunque su CPU."
        }
    }
}

# 4. Mosquitto -----------------------------------------------------------------
Titolo "4/5  Broker MQTT Mosquitto"
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
# Il servizio di Windows ascolta solo sul PC stesso: il broker lo avvia la
# demo con la configurazione del progetto.
$servizio = Get-Service -Name "mosquitto" -ErrorAction SilentlyContinue
if ($servizio) {
    if ($servizio.Status -eq "Running") { Stop-Service -Name "mosquitto" -Force }
    Set-Service -Name "mosquitto" -StartupType Manual
    Ok "Servizio Mosquitto fermo e su avvio manuale."
}

# 5. Verifica --------------------------------------------------------------------
Titolo "5/5  Verifica"
$verifica = "import cv2, numpy, ultralytics, torch, pyapriltags, djitellopy, paho.mqtt, pygame, PIL; " +
    "print('GPU' if torch.cuda.is_available() else 'CPU')"
$dispositivo = & $py -3.12 -c $verifica 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ($dispositivo | Out-String)
    Errore "Una libreria non si importa: leggere l'errore qui sopra."
}
Ok "Tutte le librerie si importano. Riconoscimento su $(($dispositivo | Select-Object -Last 1))."

# Rete ---------------------------------------------------------------------------
if (-not $SenzaRete) {
    Write-Host ""
    $risposta = Read-Host "Preparare ora la rete per l'orologio (cavo Ethernet collegato al router)? [S/n]"
    if ($risposta -eq "" -or $risposta -match '^[sSyY]') {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "configura_pc.ps1")
    } else {
        Info "Rete da preparare in seguito con demo\configura_pc.bat."
    }
}

Write-Host ""
Ok "Installazione completata."
Info "Avvio della demo:      doppio clic su demo\avvia_demo.bat"
Info "Chiusura missione:     doppio clic su demo\chiudi_missione.bat"
Write-Host ""
Fine 0
