"""Usage: python probe.py <dataset dir> <--path dir of the CLI run>
Offline recall probe of the saved WARM index (warm-python 0.6.4 wheel) against the Cohere 1M ground truth.
Same queries and ground truth as the CLI run (test.parquet, neighbors.parquet, k=100). rerank=ef unless the second column says otherwise."""
import numpy as np, pandas as pd, time, importlib.metadata as m
import writerslogic_warm as w
import sys
d=sys.argv[1].rstrip('/')+'/'  # directory with test.parquet and neighbors.parquet (Cohere 1M)
print('warm-python', m.version('warm-python'), flush=True)
q=np.stack(pd.read_parquet(d+'test.parquet')['emb'].values).astype('float32')
gt=pd.read_parquet(d+'neighbors.parquet')['neighbors_id'].values
ix=w.VectorIndex(metric='angular',degree=32,build_ef=300); ix.load(sys.argv[2]+'/vector_bench_test/index.warm')
ids=np.load(sys.argv[2]+'/vector_bench_test/ids.npy')
print('queries', len(q), 'k 100', flush=True)
print('ef rerank recall@100 single-thread-ms-per-query', flush=True)
for ef,rr in [(100,100),(120,120),(128,128),(150,150),(200,200),(300,300),(500,500),(800,800),(2000,2000),(200,100),(800,100)]:
    ix.set_query_arguments(ef=ef,rerank=rr)
    t=time.perf_counter(); r=0
    for i in range(len(q)):
        res=ids[ix.query(q[i],100)]
        r+=len(set(res.tolist())&set(gt[i][:100].tolist()))/100
    print(ef,rr,round(r/len(q),4),round((time.perf_counter()-t)/len(q)*1e3,3),flush=True)
