"""Hot reload changes future schedules, never the deadline of a live action."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("heartbeatd", Path(__file__).with_name("heartbeatd.py"))
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)


class ReloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runner = hb.Runner(state_path=Path(tmp.name) / "state.json")
        self.entry = dict(timer="t", name="a", enabled=True,
                          schedule={"interval": {"seconds": 60}})

    def refresh(self, entry):
        with patch.object(hb, "load_registry", return_value=({}, [entry])):
            self.runner.refresh()

    def test_only_schedule_and_enabled_edits_invalidate_next_fire(self):
        self.refresh(self.entry)
        self.runner.nexts["t"] = 1000
        self.refresh(dict(self.entry, cmd=["new-command"]))
        self.assertEqual(self.runner.nexts["t"], 1000)
        self.refresh(dict(self.entry, schedule={"interval": {"seconds": 10}}))
        self.assertIsNone(self.runner.nexts["t"])
        self.runner.nexts["t"] = 2000
        self.refresh(dict(self.entry, enabled=False))
        self.assertIsNone(self.runner.nexts["t"])

    def test_removed_timer_retains_running_timeout(self):
        process = Mock(pid=12345)
        process.poll.side_effect = [None, -15]
        self.runner.children["t"] = (process, 10, 5)
        self.runner.face = []
        with patch.object(hb.time, "monotonic", return_value=16), \
             patch.object(hb, "kill_tree") as kill:
            self.runner.reap()
        kill.assert_called_once_with(process)
        self.assertEqual(self.runner.state["t"]["last_exit"], -15)
        self.assertEqual(self.runner.state["t"]["last_duration"], 6)

    def test_term_exiting_leader_does_not_leave_ignoring_child(self):
        with tempfile.TemporaryDirectory() as root:
            ready = Path(root, "ready")
            escaped = Path(root, "escaped")
            child = ("import signal,time,pathlib; "
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                     "pathlib.Path(%r).touch(); time.sleep(.7); "
                     "pathlib.Path(%r).touch()") % (str(ready), str(escaped))
            parent = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(30)")
            process = subprocess.Popen([sys.executable, "-c", parent, child],
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                hb.kill_tree(process, grace=.1)
                time.sleep(.8)
                self.assertFalse(escaped.exists(), "descendant survived the group leader")
            finally:
                hb._signal_group(process.pid, hb.signal.SIGKILL)
                process.wait(timeout=5)

    def test_once_sigterm_interrupts_wait_and_cleans_up_action(self):
        import json
        import os
        import platform
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            registry = root / "heartbeats"
            (registry / "profiles").mkdir(parents=True)
            (registry / "timers").mkdir()
            (registry / "profiles/a.json").write_text(json.dumps({
                "cmd": [sys.executable, "-c", "import time; time.sleep(30)"]}))
            (registry / "timers/test.t.json").write_text(json.dumps({
                "hostname": platform.node(), "profile": "a",
                "schedule": {"interval": {"seconds": 1}}}))
            state = root / "state.json"
            state.write_text(json.dumps({"t": {"last_fire": time.time() - 60}}))
            env = dict(os.environ, HEARTBEATD_ROOT=str(root),
                       HEARTBEATD_STATE=str(state), HEARTBEATD_HOST_ID=str(root / "host-id"))
            process = subprocess.Popen([sys.executable, str(Path(hb.__file__)), "run", "--once"],
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if json.loads(state.read_text()).get("t", {}).get("fires") == 1:
                        break
                    time.sleep(.02)
                else:
                    self.fail("action was not started")
                process.terminate()
                output = process.communicate(timeout=5)[0]
                self.assertEqual(process.returncode, 0, output)
                self.assertIn("stopped", output)
                self.assertLess(json.loads(state.read_text())["t"]["last_exit"], 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()

    def test_manual_fire_interrupt_runs_shutdown(self):
        runner = Mock()
        runner.fire.side_effect = KeyboardInterrupt
        with patch.object(hb, "load_registry", return_value=({}, [self.entry])), \
             patch.object(hb, "Runner", return_value=runner):
            with self.assertRaises(KeyboardInterrupt):
                hb.cmd_fire(["t"])
        runner.shutdown.assert_called_once_with()



if __name__ == "__main__":
    unittest.main()
