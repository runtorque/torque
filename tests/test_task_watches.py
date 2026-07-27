import asyncio
import tempfile
import unittest
from pathlib import Path
try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
install_aiohttp_stub()
from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState
from torque.server import _handle_user_agent_message_command

class TaskWatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=TorqueDB(Path(self.tmp.name)/'torque.db'); self.db.init(); self.addCleanup(self.db.close)
        self.state=MatrixState(self.db); self.state.groups['g']=[]
        self.agent=AgentCell(id='agent', name='Agent', group='g', cell_type='agent', kind='worker', session_id='s')
        self.other=AgentCell(id='other', name='Other', group='other', cell_type='agent', kind='worker')
        self.state.agents[self.agent.id]=self.agent; self.state.agents[self.other.id]=self.other
        self.state.groups['g']=['agent']; self.state.groups['other']=['other']
        self.one=self.state.board_add_task('One', 'g', lane='To Do', id='G:1')
        self.two=self.state.board_add_task('Two', 'g', lane='To Do', id='G:2')
        self.hidden=self.state.board_add_task('Hidden', 'other', lane='To Do', id='O:1')
    async def _command(self, text, key='k'):
        sent=[]
        async def prompt(*args, **kwargs): sent.append(args)
        result=await _handle_user_agent_message_command({'agent_id':'agent','message':text,'idempotency_key':key}, self.state, prompt)
        self.assertEqual(sent, [])
        return result
    async def test_create_incremental_fire_cancel_and_no_provider_prompt(self):
        created=await self._command('/watch G:1 G:2', 'create')
        self.assertEqual(created['type'],'ok'); watch=self.db.list_task_watches(requester_agent_id='agent',status='active')[0]
        self.state.board_move_task('G:1','Done'); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'active')
        listed=await self._command('/watches','list'); self.assertIn('1/2 Done',self.db.load_direct_message(listed['message_id'])['message'])
        cancelled=await self._command('/unwatch '+watch['id'],'cancel'); self.assertIn('Cancelled',self.db.load_direct_message(cancelled['message_id'])['message'])
        self.state.board_move_task('G:2','Done'); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'cancelled')
    async def test_immediate_once_scope_and_bad_forms_are_local(self):
        self.state.board_move_task('G:1','Done')
        result=await self._command('/watch G:1','immediate')
        self.assertEqual(self.db.list_task_watches(requester_agent_id='agent')[0]['status'],'fired')
        self.assertEqual(len(self.db.list_operator_notices()),1)
        again=await self._command('/watch G:1','immediate')
        self.assertTrue(again['deduped']); self.assertEqual(len(self.db.list_operator_notices()),1)
        for i,text in enumerate(('/watch','/watch G:1 G:1','/watch O:1','/WATCH G:1','/unwatch','/watches x')):
            response=await self._command(text,'bad'+str(i)); self.assertEqual(response['type'],'ok')
        sent=[]
        async def prompt(*args, **kwargs): sent.append(args)
        normal=await _handle_user_agent_message_command({'agent_id':'agent','message':'/watchdog','idempotency_key':'normal'}, self.state, prompt)
        self.assertTrue(sent)
        self.assertEqual(self.db.load_direct_message(normal['message_id'])['sender_kind'],'user')
    async def test_expiry_delete_and_restart_reconcile(self):
        watch=self.state.create_task_watch(target=self.agent,task_ids=['G:1'],now=1)
        self.state.reconcile_task_watches(now=31*24*60*60+2); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'cancelled')
        watch=self.state.create_task_watch(target=self.agent,task_ids=['G:2'])
        self.state.board_remove_task('G:2'); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'cancelled')
