@echo off
set PWD=w'tFzgg2vPZ0D,Z
set PLINK="C:\Program Files\PuTTY\plink.exe"
set HOST=root@187.77.130.144

echo === Step 1: Accept host key ===
echo y | %PLINK% -batch %HOST% "exit" 2>nul
echo Done accepting host key
echo.

echo === Step 2: Create nginx config file ===
(
  echo %PWD%
) | %PLINK% -batch %HOST% "cat > /etc/nginx/conf.d/binance-webhook.conf << 'NGINXEOF'
server {
    listen 80;
    server_name 187.77.130.144;
    
    location /binance/webhook {
        proxy_pass http://127.0.0.1:5003/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF
echo '--- File contents ---'
cat /etc/nginx/conf.d/binance-webhook.conf"
echo.

echo === Step 3: Test nginx config ===
(
  echo %PWD%
) | %PLINK% -batch %HOST% "nginx -t"
echo.

echo === Step 4: Reload nginx ===
(
  echo %PWD%
) | %PLINK% -batch %HOST% "nginx -s reload && echo RELOAD_OK || echo RELOAD_FAILED"
echo.

echo === Step 5: Test webhook ===
(
  echo %PWD%
) | %PLINK% -batch %HOST% "curl -s -X POST -H 'Content-Type: application/json' -d '{\"secret\":\"528586\",\"action\":\"PING\",\"ticker\":\"ETHUSDT\"}' http://127.0.0.1:5003/webhook"
echo.

echo === Step 6: Test via nginx proxy ===
(
  echo %PWD%
) | %PLINK% -batch %HOST% "curl -s -X POST -H 'Content-Type: application/json' -d '{\"secret\":\"528586\",\"action\":\"PING\",\"ticker\":\"ETHUSDT\"}' http://127.0.0.1/binance/webhook"
echo.
echo === All done ===
pause
