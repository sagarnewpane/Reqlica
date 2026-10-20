"""Opt-in live generation benchmark. Uses provider quota; never executes generated code."""

import argparse
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pydantic import SecretStr
from reqlica.generation import jobs
from reqlica.generation.schemas import GenerationRequest
from reqlica.projects.store import create_project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requirements', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory for isolated projects, checkpoints and metrics')
    parser.add_argument('--provider', choices=('groq', 'openai', 'compatible'), default='groq')
    parser.add_argument('--model', default='openai/gpt-oss-20b')
    parser.add_argument('--base-url')
    parser.add_argument('--runs', type=int, choices=range(1, 11), default=3)
    parser.add_argument('--max-tokens', type=int, default=8192)
    parser.add_argument('--confirm-provider-spend', action='store_true')
    args = parser.parse_args()
    if not args.confirm_provider_spend:
        parser.error('Live evaluations incur provider charges. Pass --confirm-provider-spend to run.')
    request = GenerationRequest(requirements=args.requirements.read_text(), provider=args.provider,
                                model=args.model, base_url=args.base_url, max_tokens=args.max_tokens)
    output = args.output_dir.absolute()
    output.mkdir(parents=True, exist_ok=False)
    # This dedicated process uses an isolated registry, never the user's projects.
    os.environ['REQLICA_HOME'] = str(output / 'registry')
    key = os.environ.get('REQLICA_EVAL_API_KEY')
    key = SecretStr(key) if key else None
    results = []
    for index in range(args.runs):
        project = create_project(output / f'run-{index + 1}', f'Evaluation {index + 1}')
        job_id = uuid4().hex
        started = monotonic()
        row = {'run': index + 1, 'generation_id': job_id, 'schema_failures': 0, 'other_validation_failures': 0}
        try:
            job = jobs.create_job(project.id, job_id, request)
            for _ in range(66):
                job = jobs.step_job(project.id, job_id, key)
                code = (job.get('error') or {}).get('code')
                if code in {'response_schema_mismatch', 'malformed_json'}:
                    row['schema_failures'] += 1
                elif job['status'] == 'retrying':
                    row['other_validation_failures'] += 1
                if job['status'] not in {'ready', 'retrying'}:
                    break
            row.update(status=job['status'], result=(job.get('result') or {}).get('status'),
                       calls=job['calls'], usage=job['usage'], error=job.get('error'))
        except Exception as error:
            # Fixed diagnostics only; provider text/credentials never enter the report.
            row.update(status='error', error_type=type(error).__name__)
        row['elapsed_seconds'] = round(monotonic() - started, 2)
        row['generated_code_executed'] = False
        results.append(row)
        (output / 'metrics.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(row))
    successful = sum(row.get('result') == 'draft_created' for row in results)
    print(f'{successful}/{len(results)} runs produced statically validated drafts. Code behavior was not tested.')


if __name__ == '__main__':
    main()
