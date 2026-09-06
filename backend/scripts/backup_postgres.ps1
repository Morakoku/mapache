<#
LOOP-13 — Backup PostgreSQL (pg_dump) verificable.

Uso (desde backend/):
  powershell -ExecutionPolicy Bypass -File scripts/backup_postgres.ps1 [-TargetDir <dir>] [-Verify]

Por defecto escribe en $env:LOCALAPPDATA\MapacheBackups\pg\ con timestamp.
Con -Verify, restaura el dump en una BD temporal (crm_backup_verify) y comprueba
conteos, para demostrar que el backup realmente restaura.

RPO/RTO objetivo (documentado en LOOP-13):
  - RPO: 24 h (copia diaria). Subir a 1 h cuando el volumen lo justifique.
  - RTO: 1 h (restauración a una BD nueva + repunto de la app).

El script NO toca la BD de producción más que con pg_dump (lectura).
No se usan credenciales en claro: docker exec usa el usuario del contenedor.
#>

param(
    [string]$TargetDir = "$env:LOCALAPPDATA\MapacheBackups\pg",
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$Container = "crm-postgres"
$DbUser = "crm"
$DbName = "crm"

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DumpHost = Join-Path $TargetDir "${DbName}_$Timestamp.dump"
$DumpIn = "/tmp/${DbName}_$Timestamp.dump"

Write-Host "[backup] pg_dump $DbName -> $DumpHost"
docker exec $Container pg_dump -U $DbUser -d $DbName -Fc -f $DumpIn | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pg_dump falló (exit $LASTEXITCODE)" }

docker cp "${Container}:$DumpIn" $DumpHost | Out-Null
docker exec $Container rm -f $DumpIn | Out-Null

$Hash = (Get-FileHash -Algorithm SHA256 $DumpHost).Hash
$Size = (Get-Item $DumpHost).Length
$Manifest = Join-Path $TargetDir "MANIFEST.txt"
Add-Content -LiteralPath $Manifest -Value "$Hash  len=$Size  $((Split-Path $DumpHost -Leaf))"
Write-Host "[backup] OK $DumpHost ($Size bytes)"
Write-Host "[backup] SHA256=$Hash"

if ($Verify) {
    Write-Host "[verify] restaurando en crm_backup_verify ..."
    docker exec $Container dropdb -U $DbUser --if-exists crm_backup_verify | Out-Null
    docker exec $Container createdb -U $DbUser crm_backup_verify | Out-Null
    cmd /c "type `"$DumpHost`" | docker exec -i $Container pg_restore -U $DbUser -d crm_backup_verify --no-owner" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_restore falló (exit $LASTEXITCODE)" }
    $Real = docker exec $Container psql -U $DbUser -d $DbName -tAc "SELECT count(*) FROM companies"
    $Restored = docker exec $Container psql -U $DbUser -d crm_backup_verify -tAc "SELECT count(*) FROM companies"
    docker exec $Container dropdb -U $DbUser crm_backup_verify | Out-Null
    if ([int]$Real -eq [int]$Restored) {
        Write-Host "[verify] OK companies: real=$Real restaurado=$Restored"
    } else {
        throw "[verify] MISMATCH companies: real=$Real restaurado=$Restored"
    }
}

Write-Host "[backup] fin. MANIFEST: $Manifest"
