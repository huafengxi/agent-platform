"""Local exclusion survives malformed/replaced synced heartbeat documents."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
CODE = """
import sys, time
sys.path.insert(0, sys.argv[1])
import runner
handle, path = runner.acquire_single_instance_lock(sys.argv[2], 'test')
print('acquired', flush=True)
time.sleep(60)
"""


class LockTests(unittest.TestCase):
    def test_guard_is_independent_of_heartbeat_and_released_on_death(self):
        with tempfile.TemporaryDirectory() as root:
            args = [sys.executable, '-c', CODE, str(HERE), root]
            heartbeat = Path(root, 'agents/run/agentd.test.lock')
            first = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(first.stdout.readline().strip(), 'acquired')
                for body in ('{partial', '[]', json.dumps({'pid': 0})):
                    replacement = heartbeat.with_suffix('.tmp')
                    replacement.write_text(body)
                    os.replace(replacement, heartbeat)
                    second = subprocess.run(args, capture_output=True, text=True, timeout=5)
                    self.assertEqual(second.returncode, 0)
                    self.assertEqual(second.stdout, '')
                    self.assertIn('已在运行', second.stderr)
                    self.assertIsNone(first.poll())
            finally:
                first.kill()
                first.communicate(timeout=5)
            successor = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(successor.stdout.readline().strip(), 'acquired')
                self.assertEqual(json.loads(heartbeat.read_text())['pid'], successor.pid)
            finally:
                successor.terminate()
                successor.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
