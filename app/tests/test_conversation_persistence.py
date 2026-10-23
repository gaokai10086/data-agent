import tempfile
from pathlib import Path
from unittest import TestCase

from app.service.conversation_repository import ConversationRepository


class ConversationPersistenceTest(TestCase):
    def test_create_messages_list_delete_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "app.sqlite3")
            first = ConversationRepository(path)
            first.initialize()
            first.create_conversation("session-1", "销售分析")
            first.add_message("session-1", request_id="req-1", role="user", status="completed", content="查询销售额")
            first.add_message("session-1", request_id="req-1", role="assistant", agent_type="data_agent", status="completed", content="", structured_result={"type": "bi_report", "rows": 1})
            # 重复消费同一个 request_id 不产生第二条同角色消息。
            first.add_message("session-1", request_id="req-1", role="assistant", agent_type="data_agent", status="completed", structured_result={"rows": 2})

            restarted = ConversationRepository(path)
            restarted.initialize()
            self.assertEqual(len(restarted.list_conversations()), 1)
            messages = restarted.list_messages("session-1")
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[1]["structured_result"], {"type": "bi_report", "rows": 1})
            self.assertTrue(restarted.delete_conversation("session-1"))
            self.assertEqual(restarted.list_conversations(), [])
