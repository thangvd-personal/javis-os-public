"""Opt-in real CLI qualification of typed role profiles, with local dummy inference.

No adapter/readiness stub is used. Actual OperationWorkspace/OperationProfile and
claude_engine build the request. This qualifies native enforcement, not account
auth or worker/CallLedger feasibility; that separate actual pipeline must run
only after qualification. No synthetic Trial is installed for a role.
"""
import sys
from pathlib import Path

if any(flag in sys.argv for flag in ('--role-worker', '--role-owner')):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ROOT, SERVER  # noqa: F401,E402

import asyncio
import contextlib
import gzip
import http.server
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import improvement_claude as native
import improvement_worker as worker
from improvement_operation_profile import OperationProfile
from improvement_operation_workspace import OperationWorkspace
from improvement_sandbox import TrialWorkspace, worker_environment
from improvement_store import _hash
from test_improvement_runtime_qualification import candidate, _ambient, _job_pids, _alive, CANDIDATE_VERSION, CANDIDATE_HASH, SDK_VERSION, MARKERS

KINDS = ('propose', 'repair', 'semantic')
DATA_MARKER = 'QUALIFICATION_AUTHORIZED_ROLE_INPUT'
EVIDENCE = []


def persisted_input_markers(root):
    """Inspect only owned synthetic files; never follow native-created links."""
    markers = (DATA_MARKER.encode(), b'Retain protected owner meaning.')
    extended = Path('\\\\?\\' + str(root)) if os.name == 'nt' else root
    found = []
    for path in extended.rglob('*'):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('qualification_unexpected_link')
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_nlink != 1:
            raise ValueError('qualification_unexpected_hardlink')
        with path.open('rb') as source:
            tail = b''
            while chunk := source.read(64 * 1024):
                value = tail + chunk
                if any(marker in value for marker in markers):
                    found.append(path.name)
                    break
                tail = value[-max(map(len, markers)):]
    return found


@contextlib.contextmanager
def qualification_directory(prefix):
    """Strict owned cleanup, including native cache paths beyond MAX_PATH.

    Keep the same long-path test environment. The repo's generic cleanup helper
    already uses extended Windows paths, but intentionally ignores failures; a
    qualification must assert actual deletion rather than borrow that behavior.
    """
    parent = Path(tempfile.gettempdir()).resolve()
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    try:
        yield root
    finally:
        resolved = root.resolve()
        if resolved == parent or not resolved.is_relative_to(parent) or root.is_symlink():
            raise ValueError('qualification_cleanup_scope_changed')
        target = ('\\\\?\\' + str(resolved)) if os.name == 'nt' else str(resolved)

        def readonly(function, path, error):
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise error
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            function(path)

        shutil.rmtree(target, onexc=readonly)
        if root.exists():
            raise RuntimeError('qualification_cleanup_incomplete')


def materialization(kind, pin):
    tuning = {'version': 1, 'kind': 'authorized_tuning', 'candidate_limit': 2,
        'evidence_refs': [{'kind': 'tuning_trial', 'id': 'qualification-tuning'}],
        'baseline': {'content': 'Retain protected owner meaning.', 'hash': _hash('baseline')},
        'qualification': DATA_MARKER}
    data = tuning
    if kind == 'repair':
        data = {'kind': 'authorized_structural_repair', 'authorized_input': tuning, 'candidate_limit': 2,
                'invalid_output': 'synthetic malformed JSON', 'validation_code': 'invalid_generation_output'}
    elif kind == 'semantic':
        data = {'kind': 'authorized_semantic_review', 'source_refs': [{'kind': 'candidate', 'id': 'qualification-candidate'}],
            'candidate': {'before': 'Retain protected owner meaning.', 'after': 'Retain protected owner meaning. Check totals.'},
            'protected_context': 'Retain protected owner meaning.', 'qualification': DATA_MARKER}
    request = {'version': 1, 'experiment_id': 'qualification-experiment', 'context_id': 'qualification-context',
        'definition_hash': _hash('qualification-definition'), 'kind': kind,
        'role': 'semantic_reviewer' if kind == 'semantic' else 'improver',
        'purpose': 'improve_evaluate' if kind == 'semantic' else 'improve_propose',
        'candidate_id': 'qualification-candidate' if kind == 'semantic' else None,
        'parent_id': 'qualification-propose' if kind == 'repair' else None, 'input_hash': _hash(data)}
    return {'version': 1, 'brain_id': 'disposable-qualification-brain', 'operation_id': 'qualification-' + kind,
        'request': request, 'engine_config_hash': _hash(pin), 'data': data, 'learning_origin': 'compose'}


def role_worker(stage, kind):
    if sys.stdin.readline().strip() != 'qualification_start':
        raise ValueError('qualification_gate_missing')
    from urllib.parse import urlsplit
    endpoint = urlsplit(os.environ['ANTHROPIC_BASE_URL'])
    if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port
            or os.environ.get('ANTHROPIC_API_KEY') != 'synthetic-local-provider-only'):
        raise ValueError('qualification_transport_not_local_dummy')
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        if os.environ.get(name) != os.environ['ANTHROPIC_BASE_URL']:
            raise ValueError('qualification_proxy_not_local')
    for name in ('HOME', 'USERPROFILE', 'CLAUDE_CONFIG_DIR'):
        expected = stage.parent / ('config' if name == 'CLAUDE_CONFIG_DIR' else 'empty-auth-home')
        if Path(os.environ[name]) != expected:
            raise ValueError('qualification_home_not_scratch')
    binary = candidate(os.environ['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'])
    _ambient(stage)
    pin = {'version': 1, 'provider': 'anthropic-cli', 'engine_id': 'claude-sdk', 'model': 'claude-opus-5-5',
        'reasoning': 'medium', 'cli_path': str(binary), 'cli_hash': CANDIDATE_HASH, 'cli_version': CANDIDATE_VERSION,
        'sdk_version': SDK_VERSION, 'profile_version': native.PROFILE_VERSION, 'profile_hash': native.profile_hash()}
    workspace = OperationWorkspace(stage.parent / 'operations', brain_barrier=TrialWorkspace(stage.parent / 'trials'))
    manifest = materialization(kind, pin)
    receipt = workspace.prepare(manifest['operation_id'], 1, manifest)
    actual = Path(receipt['path'])
    shutil.copytree(stage / '.claude', actual / '.claude')
    for name in ('CLAUDE.md', '.mcp.json'):
        shutil.copyfile(stage / name, actual / name)
    from claude_cli import claude_engine
    from claude_agent_sdk._internal.transport import subprocess_cli
    original = subprocess_cli.anyio.open_process
    spawns, events, order = [], [], []

    async def observed(command, **kwargs):
        if Path(command[0]) != binary:
            raise ValueError('qualification_unpinned_executable')
        if not kwargs.get('creationflags', 0) & worker.winproc.no_window():
            raise ValueError('qualification_native_console_not_hidden')
        process = await original(command, **kwargs)
        item = {'kind': 'version' if any(flag in command for flag in ('-v', '--version')) else 'query',
                'pid': process.pid, 'creationflags': kwargs['creationflags']}
        spawns.append(item)
        print('qualification-spawn=' + json.dumps(item), file=sys.stderr, flush=True)
        return process

    async def before_query():
        # A qualification marker only, not a counterfeit production CallLedger ACK.
        order.append('before_query')
        print('qualification-before-query', file=sys.stderr, flush=True)

    async def run():
        profile = OperationProfile(pin, workspace, receipt, _hash('qualification-only-capability'), before_query=before_query)
        engine = claude_engine(system_prompt=profile.system_prompt, cwd=str(actual),
            tag='improvement-operation:' + manifest['operation_id'] + ':1', allowed_tools=[], model=pin['model'])
        engine.trial_profile = profile
        with patch.object(subprocess_cli.anyio, 'open_process', side_effect=observed), worker._sdk_hidden_processes(), \
             patch('config.read_settings', side_effect=AssertionError('Ordinary configuration access forbidden')):
            async for event in engine.query(profile.user_prompt()):
                events.append(event)
        return profile

    profile = asyncio.run(run())
    import ctypes
    result = {'scope': 'synthetic/native-role-profile-local-provider-only', 'kind': kind, 'production_billing': False,
        'operation_id': receipt['operation_id'], 'input_hash': receipt['input_hash'], 'events': events, 'order': order,
        'broker_events': profile._native.broker.events, 'denials': profile.denials, 'spawns': spawns,
        'worker_has_console': bool(ctypes.windll.kernel32.GetConsoleWindow()), 'profile_hash': native.profile_hash(),
        'receipt': receipt}
    print(json.dumps(result), flush=True)


class LocalProvider:
    """Only malicious synthetic tool requests and a bounded terminal reply."""
    def __init__(self, outside, *, pending=False):
        self.requests, self.requested, self.release = [], threading.Event(), threading.Event()
        self.count, self.pending = 0, pending
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, media='application/json'):
                self.send_response(status)
                self.send_header('Content-Type', media)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_CONNECT(self):
                provider.requests.append({'path': self.path, 'method': 'CONNECT'})
                self.reply(503, b'{}')

            def do_GET(self):
                provider.requests.append({'path': self.path, 'method': 'GET'})
                self.reply(503, b'{}')

            def do_POST(self):
                raw = self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 4_000_000))
                if self.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                data = json.loads(raw or b'{}')
                path = self.path.split('?', 1)[0]
                text = json.dumps(data)
                provider.requests.append({'path': path, 'method': 'POST', 'model': data.get('model'),
                    'effort': data.get('output_config', {}).get('effort'),
                    'tools': [item.get('name') for item in data.get('tools', [])],
                    'dummy_auth_only': self.headers.get('x-api-key') == 'synthetic-local-provider-only'
                        and self.headers.get('Authorization') in (None, 'Bearer synthetic-local-provider-only'),
                    'ambient_markers': [marker for marker in (*MARKERS, 'SYNTHETIC_AMBIENT_CONTEXT_DO_NOT_LOAD', 'OUTSIDE_ROLE_CANARY') if marker in text],
                    'authorized_data_present': DATA_MARKER in text,
                    'fast_requested': data.get('speed') == 'fast' or data.get('service_tier') in {'fast', 'priority'},
                    'results': {block['tool_use_id']: bool(block.get('is_error')) for message in data.get('messages', [])
                        if isinstance(message.get('content'), list) for block in message['content'] if block.get('type') == 'tool_result'}})
                if path == '/v1/messages/count_tokens':
                    self.reply(200, b'{"input_tokens":1}')
                    return
                if path != '/v1/messages':
                    self.reply(503, b'{}')
                    return
                provider.count += 1
                provider.requested.set()
                if provider.pending:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    provider.release.wait(40)
                    return
                if provider.count > 2:
                    self.reply(400, b'{"error":{"type":"invalid_request_error","message":"synthetic bound"}}')
                    return
                attacks = [('bash', 'Bash', {'command': 'echo forbidden > "' + str(outside / 'created.txt') + '"'}),
                    ('read', 'Read', {'file_path': str(outside / 'keep.txt')}),
                    ('web', 'WebFetch', {'url': provider.endpoint + '/forbidden-live', 'prompt': 'Read'}),
                    ('mcp', 'mcp__live__send', {'value': 'forbidden'}),
                    ('fixture', 'mcp__fixture__fixture_read', {'snapshot_id': 'fixture-allowed', 'source_version': 'v1'}),
                    ('traversal', 'mcp__fixture__fixture_read', {'snapshot_id': '../keep.txt', 'source_version': 'v1'}),
                    ('agent', 'Agent', {'subagent_type': 'qualification-canary', 'description': 'synthetic', 'prompt': 'synthetic'}),
                    ('task', 'Task', {'subagent_type': 'qualification-canary', 'description': 'synthetic', 'prompt': 'synthetic'}),
                    ('skill', 'Skill', {'skill': 'qualification-canary'}),
                    ('search', 'ToolSearch', {'query': '+fixture', 'max_results': 5}),
                    ('deferred', 'DeferredToolPlaceholder', {'query': 'synthetic'})]
                blocks = ([{'type': 'tool_use', 'id': identifier, 'name': name, 'input': args} for identifier, name, args in attacks]
                    if provider.count == 1 else [{'type': 'text', 'text': 'LOCAL_ROLE_DONE'}])
                message = {'id': 'msg_role_' + str(provider.count), 'type': 'message', 'role': 'assistant',
                    'model': 'claude-opus-5-5', 'content': [], 'stop_reason': None, 'stop_sequence': None,
                    'usage': {'input_tokens': 1, 'output_tokens': 0}}
                output = []

                def event(kind, value):
                    output.append('event: ' + kind + '\ndata: ' + json.dumps(value) + '\n\n')

                event('message_start', {'type': 'message_start', 'message': message})
                for index, block in enumerate(blocks):
                    start = {**block, 'input': {}} if block['type'] == 'tool_use' else {'type': 'text', 'text': ''}
                    delta = {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])} if block['type'] == 'tool_use' else {'type': 'text_delta', 'text': block['text']}
                    event('content_block_start', {'type': 'content_block_start', 'index': index, 'content_block': start})
                    event('content_block_delta', {'type': 'content_block_delta', 'index': index, 'delta': delta})
                    event('content_block_stop', {'type': 'content_block_stop', 'index': index})
                event('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if provider.count == 1 else 'end_turn',
                    'stop_sequence': None}, 'usage': {'output_tokens': 1}})
                event('message_stop', {'type': 'message_stop'})
                self.reply(200, ''.join(output).encode(), 'text/event-stream')

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.endpoint = 'http://127.0.0.1:' + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


class RoleNativeQualificationTests(unittest.TestCase):
    def launch(self, kind, root, endpoint, binary):
        stage = root / 'workspace'
        (stage / '.claude').mkdir(parents=True)
        for name in ('empty-auth-home', 'tmp', 'config', 'outside'):
            (root / name).mkdir(exist_ok=True)
        (stage / 'CLAUDE.md').write_text('SYNTHETIC_AMBIENT_CONTEXT_DO_NOT_LOAD')
        (stage / '.mcp.json').write_text('{"mcpServers":{"live":{"type":"http","url":"' + endpoint + '/ambient"}}}')
        environment = worker_environment(root, auth_home=root / 'empty-auth-home')
        environment.update({'CLAUDE_CONFIG_DIR': str(root / 'config'), 'ANTHROPIC_API_KEY': 'synthetic-local-provider-only',
            'ANTHROPIC_BASE_URL': endpoint, 'HTTP_PROXY': endpoint, 'HTTPS_PROXY': endpoint, 'ALL_PROXY': endpoint,
            'NO_PROXY': '127.0.0.1,localhost', 'JAVIS_TEST_NATIVE_QUALIFICATION_CLI': str(binary)})
        process = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', str(Path(__file__).resolve()), '--role-worker', kind, str(stage)],
            cwd=stage, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=worker.process_flags() | (4 if os.name == 'nt' else 0))
        try:
            from improvement_sandbox import _bytes_hash
            barrier = root / 'trials' / '.executions' / (_bytes_hash(b'disposable-qualification-brain') + '.lock')
            lifetime = root / 'operations' / '.lifetimes' / (_bytes_hash(('qualification-' + kind + ':1').encode()) + '.lock')
            barrier.parent.mkdir(parents=True, exist_ok=True)
            lifetime.parent.mkdir(parents=True, exist_ok=True)
            group = worker.WorkerGroup(process.pid, recovery_fences=(barrier, lifetime), suspended=os.name == 'nt')
        except BaseException:
            process.kill()
            process.communicate(timeout=5)
            raise
        process.stdin.write('qualification_start\n')
        process.stdin.flush()
        return process, group

    @unittest.skipUnless(os.environ.get('JAVIS_TEST_NATIVE_QUALIFICATION_CLI'), 'Exact opt-in candidate only; no real provider fallback')
    def test_guardian_death_retains_both_recovery_fences(self):
        import ctypes
        from ctypes import wintypes
        from improvement_sandbox import _bytes_hash
        from improvement_native_lifetime import fence_busy
        binary = candidate(os.environ['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'])
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with qualification_directory(prefix='role-guardian-crash-') as root:
            provider = LocalProvider(root / 'outside', pending=True)
            process, group = self.launch('semantic', root, provider.endpoint, binary)
            handles, guardian = [], None
            try:
                self.assertTrue(provider.requested.wait(25))
                pids = group.inventory()
                self.assertGreaterEqual(len(pids), 2)
                handles = [kernel.OpenProcess(0x100000, False, pid) for pid in pids]
                self.assertTrue(all(handles))
                guardian = kernel.OpenProcess(0x100001, False, group._guardian.guardian_pid)
                self.assertTrue(guardian)
                self.assertTrue(kernel.TerminateProcess(guardian, 1))
                self.assertEqual(kernel.WaitForSingleObject(guardian, 5000), 0)
                with self.assertRaises(worker.WorkerGroupCleanupError):
                    group.close()
                brain_lock = root / 'trials' / '.executions' / (_bytes_hash(b'disposable-qualification-brain') + '.lock')
                attempt_lock = root / 'operations' / '.lifetimes' / (_bytes_hash(b'qualification-semantic:1') + '.lock')
                self.assertTrue(fence_busy(brain_lock))
                self.assertTrue(fence_busy(attempt_lock))
                barrier = TrialWorkspace(root / 'trials')
                self.assertIsNone(barrier.acquire_brain_execution('disposable-qualification-brain'))
                workspace = OperationWorkspace(root / 'operations', brain_barrier=barrier)
                self.assertFalse(workspace.cleanup_proven('qualification-semantic', 1, None))
                self.assertTrue(all(kernel.WaitForSingleObject(handle, 5000) == 0 for handle in handles))
                EVIDENCE.append({'scenario': 'actual-guardian-death-fail-closed', 'kind': 'semantic',
                    'production_billing': False, 'both_recovery_fences_retained': True,
                    'native_processes_exited': True, 'automatic_recovery_granted': False})
            finally:
                group._guardian.process.wait(timeout=5)
                for stream in (group._guardian.process.stdin, group._guardian.process.stdout):
                    if not stream.closed:
                        stream.close()
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
                    kernel.CloseHandle(handle)
                if guardian:
                    kernel.CloseHandle(guardian)
                process.communicate(timeout=5)
                provider.close()

    @unittest.skipUnless(os.environ.get('JAVIS_TEST_NATIVE_QUALIFICATION_CLI'), 'Exact opt-in candidate only; no real provider fallback')
    def test_host_death_cannot_release_the_brain_barrier_before_native_exit(self):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        binary = candidate(os.environ['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'])
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        for kind in ('propose', 'semantic'):
            with self.subTest(kind=kind), qualification_directory(prefix='role-owner-crash-') as root:
                outside = root / 'outside'
                outside.mkdir()
                (root / 'tmp').mkdir()
                provider = LocalProvider(outside, pending=True)
                environment = worker_environment(root, auth_home=root / 'empty-owner-home')
                environment['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'] = str(binary)
                owner = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', str(Path(__file__).resolve()),
                    '--role-owner', kind, str(root), provider.endpoint], env=environment,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=worker.process_flags())
                handles, barrier, monitor, images = [], None, None, []
                released, stop = threading.Event(), threading.Event()
                states = []
                try:
                    self.assertTrue(provider.requested.wait(25), 'Actual native query never reached local provider')
                    owner.stdin.write('inventory\n')
                    owner.stdin.flush()
                    import queue
                    response = queue.Queue()
                    threading.Thread(target=lambda: response.put(owner.stdout.readline()), daemon=True).start()
                    metadata = json.loads(response.get(timeout=5))
                    self.assertGreaterEqual(len(metadata['pids']), 2)
                    for pid in metadata['pids']:
                        handle = kernel.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
                        self.assertTrue(handle, 'Cannot retain an owned process handle')
                        handles.append(handle)
                        buffer = ctypes.create_unicode_buffer(32768)
                        length = wintypes.DWORD(len(buffer))
                        self.assertTrue(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)))
                        images.append(buffer.value)
                    path = Path(metadata['barrier'])
                    self.assertTrue(path.resolve().is_relative_to((root / 'trials').resolve()))
                    barrier = path.open('r+b', buffering=0)

                    def observe_release():
                        # Preopen the actual production lock file. Repeating its
                        # ACL setup here would hide a millisecond release race.
                        while not stop.is_set():
                            barrier.seek(0)
                            try:
                                msvcrt.locking(barrier.fileno(), msvcrt.LK_NBLCK, 1)
                            except OSError:
                                time.sleep(.0005)
                                continue
                            from improvement_native_lifetime import fence_busy
                            if fence_busy(path):
                                barrier.seek(0)
                                msvcrt.locking(barrier.fileno(), msvcrt.LK_UNLCK, 1)
                                time.sleep(.0005)
                                continue
                            states.extend(kernel.WaitForSingleObject(handle, 0) for handle in handles)
                            released.set()
                            return

                    monitor = threading.Thread(target=observe_release, daemon=True)
                    monitor.start()
                    self.assertFalse(released.wait(.02), 'Live host did not retain the brain barrier')
                    owner.kill()  # Actual host death: no Python finally/cleanup.
                    owner.wait(timeout=5)
                    self.assertTrue(released.wait(5), 'Dead host retained its brain barrier')
                    EVIDENCE.append({'scenario': 'actual-native-owner-death-observation', 'kind': kind, 'production_billing': False,
                        'owned_process_count': len(handles), 'process_images': images, 'states_at_barrier_release': states,
                        'states_after_owner_exit': [kernel.WaitForSingleObject(handle, 0) for handle in handles],
                        'all_exited_before_barrier_release': states == [0] * len(handles)})
                    self.assertEqual(states, [0] * len(handles), 'Recovery barrier became available while native children had not exited')
                    EVIDENCE.append({'scenario': 'actual-native-owner-death', 'kind': kind, 'production_billing': False,
                        'owned_process_count': len(handles), 'all_exited_before_barrier_release': True})
                finally:
                    stop.set()
                    if monitor is not None:
                        monitor.join(timeout=2)
                    if owner.poll() is None:
                        owner.kill()
                    owner.communicate(timeout=5)
                    for handle in handles:
                        self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0, 'Owned native child survived host death')
                        kernel.CloseHandle(handle)
                    if barrier is not None:
                        barrier.close()
                    provider.close()

    @unittest.skipUnless(os.environ.get('JAVIS_TEST_NATIVE_QUALIFICATION_CLI'), 'Exact opt-in candidate only; no real provider fallback')
    def test_real_typed_profiles_deny_all_ambient_tools_and_preserve_input_and_unknowns(self):
        binary = candidate(os.environ['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'])
        for kind in KINDS:
            with self.subTest(kind=kind), qualification_directory(prefix='role-qualification-') as directory:
                root = Path(directory)
                outside = root / 'outside'
                outside.mkdir()
                (outside / 'keep.txt').write_text('OUTSIDE_ROLE_CANARY')
                provider = LocalProvider(outside)
                process, group = self.launch(kind, root, provider.endpoint, binary)
                owned, alive_after_close = [], []
                try:
                    stdout, stderr = process.communicate(timeout=35)
                finally:
                    owned = _job_pids(group)
                    group.close()
                    alive_after_close = [pid for pid in owned if _alive(pid)]
                    deadline = time.monotonic() + 5
                    while any(_alive(pid) for pid in owned) and time.monotonic() < deadline:
                        time.sleep(.02)
                    if process.poll() is None:
                        process.communicate(timeout=5)
                    provider.close()
                self.assertFalse(any(_alive(pid) for pid in owned), 'Owned native processes still alive before directory cleanup')
                self.assertFalse(alive_after_close, 'Job close returned before native descendants exited')
                self.assertEqual(process.returncode, 0, stderr[-2000:])
                value = json.loads(stdout)
                # The parent first drained the actual job. A completed SDK query
                # does not permit the child to delete input while its descendants
                # may still be closing. Use the real workspace proof afterward.
                workspace = OperationWorkspace(root / 'operations', brain_barrier=TrialWorkspace(root / 'trials'))
                receipt = value['receipt']
                self.assertTrue(workspace.cleanup_proven(receipt['operation_id'], 1, receipt))
                value['plaintext_cleaned'] = not Path(receipt['path']).exists()
                self.assertEqual(value['kind'], kind)
                self.assertEqual(value['order'], ['before_query'])
                self.assertTrue(value['plaintext_cleaned'])
                self.assertFalse(value['worker_has_console'])
                self.assertFalse([event for event in value['events'] if event['type'] == 'error'], value['events'])
                receipts = [event['receipt'] for event in value['events'] if event['type'] == 'operation_installation_receipt']
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]['operation_id'], value['operation_id'])
                self.assertEqual(receipts[0]['input_hash'], value['input_hash'])
                self.assertEqual(receipts[0]['coverage']['tool_inventory'], 'observed')
                self.assertEqual(receipts[0]['coverage']['model'], 'observed')
                self.assertEqual(receipts[0]['coverage']['reasoning'], 'unknown')
                self.assertEqual([event['content'] for event in value['events'] if event['type'] == 'artifact'], ['LOCAL_ROLE_DONE'])
                self.assertEqual([event['status'] for event in value['broker_events']], ['fixture_miss', 'fixture_miss'])
                messages = [request for request in provider.requests if request['path'] == '/v1/messages']
                self.assertEqual(len(messages), 2)
                self.assertEqual(set(messages[-1]['results']), {'bash', 'read', 'web', 'mcp', 'fixture', 'traversal', 'agent', 'task', 'skill', 'search', 'deferred'})
                self.assertTrue(all(messages[-1]['results'].values()))
                for request in provider.requests:
                    self.assertEqual(request['method'], 'POST')
                    self.assertIn(request['path'], {'/v1/messages', '/v1/messages/count_tokens'})
                    self.assertTrue(request['dummy_auth_only'])
                    self.assertFalse(request['ambient_markers'])
                    self.assertFalse(request['fast_requested'])
                for request in messages:
                    self.assertEqual(request['model'], 'claude-opus-5-5')
                    self.assertEqual(request['effort'], 'medium')
                    self.assertEqual(request['tools'], ['mcp__fixture__fixture_read'])
                    self.assertTrue(request['authorized_data_present'])
                self.assertEqual((outside / 'keep.txt').read_text(), 'OUTSIDE_ROLE_CANARY')
                self.assertFalse((outside / 'created.txt').exists())
                self.assertFalse((outside / 'plugin-hook.txt').exists())
                self.assertFalse(list((root / 'config' / 'projects').rglob('*.jsonl')))
                self.assertEqual(persisted_input_markers(root), [], 'Native left synthetic private input outside the cleaned workspace')
                self.assertEqual({item['kind'] for item in value['spawns']}, {'version', 'query'})
                self.assertTrue(all(item['creationflags'] & worker.winproc.no_window() for item in value['spawns']))
                self.assertFalse(any(_alive(item['pid']) for item in value['spawns']))
                self.assertEqual(candidate(binary), binary)
                EVIDENCE.append({'scenario': 'typed-role-local-enforcement', 'kind': kind, 'production_billing': False,
                    'owned_before_close': owned, 'alive_immediately_after_close': alive_after_close,
                    'cache_path_lengths': [len(str(path)) for path in Path('\\\\?\\' + str(root / 'empty-auth-home')).rglob('*') if path.is_file()],
                    'profile_hash': value['profile_hash'], 'runtime': {'version': CANDIDATE_VERSION, 'sha256': CANDIDATE_HASH, 'sdk': SDK_VERSION},
                    'requests': provider.requests, 'spawns': value['spawns'], 'plaintext_cleaned': True,
                    'ambient_hooks_ran': False, 'session_transcripts_persisted': False,
                    'input_markers_persisted_after_cleanup': False,
                    'model_coverage': 'observed', 'actual_reasoning': 'unknown', 'actual_input_use': 'unknown',
                    'scope': 'Native typed-profile enforcement only; no auth readiness or actual worker/CallLedger feasibility claim.'})

    @unittest.skipUnless(os.environ.get('JAVIS_TEST_NATIVE_QUALIFICATION_CLI'), 'Exact opt-in candidate only; no real provider fallback')
    def test_each_pending_role_query_is_owned_by_the_actual_windows_job(self):
        binary = candidate(os.environ['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'])
        for kind in KINDS:
            with self.subTest(kind=kind), qualification_directory(prefix='role-qualification-cancel-') as directory:
                root = Path(directory)
                outside = root / 'outside'
                outside.mkdir()
                provider = LocalProvider(outside, pending=True)
                process, group = self.launch(kind, root, provider.endpoint, binary)
                try:
                    self.assertTrue(provider.requested.wait(25), 'Candidate did not reach local pending query')
                    pids = _job_pids(group)
                    self.assertIn(process.pid, pids)
                    self.assertGreaterEqual(len(pids), 2)
                    group.close()
                    _, stderr = process.communicate(timeout=5)
                    deadline = time.monotonic() + 5
                    while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertFalse(any(_alive(pid) for pid in pids))
                    self.assertEqual(provider.count, 1)
                    self.assertTrue(all(request.get('dummy_auth_only') for request in provider.requests))
                    self.assertTrue(all(request['path'] in {'/v1/messages', '/v1/messages/count_tokens'} for request in provider.requests))
                    self.assertIn('qualification-before-query', stderr)
                    EVIDENCE.append({'scenario': 'typed-role-pending-query-cancellation', 'kind': kind, 'production_billing': False,
                        'owned_process_count': len(pids), 'all_owned_processes_closed': True, 'query_count': 1,
                        'scope': 'Actual owned native profile process tree; no production ledger or auth claim.'})
                finally:
                    group.close()
                    if process.poll() is None:
                        process.communicate(timeout=5)
                    provider.close()

    def test_profile_fixtures_use_typed_operations_and_actual_renderers_without_runtime_probes(self):
        pin = {'provider': 'anthropic-cli', 'model': 'claude-opus-5-5', 'reasoning': 'medium',
               'cli_path': '/never-executed', 'sdk_version': SDK_VERSION}
        from improvement_operation_profile import operation_user_prompt
        from improvement_operation_protocol import operation_materialization
        for kind in KINDS:
            manifest = operation_materialization(materialization(kind, pin))
            body = json.loads(operation_user_prompt(manifest))
            self.assertNotIn('authorized_input', body)
            self.assertIn({'propose': 'proposer_task', 'repair': 'repair_task', 'semantic': 'semantic_task'}[kind], body)
            self.assertIn(DATA_MARKER, json.dumps(body))


def role_owner(root, kind, endpoint):
    binary = candidate(os.environ['JAVIS_TEST_NATIVE_QUALIFICATION_CLI'])
    barrier = TrialWorkspace(root / 'trials').acquire_brain_execution('disposable-qualification-brain')
    if barrier is None:
        raise RuntimeError('qualification_owner_brain_busy')
    process, group = RoleNativeQualificationTests().launch(kind, root, endpoint, binary)
    try:
        if sys.stdin.readline().strip() != 'inventory':
            raise ValueError('qualification_inventory_not_requested')
        print(json.dumps({'pids': _job_pids(group), 'barrier': barrier.name}), flush=True)
        sys.stdin.readline()
    finally:
        group.close()
        process.communicate(timeout=5)
        barrier.close()


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--role-worker' and sys.argv[2] in KINDS:
        role_worker(Path(sys.argv[3]), sys.argv[2])
    elif len(sys.argv) == 5 and sys.argv[1] == '--role-owner' and sys.argv[2] in KINDS:
        role_owner(Path(sys.argv[3]), sys.argv[2], sys.argv[4])
    else:
        result = unittest.main(exit=False)
        if EVIDENCE and os.environ.get('JAVIS_TEST_NATIVE_ROLE_QUALIFICATION_EVIDENCE'):
            target = Path(os.environ['JAVIS_TEST_NATIVE_ROLE_QUALIFICATION_EVIDENCE'])
            target.write_text(json.dumps({'label': 'synthetic/native-role-profile-qualification-only', 'passed': result.result.wasSuccessful(),
                'profile_hash': native.profile_hash(), 'production_billing': False, 'scenarios': EVIDENCE}, indent=2))
        raise SystemExit(not result.result.wasSuccessful())
