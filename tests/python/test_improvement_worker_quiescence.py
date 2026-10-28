"""Windows job ownership must outlive asynchronous native termination."""
from _paths import ROOT, SERVER  # noqa: F401

import ctypes
import asyncio
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

import improvement_worker as worker

_REAL_POPEN = subprocess.Popen


class Accounting(ctypes.Structure):
    # The documented Win32 ABI, including on non-Windows test hosts.
    _fields_ = [('user', ctypes.c_int64), ('kernel', ctypes.c_int64),
        ('period_user', ctypes.c_int64), ('period_kernel', ctypes.c_int64),
        ('faults', ctypes.c_uint32), ('total', ctypes.c_uint32),
        ('active', ctypes.c_uint32), ('terminated', ctypes.c_uint32)]


class WorkerQuiescenceTests(unittest.TestCase):
    def group(self, counts):
        observed = []

        def query(handle, information_class, buffer, size, returned):
            self.assertEqual(handle, 123)
            if information_class == 3:
                return True  # zero-initialized job PID list
            self.assertEqual(information_class, 1)  # JobObjectBasicAccountingInformation
            self.assertEqual(size, ctypes.sizeof(Accounting))
            active = counts.pop(0)
            ctypes.cast(buffer, ctypes.POINTER(Accounting)).contents.active = active
            observed.append(active)
            return True

        kernel = SimpleNamespace(TerminateJobObject=Mock(return_value=True),
            QueryInformationJobObject=Mock(side_effect=query), CloseHandle=Mock(return_value=True),
            OpenProcess=Mock(return_value=456), WaitForSingleObject=Mock(return_value=0))
        subject = object.__new__(worker.WorkerGroup)
        subject.pid, subject.handle, subject._kernel = 999, 123, kernel
        subject._close_lock = threading.Lock()
        subject._process_handles = {}
        return subject, kernel, observed

    def test_close_waits_for_the_job_to_be_empty_before_releasing_its_handle(self):
        subject, kernel, observed = self.group([2, 1, 0])
        with patch.object(worker.os, 'name', 'nt'):
            subject.close()
        self.assertEqual(observed, [2, 1, 0])
        kernel.TerminateJobObject.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)
        self.assertIsNone(subject.handle)
        self.assertIsNone(subject.pid)

    def test_failed_quiescence_observation_retains_ownership_for_a_retry(self):
        subject, kernel, _ = self.group([])
        kernel.QueryInformationJobObject.side_effect = None
        kernel.QueryInformationJobObject.return_value = False
        with patch.object(worker.os, 'name', 'nt'), self.assertRaises(OSError):
            subject.close()
        kernel.CloseHandle.assert_not_called()
        self.assertEqual(subject.handle, 123)
        self.assertEqual(subject.pid, 999)

    def test_failed_termination_does_not_claim_the_job_closed(self):
        subject, kernel, _ = self.group([])
        kernel.TerminateJobObject.return_value = False
        with patch.object(worker.os, 'name', 'nt'), self.assertRaises(OSError):
            subject.close()
        self.assertEqual([call.args[1] for call in kernel.QueryInformationJobObject.call_args_list], [3])
        kernel.CloseHandle.assert_not_called()
        self.assertEqual(subject.handle, 123)

    def test_a_still_active_job_retains_its_handle_after_the_observation_deadline(self):
        subject, kernel, observed = self.group([1])
        with patch.object(worker.os, 'name', 'nt'), patch.object(worker.time, 'monotonic', side_effect=[0, 6]), self.assertRaises(OSError):
            subject.close()
        self.assertEqual(observed, [1])
        kernel.CloseHandle.assert_not_called()
        self.assertEqual(subject.handle, 123)

    def test_pid_list_and_accounting_removal_do_not_replace_process_handle_waits(self):
        subject, kernel, _ = self.group([])

        class Members(ctypes.Structure):
            _fields_ = [('assigned', ctypes.c_uint32), ('count', ctypes.c_uint32), ('pids', ctypes.c_size_t * 4096)]

        listed = []

        def query(handle, information_class, buffer, size, returned):
            if information_class == 3:
                members = ctypes.cast(buffer, ctypes.POINTER(Members)).contents
                if not listed:
                    members.assigned = members.count = 1
                    members.pids[0] = 987
                listed.append(True)
            # Accounting is already zero after removal, while the captured
            # process object has not yet reached its signaled exit state.
            return True

        kernel.QueryInformationJobObject.side_effect = query
        kernel.WaitForSingleObject.side_effect = [0x102, 0]
        with patch.object(worker.os, 'name', 'nt'):
            subject.close()
        kernel.OpenProcess.assert_called_once_with(0x100000, False, 987)
        self.assertGreaterEqual(kernel.WaitForSingleObject.call_count, 2)
        self.assertEqual([entry.args[0] for entry in kernel.CloseHandle.call_args_list], [456, 123])

    def test_a_dropped_birth_notification_cannot_become_a_cleanup_proof(self):
        subject, kernel, _ = self.group([])
        subject._inventory_port = 999
        subject._births, subject._inventory_error = 1, False

        def query(handle, information_class, buffer, size, returned):
            if information_class == 1:
                value = ctypes.cast(buffer, ctypes.POINTER(Accounting)).contents
                value.total, value.active = 2, 0
            return True

        kernel.QueryInformationJobObject.side_effect = query
        with patch.object(worker.os, 'name', 'nt'), patch.object(worker.time, 'monotonic', side_effect=[0, 6]), \
                self.assertRaises(worker.WorkerGroupCleanupError):
            subject.close()
        kernel.CloseHandle.assert_not_called()
        self.assertEqual(subject.handle, 123)

    def test_durable_native_fences_block_both_brain_and_attempt_recovery(self):
        from improvement_sandbox import TrialWorkspace, _bytes_hash
        from improvement_native_lifetime import fence_directory
        with tempfile.TemporaryDirectory() as directory:
            workspace = TrialWorkspace(Path(directory) / 'trials')
            for namespace, key in (('.executions', 'synthetic-brain'), ('.lifetimes', 'trial-a:1')):
                lock = workspace.root / namespace / (_bytes_hash(key.encode()) + '.lock')
                fence = fence_directory(lock)
                fence.mkdir(parents=True)
                (fence / 'crashed-guardian.fence').write_text('native-cleanup-unproven')
                self.assertIsNone(workspace._try_lifetime_lock(namespace, key))
                self.assertTrue((fence / 'crashed-guardian.fence').exists())


class TransportQuiescenceTests(unittest.IsolatedAsyncioTestCase):
    async def check_ownership(self, namespace, *, cancel):
        from improvement_native_transport import execute_worker
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        entered, release = threading.Event(), threading.Event()
        identity = {'attempt': 1, 'input_hash': 'd' * 64}
        if namespace == 'trial':
            identity['trial_id'] = 'trial-quiescence'
        else:
            identity.update(operation_id='operation-quiescence', role='improver', purpose='improve_propose')
        packets = [{'version': 1, 'sequence': 1, 'type': 'worker_ready', **identity, 'capability_hash': 'c' * 64},
            {'version': 1, 'sequence': 2, 'type': 'engine_start_requested', **identity},
            {'version': 1, 'sequence': 3, 'type': 'worker_complete', **identity, 'tool_denials': [],
             **({'fixture_events': []} if namespace == 'trial' else {})}]

        class Process:
            pid, returncode = 987654321, 0

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b''.join(json.dumps(packet).encode() + b'\n' for packet in packets))
                self.stderr = io.BytesIO()

            def poll(self):
                return self.returncode

            def wait(self, **kwargs):
                return self.returncode

        process = Process()
        attempts = []

        class Group:
            def __init__(self, pid, **kwargs):
                if pid != process.pid:
                    raise AssertionError('Unexpected process ownership')

            def close(self):
                attempts.append(time.monotonic())
                entered.set()
                if not release.is_set():
                    raise worker.WorkerGroupCleanupError('synthetic_job_observation_unavailable')

        def spawn(command, **kwargs):
            if str(command[-1]).endswith(('improvement_worker.py', 'improvement_operation_worker.py')):
                return process
            return _REAL_POPEN(command, **kwargs)  # Disposable Windows ACL metadata only.

        async def marker():
            pass

        async def run():
            return [event async for event in execute_worker(namespace,
                {'deadline_at': time.time() + 10, 'capability_hash': 'c' * 64}, {**identity, 'path': str(path)},
                config_pin={}, workspace_root=path, brain_id='synthetic-brain', before_engine_start=marker)]

        with patch.object(subprocess, 'Popen', side_effect=spawn), patch.object(worker, 'WorkerGroup', Group):
            task = asyncio.create_task(run())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                if cancel:
                    task.cancel()
                    task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done(), 'Transport detached ownership before native job drain could be proven')
                self.assertFalse(process.stdin.closed)
                release.set()
                if cancel:
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual(len(await task), 3)
                self.assertGreaterEqual(len(attempts), 2)
                self.assertTrue(all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_trial_and_role_transports_retain_unproven_job_ownership(self):
        for namespace in ('trial', 'operation'):
            with self.subTest(namespace=namespace):
                await self.check_ownership(namespace, cancel=False)

    async def test_repeated_caller_cancellation_cannot_detach_failed_job_cleanup(self):
        for namespace in ('trial', 'operation'):
            with self.subTest(namespace=namespace):
                await self.check_ownership(namespace, cancel=True)


if __name__ == '__main__':
    unittest.main()
