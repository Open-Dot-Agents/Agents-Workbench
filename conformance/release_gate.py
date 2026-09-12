#!/usr/bin/env python3
"""Require current baseline and extended evidence for a release candidate."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import run_extended
import summarize_extended
from validate_result import required_preflight_checks, validate_adapter_semantics

ROOT = Path(__file__).resolve().parents[2]
VENDORS = ('codex', 'copilot', 'claude')


def verify(directory, vendors=VENDORS, release=False):
    schema = json.loads((ROOT/'SPEC/spec/1.0/schemas/conformance-result.schema.json').read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = []
    for vendor in vendors:
        try:
            result = json.loads((directory/(vendor+'.json')).read_text())
            validator.validate(result)
            assert result['class'] == 'adapter', 'adapter baseline required'
            validate_adapter_semantics(result)
            assert result['passed'] is True, 'baseline did not pass'
            assert result['implementation'] == 'reference-cli-'+vendor, 'wrong implementation'
            metadata = result['metadata']
            assert metadata['harness'] == vendor and metadata['runMode'] == 'native', 'native run required'
            assert all(c['passed'] is True for c in result['checks']), 'failed baseline assertion'
            digest = metadata['agentsSha256']
            assert re.fullmatch('[0-9a-f]{64}', digest), 'CLI hash missing'
            assert metadata['runnerSha256'] == hashlib.sha256((ROOT/'WORKBENCH/conformance/run_adapter.py').read_bytes()).hexdigest(), 'baseline runner changed'
            if release:
                assert result['implementationVersion'] == '1.0.0', 'wrong CLI release version'
                assert metadata['sourceDirty'] == {c:False for c in ('.','CLI','SPEC','WORKBENCH')}, 'release evidence has uncommitted source changes'
                import subprocess
                expected = {c:subprocess.check_output(['git','-C',str(ROOT/c),'rev-parse','HEAD'],text=True).strip()
                            for c in ('.','CLI','SPEC','WORKBENCH')}
                assert metadata['sourceCommits'] == expected, 'release source commits differ'
            audit = summarize_extended.audit(directory/'extended', [vendor])
            assert audit['functionalPass'], 'extended evidence failed or is incomplete'
            for case in run_extended.cases_for(vendor):
                extended = json.loads((directory/'extended'/vendor/(case+'.json')).read_text())
                assert set(extended['sources']) == set(run_extended.SOURCES), 'extended source snapshot set is incomplete or unknown'
                assert extended['metadata']['agentsSha256'] == digest, 'mixed CLI binaries'
                assert extended['metadata']['sourceCommits'] == metadata['sourceCommits'], 'mixed source commits'
                assert extended['metadata']['sourceDirty'] == metadata['sourceDirty'], 'mixed source state'
                if release:
                    assert extended['sources'] == run_extended.SOURCES, 'extended runner or helper changed'
                preflight = extended['preflight']
                assert isinstance(preflight, list) and preflight, 'extended preflight is missing'
                assert all(isinstance(c, dict) and c.get('passed') is True for c in preflight), 'extended preflight failed'
                required = required_preflight_checks(vendor, extended['metadata'].get('authMode', 'environment'))
                assert required.issubset({c.get('id') for c in preflight}), 'extended preflight is missing required checks'
        except (OSError, ValueError, AssertionError, KeyError, TypeError) as error:
            errors.append(f'{vendor}: {error}')
        except Exception as error:
            # JSON Schema validation errors are also failures, never skipped evidence.
            errors.append(f'{vendor}: invalid evidence: {error}')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--vendors', nargs='+', choices=VENDORS, default=list(VENDORS))
    parser.add_argument('--release', action='store_true')
    args = parser.parse_args()
    if args.release and set(args.vendors) != set(VENDORS):
        parser.error('release requires all three harnesses')
    errors = verify(args.evidence_dir, args.vendors, args.release)
    for error in errors: print('FAIL', error)
    if not errors: print('Baseline and extended evidence agree for every required harness.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
