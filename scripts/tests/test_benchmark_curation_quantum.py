"""The offline scheduler benchmark keeps its synthetic assumptions explicit."""
import json
from pathlib import Path
import subprocess
import sys


def test_quantum_benchmark_compares_identical_verdict_work():
    script = Path(__file__).resolve().parents[1] / 'benchmark_curation_quantum.py'
    result = subprocess.run([sys.executable, str(script)], check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report['prior_drain']['all_decided_once'] and report['adaptive']['all_decided_once']
    assert report['prior_drain']['verdicts'] == report['adaptive']['verdicts'] == 20
    assert report['prior_drain']['first_yield_s'] == 400
    assert report['adaptive']['first_yield_s'] == 80
    assert report['prior_drain']['total_verdict_time_s'] == report['adaptive']['total_verdict_time_s']
    assert 'Synthetic' in report['limitations']
