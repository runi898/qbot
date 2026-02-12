import unittest
import re
from unittest.mock import MagicMock, patch

# Mock database module before importing SubscriptionManager
import sys
from types import ModuleType

# Mock the database module structure
mock_db_module = ModuleType('modules.news_collector.database')
mock_news_db = MagicMock()
mock_db_module.news_db = mock_news_db
sys.modules['modules.news_collector.database'] = mock_db_module

# Now import the class to test
# We need to bypass the 'news_db' import in the module file
with patch.dict('sys.modules', {'modules.news_collector.database': mock_db_module}):
    # Initializing _instance to None to ensure fresh start for singleton
    from modules.news_subscription.module import SubscriptionManager
    SubscriptionManager._instance = None

class TestSubscriptionManager(unittest.TestCase):
    def setUp(self):
        # Reset singleton
        SubscriptionManager._instance = None
        self.manager = SubscriptionManager()
        # Mock db behavior
        mock_news_db.get_all_subscriptions.return_value = []
        self.manager.initialize()

    def test_smart_boundary_matching(self):
        """Test the smart boundary logic for numbers"""
        user_id = 12345
        
        # Test Case 1: "0元"
        self.manager._add_to_cache(user_id, "0元")
        
        # Should match
        self.assertTrue(self.manager.get_matches("抢购0元购"))
        self.assertTrue(self.manager.get_matches("价格:0元!"))
        
        # Should NOT match
        self.assertFalse(self.manager.get_matches("现价60元"))  # 6 prefix
        self.assertFalse(self.manager.get_matches("0.6元"))   # .6 suffix
        self.assertFalse(self.manager.get_matches("10元"))    # 1 prefix
        
    def test_regex_matching(self):
        """Test explicit regex support"""
        user_id = 67890
        # Subscribe to "starts with 手机"
        self.manager._add_to_cache(user_id, "re:^手机")
        
        self.assertTrue(self.manager.get_matches("手机壳"))
        self.assertFalse(self.manager.get_matches("苹果手机"))
        
    def test_normal_text_matching(self):
        """Test normal text inclusion"""
        user_id = 11111
        self.manager._add_to_cache(user_id, "抽纸")
        
        self.assertTrue(self.manager.get_matches("维达抽纸"))
        self.assertFalse(self.manager.get_matches("卫生纸"))

    def test_pause_logic(self):
        """Test pause functionality"""
        user_id = 22222
        self.manager._add_to_cache(user_id, "抽纸")
        
        # Verify match before pause
        self.assertTrue(self.manager.get_matches("抽纸"))
        
        # Verify no match after pause
        self.manager.user_paused.add(user_id)
        self.assertFalse(self.manager.get_matches("抽纸"))

if __name__ == '__main__':
    unittest.main()
