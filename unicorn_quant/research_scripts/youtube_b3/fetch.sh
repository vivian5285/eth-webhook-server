cd "$(dirname "$0")/subs"
while read who ids; do for v in $ids; do
  ls ${who}_${v}.* >/dev/null 2>&1 && continue
  yt-dlp --skip-download --write-auto-subs --write-subs --sub-langs "zh-Hans,zh-Hant,zh,zh-CN,zh-TW,en,en-orig,zh-Hans-orig,zh-orig" --sub-format vtt -o "${who}_${v}.%(ext)s" -- "$v" >/dev/null 2>&1
  sleep 4; ls ${who}_${v}.* >/dev/null 2>&1 && echo "ok $who $v" || echo "none $who $v"
done; done < ../pick.txt
