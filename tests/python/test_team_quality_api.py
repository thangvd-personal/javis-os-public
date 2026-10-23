"""Owner routes for opt-in quality contracts and non-blocking usage (fake engines only)."""
from _paths import ROOT, SERVER  # noqa: F401
import os
import asyncio
import tempfile
import unittest
from pathlib import Path

os.environ['JAVIS_STATE_DIR'] = tempfile.mkdtemp(prefix='javis-quality-api-')
BRAIN = Path(tempfile.mkdtemp(prefix='javis-quality-brain-'))
OTHER = Path(tempfile.mkdtemp(prefix='javis-quality-other-'))
(BRAIN / 'agents').mkdir()
for slug in ('leader', 'maker'):
    (BRAIN / 'agents' / f'{slug}.md').write_text(
        f'---\nname: {slug}\nrole: Helper\n---\nStay in scope.\n', encoding='utf-8')

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class QualityRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app, base_url='http://127.0.0.1:8080')
        main._TEAM_API.deps.spawn = lambda coro: coro.close() if hasattr(coro, 'close') else None
        main._TEAM_API.deps.notify = lambda *args: None

    def setUp(self):
        response = self.client.post(f'/teams?brain={BRAIN}', json={
            'name': self.id(), 'members': ['leader', 'maker'], 'leader_slug': 'leader'})
        self.assertEqual(response.status_code, 200, response.text)
        self.team = response.json()['team']
        self.base = f"/teams/{self.team['team_id']}"
        self.query = f'?brain={BRAIN}'

    def test_policy_is_opt_in_and_observe_only(self):
        response = self.client.get(self.base + '/quality-policy' + self.query)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['policy']['enabled'])
        response = self.client.put(self.base + '/quality-policy' + self.query, json={
            'enabled': True, 'limit_migration': 'observe', 'reference_usd': 0.01})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['policy']['cost_behavior'], 'observe_only')
        view = self.client.get(self.base + self.query).json()['team']
        self.assertTrue(view['quality_policy']['enabled'])

    def test_invalid_policy_does_not_mutate(self):
        for value in (-1, 'NaN', 'Infinity', True):
            response = self.client.put(self.base + '/quality-policy' + self.query, json={
                'enabled': True, 'limit_migration': 'observe', 'reference_usd': value})
            self.assertEqual(response.status_code, 400, response.text)
        response = self.client.put(self.base + '/quality-policy' + self.query,
                                   json={'cost_behavior': 'stop'})
        self.assertEqual(response.status_code, 400, response.text)

    def test_template_versions_and_owner_defaults(self):
        self.assertIsNone(self.client.get(self.base + '/acceptance-template' + self.query)
                          .json()['template'])
        body = {'expected_version': 0, 'criteria': [{'kind': 'owner_confirm', 'target': 'message'}]}
        response = self.client.put(self.base + '/acceptance-template' + self.query, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['template']['version'], 1)
        response = self.client.put(self.base + '/acceptance-template' + self.query, json=body)
        self.assertEqual(response.status_code, 409, response.text)

    def test_policy_template_and_usage_stay_in_their_brain(self):
        for suffix in ('quality-policy', 'acceptance-template', 'usage'):
            response = self.client.get(self.base + '/' + suffix + f'?brain={OTHER}')
            self.assertEqual(response.status_code, 404, response.text)
        response = self.client.put(self.base + '/quality-policy' + f'?brain={OTHER}',
                                   json={'enabled': True, 'limit_migration': 'observe'})
        self.assertEqual(response.status_code, 404, response.text)

    def test_usage_get_is_informational_and_rejects_foreign_task(self):
        response = self.client.get(self.base + '/usage' + self.query)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['usage']['cost_behavior'], 'observe_only')
        response = self.client.get(self.base + '/tasks/not-this-team/usage' + self.query)
        self.assertEqual(response.status_code, 404, response.text)

    def test_fallback_job_waits_for_version_bound_owner_decision(self):
        api = main._TEAM_API
        queued = []
        previous_spawn, previous_engine = api.deps.spawn, api.deps.engine_for
        api.deps.spawn = queued.append
        def engine(team, key, role):
            async def run(request):
                return {'text': 'A completed draft for owner review.', 'tokens_in': 2, 'tokens_out': 3}
            return 'fake', 'fake-model', run
        api.deps.engine_for = engine
        try:
            response = self.client.put(self.base + '/quality-policy' + self.query,
                                       json={'enabled': True, 'limit_migration': 'observe', 'reference_usd': 0})
            self.assertEqual(response.status_code, 200, response.text)
            maker = next(m['agent_key'] for m in self.team['members'] if m['slug'] == 'maker')
            response = self.client.post(self.base + '/messages' + self.query, json={
                'text': 'Prepare a draft.', 'as_job': True, 'mentions': [maker], 'client_msg_id': 'quality-fallback'})
            self.assertEqual(response.status_code, 200, response.text)
            task_id = response.json()['task_id']
            async def drain():
                await api.runner().dispatch_once(wait=True)
                while queued:
                    item = queued.pop(0)
                    if asyncio.iscoroutine(item):
                        await item
                await api.runner().wait_jobs()
            asyncio.run(drain())
            endpoint = self.base + f'/tasks/{task_id}/acceptance'
            a = self.client.get(endpoint + self.query).json()['acceptance']
            self.assertEqual(a['status'], 'review', a)
            self.assertNotEqual(api.deps.task_store().get_task(task_id)['status'], 'done')
            response = self.client.post(endpoint + '/decide' + self.query,
                                        json={'decision': 'accept', 'revision': a['revision'], 'round': a['rounds']})
            self.assertEqual(response.status_code, 409, response.text)
            payload = {'decision': 'accept', 'revision': a['revision'], 'round': a['rounds'],
                       'contract_version': a['contract_version'], 'output_manifest_hash': a['output_manifest_hash'],
                       'decision_key': 'owner-click-one'}
            response = self.client.post(endpoint + '/decide' + self.query, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            response = self.client.post(endpoint + '/decide' + self.query, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(api.deps.task_store().get_task(task_id)['status'], 'done')
        finally:
            for item in queued:
                if hasattr(item, 'close'):
                    item.close()
            api.deps.spawn, api.deps.engine_for = previous_spawn, previous_engine

    def test_legacy_http_decision_may_omit_quality_snapshot(self):
        runner = main._TEAM_API.runner()
        previous_engine = runner.engine_for
        def engine(team, key, role):
            async def run(request):
                return {'text': 'Legacy draft', 'tokens_in': 1, 'tokens_out': 1}
            return 'fake', 'fake', run
        runner.engine_for = engine
        try:
            maker = next(m['agent_key'] for m in self.team['members'] if m['slug'] == 'maker')
            task_id = runner.submit(self.team['team_id'], 'Prepare legacy draft', as_job=True,
                mentions=[maker], client_msg_id='legacy-draft')['task_id']
            runner.set_acceptance(self.team['team_id'], task_id, [{'kind': 'owner_confirm', 'target': 'message'}])
            async def drain():
                await runner.dispatch_once(wait=True)
                await runner.wait_jobs()
            asyncio.run(drain())
            endpoint = self.base + f'/tasks/{task_id}/acceptance'
            acc = self.client.get(endpoint + self.query).json()['acceptance']
            self.assertEqual(acc['status'], 'review', acc)
            response = self.client.post(endpoint + '/decide' + self.query, json={
                'decision': 'accept', 'revision': acc['revision'], 'round': acc['rounds']})
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            runner.engine_for = previous_engine


if __name__ == '__main__':
    unittest.main()
