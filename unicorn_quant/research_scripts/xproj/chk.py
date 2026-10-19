from xlib import *
from xlib import _seg
B=load_base(); F=B["F"]
for a,z in [(START,H_SPLIT),(H_SPLIT,None),(H_SPLIT-1,None),(START,H_SPLIT-1)]:
    print(a,z,[round(x,3) for x in _seg(F,a,z)])
import json; print(sorted(set(O.TRADABLE_FALLBACK)^set(json.load(open(XP+"/tradable.json")))))
print(max(F), O.dstr(max(F)))
