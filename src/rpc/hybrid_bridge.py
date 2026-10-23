"""Fast isolated RPC memory plus GDB solely for safe hook removal."""
from src.rpc.azahar_bridge import AzaharBridge
from src.rpc.gdb_bridge import GDBBridge


class HybridBridge(AzaharBridge):
    fast_memory=True

    def __init__(self,rpc_port,gdb_port,title_id,process_name):
        super().__init__(port=rpc_port,timeout=.25,retries=1)
        self.debugger=GDBBridge(gdb_port,title_id,process_name,timeout=2)
        self.expected_title_id=title_id

    def auto_attach_albw(self,force=False):
        attached=super().auto_attach_albw(force)
        if attached and self.attached_title_id!=self.expected_title_id:
            self.detach()
            self.detection_status='other_game'
            return False
        return attached

    def _exchange(self,command,reply=True):
        if self.debugger.attached_pid is None:
            if not self.debugger.auto_attach_albw(force=True):
                return None
        if self.debugger.attached_pid!=self.attached_pid:
            return None
        return self.debugger._exchange(command,reply)

    def close(self):
        self.debugger.close()
        super().close()
