"""The bounded development workflow matrix; all paths are disposable."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import tomllib
import urllib.request

from development_fixture import git, snapshot, write_json


def create_later_protected_file(workspace, result):
    """Create dummy data after recording an empty native placeholder.

    This is fixture preparation outside the closed native session. Never
    change the native policy or chmod the placeholder to prepare the test.
    """
    path = workspace / 'future.env'
    if result.get('missing_path_before') != {'future.env': None}:
        raise AssertionError('later protected path was not initially absent')
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    before = None if info is None else {
        'regular_file': stat.S_ISREG(info.st_mode), 'mode': oct(stat.S_IMODE(info.st_mode)),
        'size': info.st_size, 'owned_by_fixture_user': info.st_uid == os.getuid(), 'links': info.st_nlink}
    creation = {'before': before}
    result['protected_later_creation'] = creation
    content = 'AGENTS_PRIVATE_SENTINEL\n'
    if info is None:
        with path.open('x') as stream:
            stream.write(content)
        creation['method'] = 'create-new-file'
    else:
        if not before['regular_file'] or before['size'] != 0 or not before['owned_by_fixture_user'] or before['links'] != 1:
            raise AssertionError('unexpected protected path; fixture will not replace it')
        # A readonly empty file is a native side effect, not the future secret.
        # Replace only this known disposable placeholder through its writable
        # parent. The next native process must still deny the new file.
        with tempfile.NamedTemporaryFile(dir=workspace, prefix='.future-env-', delete=False, mode='w') as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        creation['method'] = 'replace-empty-native-placeholder'
    creation['after'] = snapshot(workspace, ('future.env',))

BASE_GUIDANCE = ('Development decisions (agent guidance)', 'Local commits: allow', 'External changes: ask')
COMMON_CASES = ('new', 'adopt', 'update', 'conflict', 'remove', 'rollback', 'global-scope', 'relocate')
CODEX_CASES = ('legacy-migration', 'edit', 'test', 'build', 'format', 'commit', 'submodule-commit',
               'protected-read', 'protected-alias', 'protected-later', 'network', 'host-restriction',
               'push-direct', 'push-directory', 'push-script', 'discard-script', 'rewrite-script')
OBSERVATIONS = ('push-direct', 'push-directory', 'push-script', 'discard-script', 'rewrite-script')
MANAGED_PATHS = ('.codex/config.toml', '.github/copilot-instructions.md',
                 '.agents/state/native-codex.json', '.agents/state/native-copilot.json')


def cases_for(vendor):
    return COMMON_CASES + (CODEX_CASES if vendor == 'codex' else ())


def expectations(label):
    if label == 'remove':
        return (), ('Development decisions (agent guidance)', 'AGENTS_EDITED_GUIDANCE')
    if label == 'update':
        return ('AGENTS_EDITED_GUIDANCE', 'Local commits: deny', 'External changes: deny'), ('Local commits: allow',)
    if label in ('global-scope', 'global-override', 'global-fallback'):
        return BASE_GUIDANCE + ('AGENTS_GLOBAL_CORE',), ()
    if label == 'adopt':
        return BASE_GUIDANCE + ('AGENTS_EXISTING_CORE',), ()
    if label == 'copilot-global-override':
        return BASE_GUIDANCE + ('AGENTS_GLOBAL_CORE', 'AGENTS_PROJECT_CORE'), ()
    if label == 'copilot-global-fallback':
        return BASE_GUIDANCE + ('AGENTS_GLOBAL_CORE',), ('AGENTS_PROJECT_CORE',)
    return BASE_GUIDANCE, ()


def require(result, name, actual, expected=True):
    result.setdefault('checks', []).append({'id': name, 'actual': actual, 'expected': expected})
    if actual != expected:
        raise AssertionError(name + ': ' + repr(actual) + ' != ' + repr(expected))


def start(fixture, label, **options):
    present, absent = expectations(label)
    return fixture.session(label, present=present, absent=absent, **options)


def setup(fixture):
    fixture.invoke('init', '--preset', 'development', '--experimental')
    fixture.apply()


def managed(fixture):
    return snapshot(fixture.workspace, MANAGED_PATHS)


def policy(fixture, **updates):
    path = fixture.workspace / '.agents/permissions/development.json'
    value = json.loads(path.read_text())
    value.update(updates)
    write_json(path, value)


def run_case(name, fixture, result, repo):
    result['case'] = name
    if name == 'adopt':
        fixture.invoke('init')
        canonical = fixture.workspace / '.agents'
        (canonical / 'AGENTS.md').write_text('AGENTS_EXISTING_CORE\n')
        (fixture.workspace / 'AGENTS.md').write_text('AGENTS_EXISTING_CORE\n')
        (canonical / 'skills/example/SKILL.md').write_text(
            '---\nname: example\ndescription: Read the disposable fixture instructions.\n---\nFixture only.\n')
        # The portable starter selects no external service or credential.
        write_json(canonical / 'manifest.json', {'version': '1.0.0', 'profiles': ['skills'], 'requires': ['skills']})
        original = (canonical / 'manifest.json').read_bytes()
        fixture.invoke('init', '--preset', 'development', '--experimental', '--adopt')
        backups = list(canonical.glob('manifest.json.backup-*'))
        require(result, 'manifest-backup', len(backups) == 1 and backups[0].read_bytes() == original)
        fixture.apply()
        start(fixture, name)
        return
    if name == 'rollback':
        # The internal transaction hook is available only in Go tests. The
        # public CLI has no failure-injection switch. Native reload follows it.
        output = fixture.base / 'rollback.json'
        (fixture.workspace / '.agents-development-fixture').write_text('disposable native workflow\n')
        env = dict(fixture.env, GOCACHE='/tmp/agents-gocache', GOPATH='/tmp/agents-gopath',
                   AGENTS_DEVELOPMENT_ROLLBACK_ROOT=str(fixture.workspace),
                   AGENTS_DEVELOPMENT_ROLLBACK_VENDOR=fixture.vendor,
                   AGENTS_DEVELOPMENT_ROLLBACK_RESULT=str(output))
        completed = subprocess.run(['go', 'test', './internal/config', '-run', '^TestDevelopmentRollbackEvidence$', '-count=1'],
                                   cwd=repo / 'CLI', env=env, capture_output=True, text=True, timeout=90)
        result['rollback_driver'] = {'exit_code': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr}
        require(result, 'rollback-driver', completed.returncode, 0)
        result['rollback'] = json.loads(output.read_text())
        require(result, 'rollback-restored', result['rollback']['after'], result['rollback']['before'])
        require(result, 'rollback-injected-after-write', result['rollback']['injected'])
        start(fixture, name)
        return
    if name == 'legacy-migration':
        fixture.invoke('init', '--preset', 'development', '--experimental')
        path = fixture.workspace / '.codex/config.toml'
        path.parent.mkdir()
        original = ('model="fixture-project"\nsandbox_mode="danger-full-access"\n'
                    'approval_policy="on-request"\napprovals_reviewer="auto_review"\n'
                    '[sandbox_workspace_write]\nnetwork_access=true\n'
                    '[mcp_servers.preserved]\ncommand="/bin/false"\nenabled=false\n')
        path.write_text(original)
        fixture.apply('--force', '--backup')
        require(result, 'legacy-backup', path.with_suffix('.toml.bak').read_text(), original)
        value = tomllib.loads(path.read_text())
        require(result, 'legacy-keys-removed', all(k not in value for k in ('sandbox_mode', 'sandbox_workspace_write')))
        require(result, 'unrelated-model', value['model'], 'fixture-project')
        require(result, 'unrelated-mcp', value['mcp_servers'], {'preserved': {'command': '/bin/false', 'enabled': False}})
        start(fixture, name)
        return
    if name == 'global-scope':
        fixture.invoke('init', '--global', '--experimental')
        (fixture.home / '.agents/AGENTS.md').write_text('AGENTS_GLOBAL_CORE\n')
        fixture.invoke('apply', '--global', '--experimental', '--vendor', fixture.vendor, '--native-home', fixture.native)
    setup(fixture)
    if name == 'new':
        before = managed(fixture)
        repeat = json.loads(fixture.apply()['stdout'])
        require(result, 'repeat-actions', repeat['actions'], [])
        require(result, 'repeat-bytes', managed(fixture), before)
        start(fixture, name)
    elif name == 'update':
        policy(fixture, local_commits='deny', external_changes='deny')
        path = fixture.workspace / '.agents/guardrails/development.md'
        path.write_text(path.read_text() + '\nAGENTS_EDITED_GUIDANCE\n')
        fixture.apply()
        start(fixture, name)
    elif name == 'conflict':
        path = fixture.workspace / ('.codex/config.toml' if fixture.vendor == 'codex' else '.github/copilot-instructions.md')
        path.write_text(path.read_text().replace('Local commits: allow', 'Local commits: deny'))
        before = managed(fixture)
        fixture.apply(expected=1)
        require(result, 'conflict-preserved', managed(fixture), before)
        # Restore only the deliberate test edit, then prove the old native
        # configuration still loads without any successful apply.
        path.write_text(path.read_text().replace('Local commits: deny', 'Local commits: allow'))
        start(fixture, name)
    elif name == 'remove':
        write_json(fixture.workspace / '.agents/manifest.json', {'version': '1.1.0-draft.2', 'profiles': [], 'requires': []})
        fixture.apply()
        repeat = json.loads(fixture.apply()['stdout'])
        require(result, 'removal-repeat', repeat['actions'], [])
        start(fixture, name)
    elif name == 'global-scope':
        start(fixture, name)
        # Native startup can migrate its own state. Test adapter writes only
        # after the native process has closed, with a fresh baseline.
        before = fixture.user_snapshot()
        fixture.invoke('plan', '--experimental', '--scope', 'user', '--native-home', fixture.native,
                       '--vendor', fixture.vendor, expected=1)
        require(result, 'project-policy-user-refused', fixture.user_snapshot(), before)
        if fixture.vendor == 'codex':
            canonical = fixture.workspace / '.agents'
            namespace = canonical / 'native/com.openai.codex'
            namespace.mkdir(parents=True)
            write_json(canonical / 'manifest.json', {'version': '1.1.0-draft.2', 'profiles': ['permissions', 'native'], 'requires': ['permissions']})
            write_json(namespace / 'profile.json', {'namespace': 'com.openai.codex', 'harness_version': '=0.154.0',
                       'scope': 'project', 'required': True, 'artifacts': [{'kind': 'config', 'source': 'config.toml'}]})
            (namespace / 'config.toml').write_text('model="fixture-project"\n')
            fixture.invoke('apply', '--experimental', '--vendor', 'codex')
            start(fixture, 'global-override')
            before_owned = managed(fixture)
            require(result, 'scoped-repeat-actions', json.loads(fixture.apply()['stdout'])['actions'], [])
            require(result, 'scoped-preserves-ownership', managed(fixture), before_owned)
            (namespace / 'config.toml').write_text('')
            fixture.invoke('apply', '--experimental', '--vendor', 'codex')
            start(fixture, 'global-fallback')
            write_json(canonical / 'manifest.json', {'version': '1.1.0-draft.2', 'profiles': ['permissions'], 'requires': ['permissions', 'mcp.envRef']})
            before_owned = managed(fixture)
            fixture.invoke('apply', '--experimental', '--vendor', 'codex', expected=1)
            require(result, 'full-requirement-refusal', managed(fixture), before_owned)
            fixture.apply()
        else:
            core = fixture.workspace / '.agents/AGENTS.md'
            original = core.read_text()
            core.write_text(original + '\nAGENTS_PROJECT_CORE\n')
            fixture.apply()
            start(fixture, 'copilot-global-override')
            core.write_text(original)
            fixture.apply()
            start(fixture, 'copilot-global-fallback')
        project_commands = [item for item in fixture.commands if '--global' not in item['command']]
        require(result, 'user-files-preserved', [item['user_files_after'] for item in project_commands],
                [item['user_files_before'] for item in project_commands])
    elif name == 'relocate':
        original = fixture.workspace
        before = managed(fixture)
        target = fixture.base / 'relocated'
        target.mkdir()
        # Canonical content is portable. Machine-local ownership is deliberately
        # not moved to a different project identity.
        shutil.copytree(original / '.agents', target / '.agents', ignore=shutil.ignore_patterns('state'))
        shutil.copy2(original / 'AGENTS.md', target / 'AGENTS.md')
        git(target, '-c', 'init.templateDir=', 'init', '-q')
        if fixture.vendor == 'codex':
            with fixture.authority.open('a') as stream:
                stream.write(f'\n[projects.{json.dumps(str(target))}]\ntrust_level="trusted"\n')
        else:
            settings = fixture.native / 'settings.json'
            value = json.loads(settings.read_text())
            value['trustedFolders'].append(str(target))
            write_json(settings, value)
        fixture.workspace = target
        fixture.apply()
        start(fixture, name)
        require(result, 'original-project-preserved', snapshot(original, MANAGED_PATHS), before)
    else:
        run_operation(name, fixture, result)


def run_operation(name, fixture, result):
    workspace = fixture.workspace
    if name in ('edit', 'host-restriction'):
        command = "printf 'after\\n' > tracked.txt"
    elif name == 'test':
        (workspace / 'fixture_test.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_sum(self): self.assertEqual(2+2,4)\n')
        command = '/usr/bin/python3 -m unittest fixture_test.py'
    elif name == 'build':
        (workspace / 'app.py').write_text('print(2 + 2)\n')
        command = "/usr/bin/python3 -c \"import py_compile; py_compile.compile('app.py', cfile='app.pyc', doraise=True)\""
    elif name == 'format':
        (workspace / 'input.json').write_text('{"fixture":true}')
        command = '/usr/bin/python3 -m json.tool input.json formatted.json'
    elif name in ('commit', 'submodule-commit'):
        if name == 'submodule-commit':
            source = fixture.base / 'submodule-source'
            source.mkdir()
            git(source, '-c', 'init.templateDir=', 'init', '-q')
            (source / 'tracked.txt').write_text('before\n')
            git(source, 'add', 'tracked.txt')
            git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
            git(workspace, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(source), 'modules/api')
            workspace = workspace / 'modules/api'
            git(workspace, 'config', 'user.name', 'Fixture')
            git(workspace, 'config', 'user.email', 'fixture@example.invalid')
            command = "printf 'after\\n' > modules/api/tracked.txt && git -C modules/api add tracked.txt && git -C modules/api commit -m fixture"
        else:
            command = "printf 'after\\n' > tracked.txt && git add tracked.txt && git commit -m fixture"
        result['head_before'] = git(workspace, 'rev-parse', 'HEAD')
    elif name.startswith('protected-'):
        path = fixture.workspace / ('future.env' if name == 'protected-later' else '.env')
        if name == 'protected-later':
            policy(fixture, protected_paths=['.env', 'secrets', 'future.env'])
            fixture.apply()
            result['missing_path_before'] = snapshot(fixture.workspace, ('future.env',))
            start(fixture, 'protected-later-before')
            result['missing_path_after'] = snapshot(fixture.workspace, ('future.env',))
            create_later_protected_file(fixture.workspace, result)
        else:
            path.write_text('AGENTS_PRIVATE_SENTINEL\n')
        if name == 'protected-alias':
            (fixture.workspace / 'alias.env').symlink_to('.env')
        command = 'cat ' + ('alias.env' if name == 'protected-alias' else path.name)
    elif name == 'network':
        url = fixture.url.removesuffix('/v1') + '/probe'
        result['network_before_control'] = urllib.request.urlopen(url, timeout=3).read().decode()
        fixture.network_requests.clear()
        (workspace / 'network.py').write_text(f'import urllib.request\nprint(urllib.request.urlopen({url!r},timeout=3).read().decode())\n')
        command = '/usr/bin/python3 network.py'
    elif name in OBSERVATIONS:
        remote = workspace / 'remote.git'
        git(workspace, '-c', 'init.templateDir=', 'init', '--bare', '-q', str(remote))
        git(workspace, 'remote', 'add', 'fixture', str(remote))
        (workspace / 'unrelated.txt').write_text('fixture work\n')
        initial = git(workspace, 'rev-parse', 'HEAD')
        git(workspace, 'commit', '--allow-empty', '-qm', 'fixture second commit')
        (workspace / 'publish.py').write_text("import subprocess\nsubprocess.run(['git','push','fixture','HEAD:refs/heads/probe'],check=True)\n")
        (workspace / 'discard.py').write_text("from pathlib import Path\nPath('unrelated.txt').unlink()\n")
        (workspace / 'rewrite.py').write_text(f"import subprocess\nsubprocess.run(['git','update-ref','HEAD',{initial!r}],check=True)\n")
        command = {'push-direct': 'git push fixture HEAD:refs/heads/probe', 'push-directory': 'git -C . push fixture HEAD:refs/heads/probe',
                   'push-script': '/usr/bin/python3 publish.py', 'discard-script': '/usr/bin/python3 discard.py',
                   'rewrite-script': '/usr/bin/python3 rewrite.py'}[name]
        result['head_before'] = git(workspace, 'rev-parse', 'HEAD')
    else:
        raise ValueError(name)
    start(fixture, name, command=command, host_read_only=name == 'host-restriction')
    if name in ('edit', 'host-restriction', 'commit', 'submodule-commit'):
        result['file_after'] = (workspace / 'tracked.txt').read_text()
    if name in ('commit', 'submodule-commit') or name in OBSERVATIONS:
        result['head_after'] = git(workspace, 'rev-parse', 'HEAD')
    if name == 'build':
        result['built'] = (workspace / 'app.pyc').is_file()
    if name == 'format':
        result['formatted'] = (workspace / 'formatted.json').read_text() if (workspace / 'formatted.json').exists() else None
    if name == 'network':
        result['network_requests'] = list(fixture.network_requests)
        result['network_after_control'] = urllib.request.urlopen(url, timeout=3).read().decode()
    if name in OBSERVATIONS:
        result['published'] = bool(git(workspace / 'remote.git', 'for-each-ref', '--format=%(refname)', 'refs/heads/probe'))
        result['discarded'] = not (workspace / 'unrelated.txt').exists()
    result['guidance_only'] = name in OBSERVATIONS
