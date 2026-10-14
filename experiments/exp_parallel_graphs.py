"""Do writes to different graphs run in parallel, while writes to one graph serialise?"""
import time
from concurrent.futures import ThreadPoolExecutor

from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)
# A deliberately heavy write: create 2000 nodes and link them in one query.
WRITE = ("UNWIND range(1, 2000) AS i CREATE (:N {i:i, w:$w}) "
         "WITH count(*) AS c MATCH (n:N {w:$w}) RETURN count(n)")


def bench(n_graphs, n_writes=64, workers=16):
    graphs = [db.select_graph(f"exp_par_{i}") for i in range(n_graphs)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda k: graphs[k % n_graphs].query(WRITE, {"w": k}), range(n_writes)))
    dt = time.perf_counter() - t0
    for g in graphs:
        g.delete()
    return dt


for n in (1, 4, 16):
    dt = bench(n)
    print(f"{n:>2} graph(s): 64 heavy writes / 16 threads in {dt:.2f}s ({64/dt:.1f} writes/s)")
