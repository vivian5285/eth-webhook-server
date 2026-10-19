import sys, os; sys.path.insert(0, ".")
from engine import optimal as O
sc = O.stock_leg()[0]; ks = sorted(sc)
print("seed", os.environ.get("PYTHONHASHSEED"), "stock_final", round(sc[ks[-1]], 6))
