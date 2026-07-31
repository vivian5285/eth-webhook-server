$pass = "w'tFzgg2vPZ0D,Z"
$cmd = "ps aux | grep gunicorn | grep -v grep"
$result = & "C:\Program Files\PuTTY\plink.exe" -batch -pw $pass root@187.77.130.144 $cmd
Write-Output $result
