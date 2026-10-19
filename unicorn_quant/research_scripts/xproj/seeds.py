from xvar import *
st = stock(); F, cB, _ = assemble(scv=st); s = S(F); print("seed-run", s, "stock", S(st)["r"])
