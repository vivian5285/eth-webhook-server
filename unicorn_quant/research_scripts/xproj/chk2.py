from xvar import *
s1=B["sc"]; s2=stock()
ds=[d for d in s1 if d in s2 and abs(s1[d]/s2[d]-1)>1e-9]
print(len(s1),len(s2),ds[:3], O.dstr(ds[0]) if ds else None, max(s1), max(s2))
