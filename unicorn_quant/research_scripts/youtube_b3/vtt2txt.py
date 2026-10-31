import re,glob,os,sys
for f in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),'subs','*.vtt'))):
    out=re.sub(r'\.[A-Za-z-]+\.vtt$','.txt',f)
    if os.path.exists(out): continue
    seen=[]
    for l in open(f,encoding='utf-8'):
        l=re.sub(r'<[^>]+>','',l).strip()
        if not l or '-->' in l or l.startswith(('WEBVTT','Kind:','Language:')): continue
        if seen and seen[-1]==l: continue
        seen.append(l)
    open(out,'w',encoding='utf-8').write(' '.join(seen))
