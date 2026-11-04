"""The map's summary: draws, members, questions as the judge reads them, and the depth rule's reads against what was
forced (a read not forced means another round)."""
import glob, json, statistics
qs, reads, forced, draws = set(), set(), set(), {}
for f in glob.glob("in/**/pass1-*.json", recursive=True):
    g = json.load(open(f))
    qs |= {tuple(x) for x in g["questions"]}
    reads |= set(g["reads"]); forced |= set(g["forced"]); draws.update(g["draws"])
out = {"draws": len(draws), "questions": len(qs), "members": sum(v["members"] for v in draws.values()),
       "occurrences": sum(v["occurrences"] for v in draws.values()), "reads": len(reads), "forced": len(forced),
       "new_reads": sorted(reads - forced), "slowest_draw_s": max(v["seconds"] for v in draws.values()),
       "median_draw_s": statistics.median(v["seconds"] for v in draws.values())}
print(json.dumps(out, indent=1))
json.dump(out, open("out/summary.json", "w"))
json.dump(sorted(qs), open("out/questions.json", "w"))
