"""Ad-hoc: print the FAIL/WARN findings from a health JSON."""

import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "output/K96/K96_health.json"
d = json.load(open(path, encoding="utf-8"))

print("verdict :", d["verdict"])
print("content :", d["standards_verdict"], "| wiring:", d["config_verdict"])
print("counts  :", d["counts"])

for c in d["checks"]:
    if c["severity"] in ("FAIL", "WARN"):
        print(f"\n[{c['severity']}] {c['title']} -- {c['result']}")
        if c["detail"]:
            print("   ", c["detail"])
        for e in c["evidence"][:5]:
            print("    *", e)
