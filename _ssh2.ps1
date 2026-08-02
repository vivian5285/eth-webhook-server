$env:Path = "C:\Windows\System32\OpenSSH\;$env:Path"
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@187.77.130.144 "date; echo OK"
