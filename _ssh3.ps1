$env:Path = "C:\Windows\System32\OpenSSH\;$env:Path"
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes root@187.77.130.144 "date"
