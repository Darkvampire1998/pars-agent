$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Install official Python 3.12 x64 first.' }
if (Test-Path '.\wheels') {
  & '.\.venv\Scripts\python.exe' -m pip install --no-index --find-links '.\wheels' -r requirements.txt
} else {
  & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Connector dependencies could not be installed.' }
if (-not (Test-Path '.\connector.json')) {
  $panel = Read-Host 'Panel HTTPS URL'
  $account = Read-Host 'Account ID from panel'
  $key = Read-Host 'Bridge key from panel' -AsSecureString
  $terminal = Read-Host 'Full path to broker terminal64.exe'
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    @{panel_url=$panel;account_id=$account;bridge_key=$plain;terminal_path=$terminal} | ConvertTo-Json | Set-Content -Encoding UTF8 '.\connector.json'
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $plain = $null
  }
}
# Restrict connector configuration and journals to this Windows user.
& icacls.exe $PSScriptRoot /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F"
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local configuration permissions.' }
& '.\.venv\Scripts\python.exe' connector.py --config connector.json
