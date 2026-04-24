"""Score all v6 ablation variant reports using LLM-as-Judge."""
import sys
import json
import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eval.ablation.report_judge import score_variant_file, _get_judge_llm

llm = _get_judge_llm()
all_scores = {}

files = sorted(glob.glob(str(Path(__file__).parent / "results" / "v6_*.json")))
for f in files:
    p = Path(f)
    if "comparison" in p.name:
        continue
    variant = p.stem.replace("v6_", "")
    print(f"\n=== Judging: {variant} ===", flush=True)
    scored = score_variant_file(p, llm=llm)
    all_scores[variant] = scored

# Compute aggregates
dims = ("coverage", "depth", "structure", "citations", "overall")
sep = "=" * 70
header_fmt = "  {:<20} {:>3} {:>6} {:>6} {:>6} {:>6} {:>7}"
print(f"\n{sep}")
print(header_fmt.format("Variant", "N", "Cover", "Depth", "Struct", "Cite", "Overall"))
print(header_fmt.format("-" * 20, "---", "------", "------", "------", "------", "-------"))

summary = {}
for variant, cases in all_scores.items():
    scored_cases = [
        c for c in cases
        if c.get("judge_scores") and "error" not in c["judge_scores"]
    ]
    if not scored_cases:
        summary[variant] = {"scored": 0, "avg_scores": {}}
        continue
    avgs = {}
    for dim in dims:
        vals = [c["judge_scores"].get(dim, 0) for c in scored_cases]
        avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0
    summary[variant] = {"scored": len(scored_cases), "avg_scores": avgs}
    a = avgs
    row = "  {:<20} {:>3} {:>6.1f} {:>6.1f} {:>6.1f} {:>6.1f} {:>7.1f}"
    print(row.format(
        variant, len(scored_cases),
        a.get("coverage", 0), a.get("depth", 0), a.get("structure", 0),
        a.get("citations", 0), a.get("overall", 0),
    ))

print(sep)

out = {"summary": summary, "details": all_scores}
out_file = Path(__file__).parent / "results" / "v6_judge_scores.json"
out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(f"\nSaved to {out_file}")
