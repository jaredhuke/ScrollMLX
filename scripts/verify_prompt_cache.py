"""In-process verification of the prompt-prefix cache (no server). Loads a small model once,
runs two turns sharing a prefix, and asserts: (1) warm turn first-token is much faster,
(2) cached output is byte-identical to the uncached baseline (greedy), (3) reset paths hold."""
import os, time
os.environ.setdefault("SCROLL_PROMPT_CACHE", "1")
import server.agent as A
from server.schemas import TokenEvent

MODEL = os.environ.get("MLX_VERIFY_MODEL", "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")
t = time.time(); A.load_model(MODEL, "primary"); print(f"loaded {MODEL} in {time.time()-t:.1f}s")


def run(messages, cache_on=True, mt=70):
    A.SCROLL_PROMPT_CACHE = bool(cache_on) and A.make_prompt_cache is not None
    t0 = time.time(); first = None; out = ""
    for ev in A.run_agent(messages=messages, cwd="/tmp", max_tokens=mt,
                          temperature=0.0, max_iterations=1, slot="primary", tools=False):
        if isinstance(ev, TokenEvent):
            if first is None:
                first = time.time() - t0
            out += ev.content
    return (first if first is not None else time.time()-t0), out


m1 = [{"role": "user", "content": "List three uses of a hash map. Be brief."}]
f1, o1 = run(m1, cache_on=True)
print(f"\nturn1 (cold): first-token {f1:.2f}s")
rec = A._prefix_cache.get("primary")
print("  cache committed:", rec is not None, "| offset==len(ids):", bool(rec) and rec['cache'][0].offset == len(rec['ids']))

m2 = m1 + [{"role": "assistant", "content": o1}, {"role": "user", "content": "Now add a fourth use, one line."}]
f2, o2 = run(m2, cache_on=True)
print(f"turn2 (warm): first-token {f2:.2f}s  | speedup {f1/max(f2,1e-6):.1f}x")
rec = A._prefix_cache.get("primary")
print("  invariant offset==len(ids):", bool(rec) and rec['cache'][0].offset == len(rec['ids']))

# correctness: cached output must equal the uncached baseline (greedy is deterministic)
A._prefix_cache.clear()
_, o2_nocache = run(m2, cache_on=False)
ident = (o2 == o2_nocache)
print(f"\ncorrectness — cached == uncached (byte-identical): {ident}")
if not ident:
    print("  cached:", repr(o2[:120])); print("  nocache:", repr(o2_nocache[:120]))

# reset path: a trimmed history (not a prefix of cached ids) must still complete coherently
A._prefix_cache.clear()
run(m1, cache_on=True)  # seed
m3 = [{"role": "user", "content": "Name two sorting algorithms."}]  # diverges right after the system block
f3, o3 = run(m3, cache_on=True)
rec = A._prefix_cache.get("primary")
print(f"\nreset/diverge path: completed, {len(o3)} chars, invariant:",
      bool(rec) and rec['cache'][0].offset == len(rec['ids']))

# kill switch: off => no records written, still works
A._prefix_cache.clear()
_, o4 = run(m1, cache_on=False)
print("kill-switch (cache off): output ok:", len(o4) > 0, "| no record:", A._prefix_cache.get("primary") is None)

print("\nSPEED OK:" , f2 < f1 * 0.7, "| CORRECTNESS OK:", ident)
