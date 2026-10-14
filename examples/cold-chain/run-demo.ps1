# Runs the cold-chain experiment on Windows. The first run creates the Python
# environment, the gate's signing key and the three credentials; later runs
# reuse them. Needs Docker Desktop and Python 3.10 or later.
#
#   .\run-demo.ps1            run straight through
#   .\run-demo.ps1 --pause    wait for Enter between parts
#   .\run-demo.ps1 --revoke   add part 2e: the operator revokes the platform's role
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Here '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    python -m venv (Join-Path $Here '.venv')
    & $Py -m pip install -q -r (Join-Path $Here 'requirements.txt')
}
$Host.UI.RawUI.WindowTitle = 'Archytan Lite cold-chain experiment'
& $Py (Join-Path $Here 'setup_gate.py')
& $Py (Join-Path $Here 'demo.py') @args
