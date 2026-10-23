"""Developer smoke gates never discover auth or query a model in these tests."""
from _paths import ROOT, SERVER  # noqa: F401

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = ROOT / "tools" / "run_agent_skill_trial_smoke.py"


class TrialSmokeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Controlled synthetic feasibility smoke is missing")
        self.root = Path(tempfile.mkdtemp(prefix="trial-smoke-test-"))
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps({"model": {"main": {"provider": "anthropic-cli", "model": "selected-model"},
            "reasoning": "medium", "claude_auth": "subscription", "fast": False}}), encoding="utf-8")
        self.cli = self.root / "unverified.exe"
        self.cli.write_bytes(b"not a verified executable")

    def command(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)

    def arguments(self, output=None):
        return ["--settings", str(self.settings), "--cli", str(self.cli), "--output-root", str(output or self.root / "output")]

    def test_unverified_binary_is_unavailable_without_auth_query_or_trial_reservation(self):
        result = self.command(*self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "unavailable")
        self.assertFalse(report["inference_attempted"])
        self.assertIsNone(report["trial"])
        self.assertEqual(report["requested"]["model"], "selected-model")
        self.assertEqual(report["requested"]["reasoning"], "medium")
        self.assertFalse((self.root / "output" / "state" / "resonance.sqlite3").exists())
        self.assertEqual(self.cli.read_bytes(), b"not a verified executable")

    def test_cli_is_closed_and_all_host_paths_are_explicit_absolute(self):
        for extra in (["--model", "override"], ["--reasoning", "max"], ["--provider", "fallback"],
                      ["--fake"], ["--li"], ["--timeout-seconds", "301"], ["--timeout-seconds", "0"]):
            with self.subTest(extra=extra):
                self.assertNotEqual(self.command(*self.arguments(), *extra).returncode, 0)
        relative = self.arguments()
        relative[1] = "settings.json"
        self.assertNotEqual(self.command(*relative).returncode, 0)
        self.assertFalse((self.root / "output").exists())

    def test_existing_output_is_never_reused_or_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("owned history", encoding="utf-8")
        result = self.command(*self.arguments(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "owned history")
        self.assertEqual(list(output.iterdir()), [marker])

    def test_sensitive_selection_is_unavailable_and_never_echoed_to_report(self):
        secret = "sk-" + "a" * 40
        self.settings.write_text(json.dumps({"model": {"main": {"provider": "anthropic-cli", "model": secret},
            "reasoning": "medium"}}), encoding="utf-8")
        result = self.command(*self.arguments())
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "unavailable")
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, (self.root / "output" / "report.json").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows legacy path budget")
    def test_deep_output_is_rejected_before_creation_selection_or_native_verification(self):
        # The retained developer run failed at this actual output-root depth.
        units = len(str(self.root).encode("utf-16-le")) // 2
        output = self.root / ("x" * (153 - units - 1))
        self.assertEqual(len(str(output).encode("utf-16-le")) // 2, 153)
        body = """
def forbidden(*args,**kwargs):raise AssertionError('deep output reached selection or native verification')
smoke._selection_module=forbidden
smoke._adapter_type=forbidden
code=smoke.main(ARGS)
assert code==2
assert not Path(OUTPUT).exists()
""".replace("ARGS", repr(self.arguments(output))).replace("OUTPUT", repr(str(output)))
        report = self.child(body)
        self.assertEqual(report["reason"], "smoke_output_path_too_long")
        self.assertFalse(report["engine_start_marked"])

    @unittest.skipUnless(os.name == "nt", "Windows UTF-16 path units")
    def test_windows_path_budget_counts_non_bmp_characters_as_two_units(self):
        body = """
path=Path('C:/'+chr(0x1F600)*60)
try:smoke._check_output_budget(path)
except ValueError as error:assert str(error)=='smoke_output_path_too_long'
else:raise AssertionError('UTF-16 path limit was counted as Unicode code points')
print(json.dumps({'units':smoke._windows_units('x'+chr(0x1F600))}))
"""
        report = self.child(body)
        self.assertEqual(report["units"], 3)

    def child(self, body):
        bootstrap = ("import importlib.util,sys,json; from pathlib import Path; "
                     "spec=importlib.util.spec_from_file_location('smoke'," + repr(str(SCRIPT)) + "); "
                     "smoke=importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)\n")
        result = subprocess.run([sys.executable, "-c", bootstrap + body], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_supported_dry_run_never_calls_adapter_verify_or_auth_and_retains_pin(self):
        body = """
from types import SimpleNamespace
selection=SimpleNamespace(read_selection=lambda path: {'provider':'anthropic-cli','model':'selected-model','reasoning':'medium','auth_mode':'subscription','fast':False},
 create_pin=lambda settings,cli: {'model':'selected-model','reasoning':'medium','cli_hash':'a'*64,'profile_hash':'b'*64})
class Adapter:
 def __init__(self,*a,**kw): raise AssertionError('dry-run constructed auth adapter')
smoke._selection_module=lambda: selection
smoke._adapter_type=lambda: Adapter
smoke.main(ARGS)
""".replace("ARGS", repr(self.arguments()))
        report = self.child(body)
        self.assertEqual(report["status"], "dry_run")
        self.assertEqual(report["pin"]["reasoning"], "medium")
        self.assertFalse(report["inference_attempted"])
        self.assertFalse(report["runtime_capability"]["verified"])
        self.assertIsNone(report["trial"])

    def test_selection_failure_does_not_echo_exception_details(self):
        body = """
from types import SimpleNamespace
def denied(path): raise ValueError('PRIVATE SYNTHETIC FAILURE Authorization: Bearer abcdefghijklmnop')
smoke._selection_module=lambda: SimpleNamespace(read_selection=denied)
smoke.main(ARGS)
""".replace("ARGS", repr(self.arguments()))
        report = self.child(body)
        self.assertEqual(report["status"], "unavailable")
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("Bearer", json.dumps(report))

    def test_unavailable_preflight_cannot_reserve_even_when_live_branch_is_selected_programmatically(self):
        # Exercise the gate with only trusted test dependencies; never invoke --live or native auth.
        body = """
from types import SimpleNamespace
options=SimpleNamespace(settings=Path(SETTINGS),cli=Path(CLI),output_root=Path(OUTPUT),timeout_seconds=300,live=True)
selection=SimpleNamespace(read_selection=lambda path: {'provider':'anthropic-cli','model':'selected-model','reasoning':'medium','auth_mode':'subscription','fast':False},
 create_pin=lambda settings,cli: {'model':'selected-model','reasoning':'medium','cli_hash':'a'*64,'profile_hash':'b'*64})
class Adapter:
 def __init__(self,*a,**kw):pass
 def verify(self):return {'supported':False,'reason':'native_enforcement_unverified'}
smoke.parser=lambda:SimpleNamespace(parse_args=lambda argv:options)
smoke._selection_module=lambda:selection
smoke._adapter_type=lambda:Adapter
def forbidden(*a,**kw):raise AssertionError('unavailable preflight reached stores/reservation')
smoke._run_one=forbidden
smoke.main([])
""".replace("SETTINGS", repr(str(self.settings))).replace("CLI", repr(str(self.cli))).replace("OUTPUT", repr(str(self.root / "unavailable")))
        report = self.child(body)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["runtime_capability"]["reason"], "native_enforcement_unverified")
        self.assertIsNone(report["trial"])
        self.assertFalse(report["inference_attempted"])

    def _synthetic_body(self):
        body = """
import asyncio,hashlib
from types import SimpleNamespace
from unittest.mock import patch
options=SimpleNamespace(settings=Path(SETTINGS),cli=Path(CLI),output_root=Path(OUTPUT),timeout_seconds=300,live=False)
smoke._prepare_root(options.output_root)
pin={'version':1,'provider':'anthropic-cli','engine_id':'claude-sdk','model':'selected-model','reasoning':'medium',
 'cli_path':CLI,'cli_hash':'a'*64,'cli_version':'synthetic','sdk_version':'synthetic',
 'profile_version':'synthetic-no-inference','profile_hash':'b'*64}
class Adapter:
 def __init__(self,pin,*,workspace_root=None,before_engine_start=None): self.before=before_engine_start
 def verify(self): return {'supported':True,'capability_hash':'c'*64}
 async def execute(self,request,workspace):
  ident={k:workspace[k] for k in ('trial_id','attempt','input_hash')}
  def packet(n,t,**kw): return {'version':1,'sequence':n,'type':t,**ident,**kw}
  yield packet(1,'worker_ready',capability_hash='c'*64)
  await self.before()
  yield packet(2,'engine_start_requested')
  raw='{"value":"alpha-3"}\\r\\n'
  events=[{'type':'observation','model':'selected-model','message_id':'test-request','session_id':'test-native-session',
    'usage':{'input_tokens':3,'output_tokens':2,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}},
   {'type':'artifact','content':raw,'hash':hashlib.sha256(raw.encode()).hexdigest()},
   {'type':'terminal','subtype':'success','is_error':False,'artifact_status':'observed'}]
  for n,event in enumerate(events,3):yield packet(n,'engine_event',event=event)
  yield packet(6,'worker_complete',fixture_events=[],tool_denials=[])
import subprocess
original_popen=subprocess.Popen
def no_engine(command,*args,**kwargs):
 if Path(command[0]).name.lower() not in {'whoami','whoami.exe','icacls','icacls.exe'}:
  raise AssertionError('test adapter must not start an engine process')
 return original_popen(command,*args,**kwargs)
with patch('subprocess.Popen',side_effect=no_engine):
 report=smoke._run_one(options,pin,{'supported':True,'capability_hash':'c'*64},selected_engine=lambda:dict(pin),
  adapter_factory=Adapter,synthetic_no_inference=True)
assert report['trial']['status']=='completed', report
assert report['trial']['variant']=='baseline' and report['trial']['repeat']==1
assert len(report['ledger'])==1 and report['ledger'][0]['status']=='ok'
assert report['usage']['tokens_in']==3 and report['usage']['tokens_out']==2
assert report['case_counts']=={'tuning':1,'qualification':1,'regression':1}
assert report['observed']['reasoning']['status']=='unknown'
assert report['encryption']['codec']=='Fernet'
key=(options.output_root/'private'/'smoke.key').read_bytes()
from cryptography.fernet import Fernet
blobs=list((options.output_root/'state'/'assets').rglob('*.enc'))
assert blobs
assert all(path.read_bytes().startswith(b'enc:') for path in blobs)
assert any(Fernet(key).decrypt(path.read_bytes()[4:]).startswith(b'revision-v1:') for path in blobs)
assert not any(key.decode() in str(value) for value in report.values())
assert report['evidence']['trace_count']==6
assert report['evidence']['evaluation']=={'verdict':'pass','mandatory_verdict':'pass','business_verdict':'pass','reason':'evaluated'}
assert report['evidence']['verdict']=='unknown'
assert report['evidence']['execution_checks']
assert all(set(item)=={'id','verdict','reason'} for item in report['evidence']['execution_checks'])
assert any(item['verdict']=='unknown' for item in report['evidence']['execution_checks'])
import base64
result_blob=next(path for path in blobs if path.stem==report['evidence']['results'][0]['blob_hash'])
stored=json.loads(base64.b64decode(Fernet(key).decrypt(result_blob.read_bytes()[4:]).split(b':',1)[1]))
assert report['evidence']['evaluation']=={key:stored['evaluation'][key] for key in ('verdict','mandatory_verdict','business_verdict','reason')}
assert report['evidence']['execution_checks']==[{key:row[key] for key in ('id','verdict','reason')} for row in stored['mandatory']]
import sqlite3
with sqlite3.connect(options.output_root/'state'/'resonance.sqlite3') as database:
 assert database.execute('SELECT COUNT(*) FROM goals').fetchone()[0]==0
 assert database.execute('SELECT COUNT(*) FROM improvement_qualification').fetchone()[0]==0
assert not (options.output_root/'state'/'team.sqlite3').exists()
assert report['production_context_coverage']=='unavailable'
assert report['inference_attempted'] is False
raw=smoke._write_report(options.output_root,report)
assert json.loads(Fernet(key).decrypt((options.output_root/'report.json.enc').read_bytes()[4:]))==json.loads(raw)
print(raw)
""".replace("SETTINGS", repr(str(self.settings))).replace("CLI", repr(str(self.cli))).replace("OUTPUT", repr(str(self.root / "synthetic")))
        return body

    def test_real_encrypted_stores_runner_ledger_and_usage_with_no_inference_test_adapter(self):
        report = self.child(self._synthetic_body())
        self.assertEqual(report["surface"], "controlled_synthetic_engine_feasibility")
        self.assertEqual(report["inference_kind"], "synthetic_no_inference")

    def _main_recovery_body(self, failure):
        body = self._synthetic_body().split("with patch('subprocess.Popen',side_effect=no_engine):", 1)[0]
        body = body.replace("smoke._prepare_root(options.output_root)\n", "").replace("live=False)", "live=True)")
        if failure == "cancel":
            body = body.replace("  yield packet(2,'engine_start_requested')", "  yield packet(2,'engine_start_requested')\n  raise asyncio.CancelledError()")
        body += """
smoke.parser=lambda:SimpleNamespace(parse_args=lambda argv:options)
smoke._selection_module=lambda:SimpleNamespace(read_selection=lambda path:{'provider':'anthropic-cli','model':'selected-model','reasoning':'medium','auth_mode':'subscription','fast':False},create_pin=lambda *args:dict(pin))
smoke._adapter_type=lambda:Adapter
original_run=smoke._run_one
def controlled_run(*args,**kwargs):
 kwargs['synthetic_no_inference']=True
 __FAILURE_ACTION__
smoke._run_one=controlled_run
with patch('subprocess.Popen',side_effect=no_engine):
 try:code=smoke.main([],_synthetic_no_inference=True)
 except (asyncio.CancelledError,KeyboardInterrupt):code='escaped'
assert (options.output_root/'report.json').is_file(), 'marked call escaped without a standalone report'
report=json.loads((options.output_root/'report.json').read_text(encoding='utf-8'))
assert len(report['ledger'])==1 and report['ledger'][0]['engine_at'] is not None
assert report['engine_start_marked'] is True
assert report['inference_kind']=='synthetic_no_inference' and report['inference_attempted'] is False
assert (options.output_root/'report.json.enc').is_file()
assert 'PRIVATE SYNTHETIC FAILURE' not in json.dumps(report)
ASSERTIONS
"""
        actions = {
            "cancel": "return original_run(*args,**kwargs)",
            "interrupt": "original_run(*args,**kwargs)\n raise KeyboardInterrupt()",
            "usage": "import improvement_usage\n def unavailable(*a):raise OSError('PRIVATE SYNTHETIC FAILURE')\n improvement_usage.trial_usage=unavailable\n return original_run(*args,**kwargs)",
        }
        assertions = {
            "cancel": "assert report['status']=='cancelled' and code==130\nassert report['trial']['status']=='unknown'\nassert report['ledger'][0]['status']=='interrupted'\nassert report['usage']['tokens_in'] is None and report['usage']['tokens_out'] is None",
            "interrupt": "assert report['status']=='cancelled' and code==130\nassert report['trial']['status']=='completed'\nassert report['ledger'][0]['status']=='ok'\nassert report['usage']['tokens_in']==3 and report['usage']['tokens_out']==2",
            "usage": "assert report['usage'] is None and report['recovery_coverage']=='usage_unavailable'\nassert report['trial']['status']=='unknown'\nassert report['ledger'][0]['status']=='interrupted'",
        }[failure]
        return body.replace("__FAILURE_ACTION__", actions[failure]).replace("ASSERTIONS", assertions)

    def test_actual_marked_call_cancellation_writes_recoverable_report_and_nonzero_exit(self):
        self.child(self._main_recovery_body("cancel"))

    def test_keyboard_interrupt_after_marked_call_writes_report_and_nonzero_exit(self):
        self.child(self._main_recovery_body("interrupt"))

    def test_usage_recovery_failure_cannot_erase_durable_engine_marker_or_synthetic_label(self):
        self.child(self._main_recovery_body("usage"))


if __name__ == "__main__":
    unittest.main()
