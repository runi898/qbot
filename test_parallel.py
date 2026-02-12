import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

# Define a mock module class
class MockModule:
    def __init__(self, name, priority, delay=1):
        self.name = name
        self.priority = priority
        self.delay = delay
        self.enabled = True
        self.start_time = 0
        self.end_time = 0

    async def can_handle(self, message, context):
        return True

    async def handle(self, message, context):
        self.start_time = time.time()
        print(f"[{self.name}] Started at {self.start_time}")
        await asyncio.sleep(self.delay)
        self.end_time = time.time()
        print(f"[{self.name}] Ended at {self.end_time}")
        return None # Return None found to let others continue, or object to stop

    async def on_load(self, config):
        pass

# Test parallel execution
async def test_parallel_execution():
    from core.module_loader import ModuleLoader
    
    loader = ModuleLoader()
    # Manually inject modules
    m1 = MockModule("Module A", 15, delay=1.0)
    m2 = MockModule("Module B", 15, delay=1.0)
    m3 = MockModule("Module C", 15, delay=1.0)
    
    loader.modules = [m1, m2, m3]
    
    # Correctly mock event_bus with async methods
    loader.event_bus = MagicMock()
    loader.event_bus.emit = AsyncMock() # Use AsyncMock for awaitable methods
    
    print("Starting process_message...")
    start_total = time.time()
    
    # Context mock
    context = MagicMock()
    
    await loader.process_message("test", context)
    
    end_total = time.time()
    duration = end_total - start_total
    
    print(f"Total duration: {duration:.2f}s")
    
    # Verification
    # If sequential: 1+1+1 = 3s
    # If parallel: max(1,1,1) = 1s (plus overhead)
    if duration < 1.5 and duration > 0.9:
        print("✅ SUCCESS: Modules executed in parallel!")
    else:
        print(f"❌ FAILURE: Modules executed in {duration:.2f}s (Expected ~1.0s)")

if __name__ == "__main__":
    asyncio.run(test_parallel_execution())
